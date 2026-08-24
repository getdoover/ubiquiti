"""Shared value types for the Ubiquiti apps."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class Platform(str, Enum):
    """airOS hardware platform code, as reported in the firmware version string.

    The platform — not the model name — is what determines which config keys a
    device understands, and firmware images are not interchangeable between
    platforms. A "Bullet M2HP" ships as either XM or XW depending on hardware
    revision, so templates must be keyed on this rather than on the model.
    """

    # airMAX M generation -> airOS 6 (older stock on 5.6.x)
    XM = "XM"
    XW = "XW"
    TI = "TI"
    XN = "XN"
    # airMAX AC generation -> airOS 8
    XC = "XC"
    WA = "WA"

    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | None) -> "Platform":
        if not value:
            return cls.UNKNOWN
        try:
            return cls(value.strip().upper())
        except ValueError:
            return cls.UNKNOWN

    @property
    def generation(self) -> str:
        """``"airos6"`` or ``"airos8"`` — the config key namespace to expect."""
        if self in (Platform.XC, Platform.WA):
            return "airos8"
        if self is Platform.UNKNOWN:
            return "unknown"
        return "airos6"


# Firmware strings look like:
#   XM.ar7240.v6.3.11.34009.210325.1502    (airOS 6, Bullet M)
#   2WA.ar934x.v8.7.11.46972.220614.0419   (airOS 8, Bullet AC IP67)
#
# Note the leading digit on the second one — observed on real Bullet AC IP67
# hardware. It is a board-revision prefix on the platform token, not part of the
# platform code, and treating it as part of the code made every AC radio parse as
# an unknown platform, which surfaces in the UI as an unknown radio.
_FIRMWARE_RE = re.compile(
    r"^(?P<prefix>\d*)(?P<platform>[A-Za-z]{2,})\.(?P<chipset>[A-Za-z0-9_-]+)\.v(?P<version>[0-9][0-9.]*)"
)


def parse_firmware(firmware: str | None) -> tuple[Platform, str | None, str | None]:
    """Split a firmware string into ``(platform, chipset, version)``.

    Returns ``(Platform.UNKNOWN, None, None)`` rather than raising, so an
    unrecognised or truncated string never takes the provisioning loop down.
    """
    if not firmware:
        return Platform.UNKNOWN, None, None
    match = _FIRMWARE_RE.match(firmware.strip())
    if not match:
        return Platform.UNKNOWN, None, None
    return (
        Platform.parse(match.group("platform")),
        match.group("chipset"),
        match.group("version"),
    )


MAC_HEX_DIGITS = 12


def normalise_mac(mac: str) -> str:
    """Canonicalise a MAC to lowercase colon-separated form.

    Accepts the shapes people actually paste in: ``04:18:D6:AA:BB:CC``,
    ``04-18-d6-aa-bb-cc``, ``0418d6aabbcc``.

    **Over-long input keeps the last 12 hex digits.** Ubiquiti prints the MAC on
    the device label prefixed by a batch code — a real Bullet AC IP67 shipped as
    ``2450BJ28704EE29BCB``, whose trailing 12 hex digits are the MAC
    (``28:70:4e:e2:9b:cb``, confirmed against the radio). Operators reasonably
    copy the whole string, so take the part that is a MAC rather than rejecting it.

    Non-hex characters are stripped first, which is safe because a MAC is hex by
    definition — so a separator anywhere, or the ``J`` in that batch code, cannot
    be part of the address. Anything shorter than 12 hex digits is still an error:
    there is nothing to recover.
    """
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if len(cleaned) < MAC_HEX_DIGITS:
        raise ValueError(f"not a MAC address: {mac!r}")
    if len(cleaned) > MAC_HEX_DIGITS:
        cleaned = cleaned[-MAC_HEX_DIGITS:]
        # Logged, never silent: if a typo made the input over-long, the truncation
        # targets a different radio and the operator needs to be able to see that.
        log.info(
            "MAC %r is longer than an address; using its last 12 hex digits (%s)",
            mac,
            cleaned,
        )
    return ":".join(cleaned[i : i + 2] for i in range(0, MAC_HEX_DIGITS, 2))


@dataclass
class DiscoveredDevice:
    """One device that answered a UBNT discovery probe.

    ``raw_tlvs`` is kept deliberately: the TLV type table is only partly
    documented, so the bench CLI dumps it to let us confirm field meanings
    against real hardware instead of trusting the table.
    """

    mac: str
    ip: str | None = None
    firmware: str | None = None
    hostname: str | None = None
    product: str | None = None
    model: str | None = None
    essid: str | None = None
    uptime: int | None = None
    source: str = "ubnt-discovery"
    #: Every address the device advertised. Real radios advertise several — the
    #: LAN address, a 169.254/16 link-local, and a secondary bridge on a
    #: different MAC — so this is kept for diagnostics while :attr:`ip` holds the
    #: one we should actually talk to.
    advertised_ips: list[str] = field(default_factory=list)
    raw_tlvs: dict[int, list[bytes]] = field(default_factory=dict)

    @property
    def platform(self) -> Platform:
        return parse_firmware(self.firmware)[0]

    @property
    def version(self) -> str | None:
        return parse_firmware(self.firmware)[2]

    @property
    def generation(self) -> str:
        return self.platform.generation

    def describe(self) -> str:
        bits = [self.mac, self.ip or "no-ip"]
        if self.model or self.product:
            bits.append(self.model or self.product)
        if self.platform is not Platform.UNKNOWN:
            bits.append(f"{self.platform.value}/{self.version or '?'}")
        if self.hostname:
            bits.append(f"({self.hostname})")
        return " ".join(str(b) for b in bits)
