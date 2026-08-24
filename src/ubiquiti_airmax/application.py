"""Ubiquiti AirMax app — live telemetry and autonomous config for one radio.

Each pass sweeps for the configured MAC, reads its telemetry, and reconciles its
config only if it has drifted from the overrides. There are no provisioning
controls in the UI: config is applied once, when required, and the app then
idles. See :mod:`ubiquiti_airmax.provisioner`.

Design notes worth knowing before changing anything here:

* The pass runs directly in ``main_loop`` at ``poll_interval``. It does not block
  through a reboot — a push returns immediately and verification happens on a
  later pass — so the longest a pass takes is a discovery sweep plus a handful of
  SSH commands. It is wrapped in a timeout so a hung radio cannot stall the app.
* When the radio is unreachable, telemetry tags are left at their last value
  rather than zeroed. A signal of 0 dBm is an extremely *strong* reading, not a
  missing one, and zeroing would draw a cliff on every graph.
"""

import asyncio
import json
import logging
import time

from pydoover.docker import Application
from ubiquiti_common import discovery, netif
from ubiquiti_common.airos import Credential
from ubiquiti_common.models import mac_or_none
from ubiquiti_common.telemetry import Telemetry

from .app_config import AirMaxConfig
from .app_state import TargetState
from .app_tags import AirMaxTags
from .app_ui import AirMaxUI
from .provisioner import Override, Provisioner, Settings, TargetSpec

log = logging.getLogger(__name__)

#: Hard ceiling on one pass. Generous — a discovery sweep plus SSH to a slow
#: radio — but finite, so a hung session cannot stall the app indefinitely.
PASS_TIMEOUT = 90.0


class AirMaxApplication(Application):
    config_cls = AirMaxConfig
    tags_cls = AirMaxTags
    ui_cls = AirMaxUI

    config: AirMaxConfig
    tags: AirMaxTags

    async def setup(self):
        self.provisioner = Provisioner(self._settings())
        self.telemetry: Telemetry = Telemetry.offline("starting up")
        self.last_result: str | None = None

        interface = self.config.interface.value

        self._sync_config()
        log.info(
            "AirMax started on %s: target=%s, %d override(s), dry_run=%s",
            interface,
            self.config.mac.value,
            len(self.config.overrides.elements),
            self.config.dry_run.value,
        )

    # ---------------------------------------------------------------- config
    def _settings(self) -> Settings:
        credentials_cfg = list(self.config.credentials.elements)
        credentials = [
            Credential(c.username.value, c.password.value) for c in credentials_cfg
        ]
        enforce = next((c for c in credentials_cfg if c.apply_to_radio.value), None)
        return Settings(
            enforce_credential=(
                Credential(enforce.username.value, enforce.password.value)
                if enforce is not None
                else None
            ),
            interface=self.config.interface.value,
            dry_run=self.config.dry_run.value,
            deployment_delay=self.config.deployment_delay.value,
            max_attempts=self.config.max_attempts.value,
            retry_backoff=self.config.retry_backoff.value,
            failed_retry_after=self.config.failed_retry_after.value,
            reboot_wait=self.config.reboot_wait.value,
            discovery_timeout=self.config.discovery_timeout.value,
            ssh_port=self.config.ssh_port.value,
            manage_addresses=self.config.manage_addresses.value,
            credentials=credentials or [Credential("ubnt", "ubnt")],
        )

    def _sync_config(self) -> None:
        """Re-read config into the provisioner. Cheap, so done every pass.

        Doing it every pass is what makes an edited override set take effect
        without a restart — and what revives a parked radio, since a changed
        intent resets its attempt count.
        """
        self.provisioner.settings = self._settings()
        mac = (self.config.mac.value or "").strip()
        if not mac:
            self.provisioner.load(None)
            return
        self.provisioner.load(
            TargetSpec(
                mac=mac,
                overrides=self._layered_overrides(),
                expected_model=self.config.expected_model.value,
            )
        )

    def _layered_overrides(self) -> list[Override]:
        """Profile layer first, per-install layer second.

        Order is the precedence: :func:`build_overlay` fills a dict, so a key
        present in both layers takes the per-install value. That is the point of
        the split — a profile states the shared intent for a role, and one install
        can deviate without editing the profile.
        """
        profile = [
            Override(key=o.key.value, value=o.val.value)
            for o in self.config.profile_overrides.elements
        ]
        install = [
            Override(key=o.key.value, value=o.val.value)
            for o in self.config.overrides.elements
        ]
        shadowed = {o.key.strip() for o in profile} & {o.key.strip() for o in install}
        if shadowed:
            log.info(
                "per-install overrides take precedence for: %s",
                ", ".join(sorted(shadowed)),
            )
        return profile + install

    # ------------------------------------------------------------- main loop
    async def main_loop(self):
        # Track the configured interval live, so editing it takes effect without
        # a restart.
        self.loop_target_period = max(5, int(self.config.poll_interval.value))

        self._sync_config()
        try:
            await asyncio.wait_for(self._run_pass(), timeout=PASS_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("pass exceeded %.0fs and was abandoned", PASS_TIMEOUT)
            self.telemetry = Telemetry.offline("timed out talking to the radio")
        except Exception:
            log.exception("pass failed; continuing")
            self.telemetry = Telemetry.offline("pass failed — see logs")

        await self._publish()

    async def _run_pass(self) -> None:
        interface = self.config.interface.value
        timeout = float(self.config.discovery_timeout.value)
        try:
            broadcasts = await netif.broadcast_addresses(interface)
        except netif.NetifError as exc:
            log.debug("no broadcast addresses for %s (%s)", interface, exc)
            broadcasts = None

        devices = await discovery.discover(broadcast_addrs=broadcasts, timeout=timeout)
        if not self._found_target(devices):
            devices += await self._unicast_fallback(interface, timeout, devices)

        self.last_result = await self.provisioner.run_pass(devices)
        if self.provisioner.telemetry is not None:
            self.telemetry = self.provisioner.telemetry

    def _found_target(self, devices: list) -> bool:
        spec = self.provisioner.spec
        return bool(spec) and any(d.mac == spec.mac for d in devices)

    async def _unicast_fallback(self, interface: str, timeout: float, already: list):
        """Probe addresses directly when broadcast did not turn up the target.

        Broadcast is not always deliverable: a Doovit that bridges its LAN onto a
        wireless interface reaches its radios by unicast but never sees a broadcast
        reply, so the radio is reachable and yet invisible.

        Cheapest candidate first — the address we last saw the radio on, then the
        one the overrides assign it — before sweeping the interface's subnet. Most
        passes stop at the first candidate; the sweep is for a radio that has moved
        or has never been seen.
        """
        candidates: list[str] = []
        record = self.provisioner.record
        if record and record.ip:
            candidates.append(record.ip)
        spec = self.provisioner.spec
        if spec:
            for o in spec.overrides:
                if o.key.strip() == "netconf.3.ip" and o.value:
                    candidates.append(o.value.strip())
        seen = {d.ip for d in already if d.ip}
        candidates = [c for c in dict.fromkeys(candidates) if c not in seen]

        if candidates:
            found = await discovery.unicast_probe(candidates, timeout=timeout)
            if self._found_target(already + found):
                return found
            already = already + found

        try:
            addrs = await netif.interface_addresses(interface)
        except netif.NetifError as exc:
            log.debug("cannot enumerate %s for a sweep (%s)", interface, exc)
            return []
        hosts = discovery.hosts_of([a.network for a in addrs])
        skip = {d.ip for d in already if d.ip} | set(a.address for a in addrs)
        hosts = [h for h in hosts if h not in skip]
        if not hosts:
            return []
        log.info(
            "target %s not seen by broadcast; sweeping %d address(es) on %s",
            spec.mac if spec else "?",
            len(hosts),
            interface,
        )
        # A sweep needs longer than the broadcast listen window: many more
        # addresses, and replies arriving over a longer tail.
        return await discovery.unicast_probe(hosts, timeout=max(timeout, 6.0))

    # --------------------------------------------------------------- publish
    async def _publish(self) -> None:
        tel = self.telemetry
        record = self.provisioner.record
        state = record.state if record else TargetState.PENDING

        # Always published, so the UI can distinguish stale from current.
        await self.tags.online.set(bool(tel.online))
        await self.tags.reachable_ok.set(bool(tel.online))
        await self.tags.config_state.set(self._describe_state(state))
        await self.tags.config_message.set(
            (record.message if record else None) or self.last_result
        )
        await self.tags.config_attempts.set(record.attempts if record else 0)
        await self.tags.config_diff.set((record.last_diff if record else None) or None)
        # Positive logic: the warning is hidden while this is True.
        await self.tags.config_ok.set(not self.provisioner.needs_attention)
        if record and record.last_seen:
            await self.tags.last_seen.set(record.last_seen)
        if record and record.ip:
            await self.tags.ip_address.set(record.ip)

        # Topology identity is published whether or not the radio answered. A
        # radio that has gone dark is exactly what an operator most wants to see
        # on the network graph, and it can only be drawn in the right place if it
        # still reports who it is and what it hangs off. `spec.mac` is the
        # normalised configured MAC, so a node keeps its place even on the first
        # pass, before the radio has ever been reached.
        spec = self.provisioner.spec
        await self.tags.radio_mac.set(tel.device_mac or (spec.mac if spec else None))
        await self.tags.uplink_mac.set(mac_or_none(self.config.uplink_mac.value))

        if not tel.online:
            # Radio unreachable — leave the telemetry tags at their last value
            # rather than zeroing them, so the UI shows stale readings instead of
            # misleading clean ones.
            return

        await self.tags.signal.set(tel.signal_dbm)
        await self.tags.noise_floor.set(tel.noise_dbm)
        await self.tags.snr.set(tel.snr_db)
        await self.tags.ccq.set(tel.ccq_pct)
        await self.tags.quality.set(tel.quality_pct)
        await self.tags.capacity.set(tel.capacity_pct)

        await self.tags.tx_rate.set(tel.tx_rate_mbps)
        await self.tags.rx_rate.set(tel.rx_rate_mbps)
        await self.tags.tx_throughput.set(tel.tx_throughput_kbps)
        await self.tags.rx_throughput.set(tel.rx_throughput_kbps)

        await self.tags.model.set(tel.model or (record.model if record else None))
        await self.tags.platform.set(
            tel.platform or (record.platform if record else None)
        )
        await self.tags.firmware.set(
            tel.firmware or (record.firmware if record else None)
        )
        await self.tags.hostname.set(tel.hostname)
        await self.tags.essid.set(tel.essid)
        await self.tags.wireless_mode.set(tel.wireless_mode)
        await self.tags.frequency.set(tel.frequency_mhz)
        await self.tags.channel_width.set(tel.chanbw_mhz)

        # Hours reads better than seconds for a radio that stays up for weeks.
        await self.tags.uptime.set(
            tel.uptime_s / 3600.0 if tel.uptime_s is not None else None
        )
        # A reboot resets uptime, so this jumps — which is what makes it loggable.
        await self.tags.started_at_ms.set(
            int((time.time() - tel.uptime_s) * 1000)
            if tel.uptime_s is not None
            else None
        )

        await self.tags.station_count.set(tel.station_count)
        await self.tags.stations.set(
            "\n".join(s.describe() for s in tel.stations) or None
        )
        # Machine-readable twin, for the network overview's edge labels.
        await self.tags.stations_json.set(
            json.dumps([s.to_dict() for s in tel.stations]) if tel.stations else None
        )
        await self.tags.ap_mac.set(tel.ap_mac)
        await self.tags.distance.set(tel.distance_m)

    def _describe_state(self, state: TargetState) -> str:
        if self.config.dry_run.value and state is TargetState.WOULD_APPLY:
            return "dry run — changes pending"
        return {
            TargetState.PENDING: "waiting for radio",
            TargetState.UNREACHABLE: "unreachable",
            TargetState.CONVERGED: "configured",
            TargetState.DRIFTED: "changes pending",
            TargetState.APPLYING: "applying, rebooting",
            TargetState.FAILED: "failed — needs attention",
            TargetState.WOULD_APPLY: "changes pending",
        }.get(state, state.value)
