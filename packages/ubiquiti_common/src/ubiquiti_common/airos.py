"""SSH driver for airOS radios (airMAX M / airOS 6 and airMAX AC / airOS 8).

The apply sequence is identical across airOS 5, 6 and 8, which is why one driver
covers every Bullet::

    cat /tmp/running.cfg          # read live config
    cat > /tmp/system.cfg         # write desired config
    cfgmtd -f /tmp/system.cfg -w  # commit to flash
    reboot                        # apply

Two things bite here and are handled below:

* **Legacy crypto.** airOS 6 runs an old dropbear that only offers key exchange,
  cipher and host-key algorithms modern asyncssh disables by default. Without the
  explicit algorithm lists, connecting fails at kex with no useful error.
* **No SFTP.** BusyBox on these devices has no sftp-server, so the config is
  written by piping to ``cat`` over an exec channel rather than via SFTP.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import asyncssh

from . import cfg
from .models import Platform, parse_firmware
from .telemetry import (
    Telemetry,
    ThroughputTracker,
    parse_mca_dump,
    parse_mca_status,
    parse_proc_net_dev,
    parse_wstalist,
)

log = logging.getLogger(__name__)

RUNNING_CFG = "/tmp/running.cfg"
STAGED_CFG = "/tmp/system.cfg"

# Status sources, in preference order. ``mca-dump`` emits the same JSON document
# as /status.cgi and is richer; ``mca-status`` is the flat key=value fallback that
# exists on older firmware. Neither is guaranteed, so both are tried.
STATUS_JSON_CMD = "mca-dump"
STATUS_FLAT_CMD = "mca-status"
STATIONS_CMD = "wstalist"
COUNTERS_CMD = "cat /proc/net/dev"

# Permissive algorithm sets so one client can talk to both an airOS 6 dropbear and
# a current airOS 8 build. Weak algorithms are deliberate: these are field radios
# on a provisioning LAN, and the alternative is not connecting at all.
LEGACY_KEX_ALGS = (
    "curve25519-sha256",
    "ecdh-sha2-nistp256",
    "diffie-hellman-group-exchange-sha256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
)
LEGACY_ENCRYPTION_ALGS = (
    "aes128-ctr",
    "aes192-ctr",
    "aes256-ctr",
    "aes128-cbc",
    "aes256-cbc",
    "3des-cbc",
)
LEGACY_MAC_ALGS = ("hmac-sha2-256", "hmac-sha1", "hmac-sha1-96")
LEGACY_HOST_KEY_ALGS = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "rsa-sha2-256",
    "rsa-sha2-512",
    "ssh-rsa",
    "ssh-dss",
)


class AirOSError(RuntimeError):
    """A command on the radio failed, or the radio was unreachable."""


@dataclass
class Credential:
    username: str
    password: str

    def __repr__(self) -> str:  # keep passwords out of logs and tracebacks
        return f"Credential(username={self.username!r}, password='***')"


@dataclass
class DeviceIdentity:
    firmware: str | None
    platform: Platform
    version: str | None
    hostname: str | None


class AirOSClient:
    """A single SSH session to one radio.

    Use as an async context manager::

        async with AirOSClient("192.168.1.20", cred) as client:
            current = await client.read_config()
    """

    def __init__(
        self,
        host: str,
        credential: Credential,
        port: int = 22,
        connect_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.credential = credential
        self.connect_timeout = connect_timeout
        self._conn: asyncssh.SSHClientConnection | None = None

    async def __aenter__(self) -> "AirOSClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def connect(self) -> None:
        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.credential.username,
                    password=self.credential.password,
                    # Field radios are reinstalled and factory-reset routinely, so a
                    # pinned host key would break provisioning rather than secure it.
                    known_hosts=None,
                    kex_algs=LEGACY_KEX_ALGS,
                    encryption_algs=LEGACY_ENCRYPTION_ALGS,
                    mac_algs=LEGACY_MAC_ALGS,
                    server_host_key_algs=LEGACY_HOST_KEY_ALGS,
                ),
                timeout=self.connect_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise AirOSError(
                f"timed out connecting to {self.host}:{self.port}"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise AirOSError(f"could not connect to {self.host}: {exc}") from exc

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            with_wait = self._conn.wait_closed()
            self._conn = None
            try:
                await with_wait
            except (OSError, asyncssh.Error):
                pass

    @property
    def conn(self) -> asyncssh.SSHClientConnection:
        if self._conn is None:
            raise AirOSError("not connected — call connect() first")
        return self._conn

    async def run(
        self, command: str, *, input: str | None = None, check: bool = True
    ) -> str:
        """Run one command, returning stdout."""
        try:
            result = await self.conn.run(command, input=input, check=False)
        except (OSError, asyncssh.Error) as exc:
            raise AirOSError(f"{self.host}: running {command!r} failed: {exc}") from exc
        if check and result.exit_status not in (0, None):
            stderr = (result.stderr or "").strip()
            raise AirOSError(
                f"{self.host}: {command!r} exited {result.exit_status}: {stderr}"
            )
        return result.stdout or ""

    async def identify(self) -> DeviceIdentity:
        """Read firmware and hostname straight off the device.

        Discovery already reports these, but reading them over SSH confirms we are
        talking to the device we think we are before we write anything to it.
        """
        firmware = (await self.run("cat /etc/version", check=False)).strip() or None
        hostname = (await self.run("uname -n", check=False)).strip() or None
        platform, _chipset, version = parse_firmware(firmware)
        return DeviceIdentity(
            firmware=firmware, platform=platform, version=version, hostname=hostname
        )

    async def read_config(self) -> cfg.Config:
        """Parse the device's live configuration."""
        body = await self.run(f"cat {RUNNING_CFG}")
        if not body.strip():
            raise AirOSError(f"{self.host}: {RUNNING_CFG} was empty")
        return cfg.parse(body)

    async def write_config(self, config: cfg.Config) -> None:
        """Stage a full config to ``/tmp/system.cfg``, verifying it landed intact.

        The byte-count check matters: a truncated write followed by ``cfgmtd``
        commits a half config to flash, and the recovery for that is the reset
        button.
        """
        body = cfg.render(config)
        expected = len(body.encode())
        await self.run(f"cat > {STAGED_CFG}", input=body)
        written = (await self.run(f"wc -c < {STAGED_CFG}")).strip()
        try:
            actual = int(written.split()[0])
        except (ValueError, IndexError) as exc:
            raise AirOSError(
                f"{self.host}: could not verify staged config size ({written!r})"
            ) from exc
        if actual != expected:
            raise AirOSError(
                f"{self.host}: staged config truncated — wrote {expected} bytes, "
                f"device has {actual}. Not committing."
            )
        log.info("%s: staged %d bytes to %s", self.host, expected, STAGED_CFG)

    async def commit(self) -> None:
        """Commit the staged config to flash. Does not reboot."""
        await self.run(f"cfgmtd -f {STAGED_CFG} -w")
        log.info("%s: committed staged config to flash", self.host)

    async def reboot(self) -> None:
        """Reboot the radio.

        The connection dies mid-command, which is success, not failure.
        """
        try:
            await self.run("reboot", check=False)
        except AirOSError:
            pass  # expected: the radio drops the session as it goes down
        log.info("%s: reboot issued", self.host)

    async def read_telemetry(
        self,
        tracker: ThroughputTracker | None = None,
        now: float | None = None,
    ) -> Telemetry:
        """Read a live telemetry snapshot.

        Every command runs with ``check=False``: a radio missing ``mca-dump`` or
        answering ``wstalist`` with nothing (it is a station, not an AP) is normal,
        not an error. Only a total absence of status is treated as a failure.

        If ``tracker`` is supplied, interface byte counters are turned into a
        throughput rate. The tracker must be kept across calls — a rate needs two
        samples.
        """
        sampled_at = time.time() if now is None else now

        status: dict = {}
        raw_json = await self.run(STATUS_JSON_CMD, check=False)
        if raw_json.strip():
            status = parse_mca_dump(raw_json)
        if not status:
            raw_flat = await self.run(STATUS_FLAT_CMD, check=False)
            status = parse_mca_status(raw_flat)
        if not status:
            raise AirOSError(
                f"{self.host}: neither {STATUS_JSON_CMD!r} nor {STATUS_FLAT_CMD!r} "
                "returned usable status"
            )

        stations = parse_wstalist(await self.run(STATIONS_CMD, check=False))
        telemetry = Telemetry.from_status(status, stations, sampled_at=sampled_at)

        counters = parse_proc_net_dev(await self.run(COUNTERS_CMD, check=False))
        if tracker is not None and counters:
            rx_kbps, tx_kbps = tracker.update(counters, sampled_at)
            telemetry.rx_throughput_kbps = rx_kbps
            telemetry.tx_throughput_kbps = tx_kbps

        return telemetry


async def probe(
    host: str,
    credentials: list[Credential],
    port: int = 22,
    connect_timeout: float = 10.0,
) -> tuple[Credential, DeviceIdentity]:
    """Find which credential works for ``host`` and identify the device.

    Tries each credential in order. Radios move between factory and configured
    credentials as they are reset and reprovisioned, so both must be tried rather
    than assumed.
    """
    if not credentials:
        raise AirOSError("no credentials configured")
    last_error: Exception | None = None
    for credential in credentials:
        client = AirOSClient(
            host, credential, port=port, connect_timeout=connect_timeout
        )
        try:
            await client.connect()
        except AirOSError as exc:
            last_error = exc
            log.debug("%s: credential %s rejected: %s", host, credential.username, exc)
            continue
        try:
            return credential, await client.identify()
        finally:
            await client.close()
    raise AirOSError(f"{host}: no configured credential worked ({last_error})")
