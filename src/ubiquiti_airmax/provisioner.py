"""The reconcile pipeline for one radio: discover → render → diff → push → verify.

This is a reconciler, not a script. Each pass is cheap and idempotent: it looks
for the configured MAC on the wire, works out what it should look like, and
pushes only if reality and intent differ. Once converged it does nothing at all,
which is why provisioning needs no button — it happens once, when required.

Verification happens on a *later* pass rather than by blocking through the
reboot, so "the radio came back on a different address" falls out for free: every
pass re-discovers.

Two bounded-retry rules keep an unconvergeable config from rebooting a radio
forever, and between them replace the old Reset Failed button:

* ``max_attempts`` pushes, then the target is parked in ``FAILED``.
* A parked target is retried when the *intent* changes (the operator edited the
  overrides or variables) or after ``failed_retry_after`` elapses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field

from ubiquiti_common import cfg, netif
from ubiquiti_common.airos import AirOSClient, AirOSError, Credential, probe
from ubiquiti_common.models import DiscoveredDevice, normalise_mac
from ubiquiti_common.telemetry import Telemetry, ThroughputTracker

from .app_state import TargetRecord, TargetState

log = logging.getLogger(__name__)

# airOS keys are dotted and 1-indexed (``netconf.1.ip``). Whitespace or an ``=``
# would render an unparseable line, and a config file the radio half-reads is
# worse than one it rejects — so an invalid key is refused, never written.
_VALID_KEY = re.compile(r"^[A-Za-z0-9_.\-]+$")

#: Keys whose value airOS stores as a crypt hash, never as a passphrase.
_HASHED_KEY = re.compile(r"^users\.[0-9]+\.password$")
#: A crypt hash: ``$id$salt$digest``. airOS uses ``$1$`` (MD5-crypt), verified by
#: regenerating a radio's stored value with ``openssl passwd -1``.
_CRYPT_HASH = re.compile(r"^\$[0-9a-z]{1,2}\$[^$]+\$[^$]+$")


@dataclass
class Override:
    """One airOS key, and a Jinja2 expression for its value."""

    key: str
    value: str = ""


class OverlayError(ValueError):
    """The overrides could not be turned into a valid config overlay."""


def build_overlay(overrides: list[Override]) -> cfg.Config:
    """Turn a list of key overrides into a config overlay.

    Values are **literal**. One install manages one radio, so there is nothing to
    parameterise — the value written is the value typed.

    A value still containing ``{{ }}`` is refused rather than written. Nothing
    renders it any more, so it would otherwise be flashed verbatim: an override of
    ``netconf.3.ip={{ ip }}`` would cost the radio its address on next boot, and
    the config would never converge.
    """
    result: cfg.Config = {}
    for override in overrides:
        key = (override.key or "").strip()
        if not key:
            continue
        if not _VALID_KEY.match(key):
            raise OverlayError(f"invalid airOS config key {key!r}")
        value = override.value or ""
        if "{{" in value or "}}" in value:
            raise OverlayError(
                f"{key}: value {value!r} looks like a leftover template "
                "placeholder. Values are literal — write the actual value."
            )
        if _HASHED_KEY.match(key) and not _CRYPT_HASH.match(value):
            # airOS stores this field verbatim and compares crypt(entered) against
            # it. A passphrase written here can therefore never match, and the
            # radio is locked out until someone presses its reset button — which
            # for a mast-mounted unit means a site visit. Refuse rather than brick.
            raise OverlayError(
                f"{key}: value must be a crypt hash like '$1$salt$digest', not a "
                "passphrase. Generate one with: openssl passwd -1 '<password>'"
            )
        result[key] = value
    return result


def intent_fingerprint(overrides: list[Override]) -> str:
    """Stable hash of the desired config.

    Hashes the *inputs* rather than the rendered output so it can never raise —
    this runs on every pass and must not be able to take the loop down.
    """
    payload = json.dumps(
        {
            "overrides": sorted((o.key, o.value) for o in overrides),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


#: The airOS user slot the enforced credential is written to. airOS ships a single
#: admin account at index 1; index 2 exists but is disabled.
ADMIN_USER_INDEX = 1


@dataclass
class Settings:
    interface: str = "eth0"
    dry_run: bool = True
    deployment_delay: int = 30
    max_attempts: int = 3
    retry_backoff: int = 300
    failed_retry_after: int = 3600
    reboot_wait: int = 120
    discovery_timeout: float = 3.0
    ssh_port: int = 22
    manage_addresses: bool = True
    #: Credential to enforce on the radio, if the operator flagged one.
    enforce_credential: Credential | None = None
    credentials: list[Credential] = field(
        default_factory=lambda: [Credential("ubnt", "ubnt")]
    )


@dataclass
class TargetSpec:
    """The one radio this app install is responsible for."""

    mac: str
    overrides: list[Override] = field(default_factory=list)
    expected_model: str = ""


class Provisioner:
    """Owns the target record across passes, and runs one pass at a time."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.spec: TargetSpec | None = None
        self.record: TargetRecord | None = None
        self.telemetry: Telemetry | None = None
        self.throughput = ThroughputTracker()
        self.addresses = AddressHolder()

    # ------------------------------------------------------------------ config
    def load(self, spec: TargetSpec | None) -> None:
        """Apply a config snapshot, preserving the record for the same MAC."""
        if spec is None:
            self.spec, self.record = None, None
            return
        try:
            mac = normalise_mac(spec.mac)
        except ValueError:
            log.warning("target MAC %r is not a MAC address; nothing to do", spec.mac)
            self.spec, self.record = None, None
            return

        spec.mac = mac
        self.spec = spec
        if self.record is None or self.record.mac != mac:
            self.record = TargetRecord(mac=mac)
            self.throughput.reset()

        fingerprint = intent_fingerprint(spec.overrides)
        if self.record.intent and self.record.intent != fingerprint:
            # New intent from the operator: clear the attempt count so an edited
            # config gets a fresh set of tries even from a parked state.
            self.record.reset("config changed, retrying")
            self.record.intent_since = None
        self.record.intent = fingerprint
        if self.record.intent_since is None:
            self.record.intent_since = time.time()

    # ------------------------------------------------------------------ status
    @property
    def state(self) -> TargetState:
        return self.record.state if self.record else TargetState.PENDING

    @property
    def needs_attention(self) -> bool:
        return bool(self.record and self.record.needs_attention)

    def find(self, devices: list[DiscoveredDevice]) -> DiscoveredDevice | None:
        if self.spec is None:
            return None
        return next((d for d in devices if d.mac == self.spec.mac), None)

    # -------------------------------------------------------------------- pass
    async def run_pass(self, devices: list[DiscoveredDevice]) -> str | None:
        """Reconcile the target against one discovery snapshot, and poll telemetry."""
        if self.spec is None or self.record is None:
            return None
        record, spec = self.record, self.spec

        device = self.find(devices)
        if device is None:
            self.telemetry = Telemetry.offline("not seen on the wire")
            self.throughput.reset()
            if record.state is not TargetState.FAILED:
                record.transition(TargetState.PENDING, "not seen on the wire")
            return None

        try:
            return await self._reconcile(spec, record, device)
        except Exception as exc:  # a bad radio must never kill the loop
            log.exception("%s: unhandled error during reconcile", spec.mac)
            self.telemetry = Telemetry.offline(str(exc))
            record.transition(TargetState.UNREACHABLE, f"error: {exc}")
            return f"error: {exc}"

    async def _reconcile(
        self, spec: TargetSpec, record: TargetRecord, device: DiscoveredDevice
    ) -> str | None:
        settings = self.settings
        record.last_seen = time.time()
        record.ip = device.ip
        record.model = device.model or device.product
        record.platform = device.platform.value
        record.firmware = device.firmware

        # A parked target is retried only once the cooldown has elapsed. An
        # intent change clears it sooner, via load().
        if record.state is TargetState.FAILED:
            if settings.failed_retry_after <= 0:
                return None
            if record.backoff_remaining(settings.failed_retry_after) > 0:
                return None
            record.reset("failed cooldown elapsed, retrying")

        if not device.ip:
            self.telemetry = Telemetry.offline("discovered but reported no IP")
            record.transition(TargetState.UNREACHABLE, "discovered but reported no IP")
            return "no IP in discovery reply"

        # --- safety gates: refuse rather than guess --------------------------
        if spec.expected_model:
            observed = f"{device.model or ''} {device.product or ''}"
            if spec.expected_model.lower() not in observed.lower():
                record.transition(
                    TargetState.FAILED,
                    f"expected model {spec.expected_model!r} but found {observed.strip()!r}",
                )
                return "model guard failed"

        try:
            desired = build_overlay(spec.overrides)
        except OverlayError as exc:
            record.transition(TargetState.FAILED, f"invalid overrides: {exc}")
            return f"invalid overrides: {exc}"

        # --- talk to the radio ----------------------------------------------
        if settings.manage_addresses:
            try:
                await self.addresses.ensure(settings.interface, device.ip)
            except netif.NetifError as exc:
                log.debug("could not make %s reachable: %s", device.ip, exc)

        try:
            credential, identity = await probe(
                device.ip, settings.credentials, port=settings.ssh_port
            )
        except AirOSError as exc:
            self.telemetry = Telemetry.offline(str(exc))
            # A radio mid-reboot is expected to be unreachable. Holding the
            # APPLYING state through the reboot window is what lets the next
            # pass verify the push instead of treating it as fresh drift.
            if (
                record.state is TargetState.APPLYING
                and record.backoff_remaining(settings.reboot_wait) > 0
            ):
                record.note(f"rebooting, not yet back: {exc}")
                return None
            record.transition(TargetState.UNREACHABLE, f"ssh: {exc}")
            return str(exc)

        async with AirOSClient(device.ip, credential, port=settings.ssh_port) as client:
            # Telemetry first: it is the reason the UI exists, and it must be
            # published even when there is no config work to do.
            try:
                self.telemetry = await client.read_telemetry(tracker=self.throughput)
            except AirOSError as exc:
                log.warning("%s: telemetry read failed: %s", spec.mac, exc)
                self.telemetry = Telemetry.offline(str(exc))

            if not desired:
                record.transition(
                    TargetState.CONVERGED,
                    "no overrides configured, nothing to apply",
                )
                return None

            current = await client.read_config()
            changes = cfg.diff(current, desired)
            # probe() already told us which credential worked, so that *is* the
            # convergence check for the login — no hashing and no config diff
            # needed to decide. And because it only ever fires when some
            # credential authenticated, the app can never lock itself out.
            enforce = settings.enforce_credential
            credential_wrong = bool(enforce) and (
                (credential.username, credential.password)
                != (enforce.username, enforce.password)
            )
            record.last_diff = cfg.format_diff(changes) + (
                f"\n~ login: {credential.username} -> {enforce.username}"
                if credential_wrong
                else ""
            )

            # Verifying a push from an earlier pass.
            if record.state is TargetState.APPLYING:
                if record.backoff_remaining(settings.reboot_wait) > 0:
                    return None  # still booting, check again next pass
                if not changes:
                    record.transition(
                        TargetState.CONVERGED,
                        f"converged after {record.attempts} attempt(s) "
                        f"on {identity.firmware}",
                    )
                    return "converged"
                if record.attempts >= settings.max_attempts:
                    record.transition(
                        TargetState.FAILED,
                        f"still differs after {record.attempts} attempt(s) — parked. "
                        f"Unconverged keys: {', '.join(sorted(changes))}",
                    )
                    return "failed to converge, parked"
                record.transition(
                    TargetState.DRIFTED,
                    "did not converge, will retry after backoff",
                )

            if not changes and not credential_wrong:
                record.transition(TargetState.CONVERGED, "config matches")
                return None

            work = f"{len(changes)} key(s)" + (" + login" if credential_wrong else "")
            if settings.dry_run:
                record.transition(
                    TargetState.WOULD_APPLY, f"dry run — {work} would change"
                )
                return f"dry run, {work} would change"

            # Hold before the first write of a new intent, so a fleet-wide
            # deploy can reach every radio before any link drops. Checked
            # after dry run and before the attempt ceiling: waiting must not
            # consume an attempt. Telemetry and the diff keep publishing
            # throughout, which is the point — the operator watches the links
            # stay up while the config propagates.
            held = record.hold_remaining(settings.deployment_delay)
            if held > 0:
                record.transition(
                    TargetState.DRIFTED,
                    f"{work} to apply — holding {held:.0f}s for the deployment delay",
                )
                return f"holding {held:.0f}s before applying"

            if record.attempts >= settings.max_attempts:
                record.transition(
                    TargetState.FAILED,
                    f"attempt ceiling ({settings.max_attempts}) reached",
                )
                return "attempt ceiling reached"

            if record.backoff_remaining(settings.retry_backoff) > 0:
                return None  # backing off quietly

            # --- the irreversible part ----------------------------------
            record.record_attempt()

            if credential_wrong:
                # Deliberately here and nowhere earlier: `passwd` changes the
                # live login immediately, so running it on a pass that then
                # writes nothing (dry run, or holding for the delay) would leave
                # the radio's live password disagreeing with its flash config.
                #
                # The radio generates the hash; we only persist it. `/etc/passwd`
                # is tmpfs on a read-only squashfs root, so the hash must reach
                # `users.N.password` in flash or the reboot below erases it.
                digest = await client.set_password(
                    credential.username, enforce.password
                )
                desired[f"users.{ADMIN_USER_INDEX}.name"] = enforce.username
                desired[f"users.{ADMIN_USER_INDEX}.password"] = digest
                log.info(
                    "%s: enforcing login %s (was %s)",
                    spec.mac,
                    enforce.username,
                    credential.username,
                )
            log.info(
                "%s (%s @ %s): applying %d key(s), attempt %d/%d",
                spec.mac,
                record.model,
                device.ip,
                len(changes),
                record.attempts,
                settings.max_attempts,
            )
            await client.write_config(cfg.merge(current, desired))
            await client.commit()
            await client.reboot()
            self.throughput.reset()  # counters restart across a reboot
            record.transition(
                TargetState.APPLYING,
                f"pushed {len(changes)} key(s), rebooting (attempt {record.attempts})",
            )
            return f"pushed {len(changes)} key(s), rebooting"


class AddressHolder:
    """Keeps one provisioning address on the interface rather than thrashing it.

    A factory radio on 192.168.1.20 is unreachable from another subnet until we
    hold an address in its range. Adding and removing that address inside every
    pass would mean thousands of add/delete cycles a day once polling is running,
    so it is added once and kept while the radio stays off-subnet.
    """

    def __init__(self) -> None:
        self.interface: str | None = None
        self.cidr: str | None = None

    async def ensure(self, interface: str, target_ip: str) -> None:
        """Make ``target_ip`` routable, reusing an address we already hold."""
        if await netif.is_reachable(interface, target_ip):
            return  # already routable, possibly by an address we added earlier
        await self.release()
        cidr = netif.helper_address_for(target_ip)
        await netif.add_address(interface, cidr)
        self.interface, self.cidr = interface, cidr

    async def release(self) -> None:
        if self.interface and self.cidr:
            try:
                await netif.remove_address(self.interface, self.cidr)
            except netif.NetifError as exc:
                log.debug("could not remove %s: %s", self.cidr, exc)
        self.interface, self.cidr = None, None
