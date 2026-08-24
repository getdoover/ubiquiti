"""Live telemetry parsing for airOS radios.

airOS exposes status two ways, and which one a radio has depends on its
generation:

* ``mca-dump`` — a single JSON document (the same payload ``/status.cgi`` serves).
  Richer and properly structured. Preferred where present.
* ``mca-status`` — flat ``key=value`` lines. Older, present everywhere.

Both are parsed tolerantly into a flat mapping, then mapped onto
:class:`Telemetry` through alias lists, because the exact field names vary
between airOS 6 and 8 and are not documented. Every reading keeps its ``raw``
mapping so ``airos status --raw`` can confirm field meanings against real
hardware rather than trusting these aliases — the same approach the discovery
TLV table takes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .models import mac_or_none

log = logging.getLogger(__name__)

# Values arrive with units attached and inconsistent spacing: "-68 dBm",
# "5800MHz", "130 Mbps", "97". Pull the leading number out of any of them.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def as_number(value: Any) -> float | None:
    """Best-effort numeric read of an airOS status value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(str(value))
    return float(match.group()) if match else None


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# A fragment that starts a new pair: an identifier immediately followed by `=`.
# Used to decide whether a comma inside a line separates pairs or sits inside a
# value (an SSID may legitimately contain a comma).
_MCA_PAIR = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*=")


def parse_mca_status(text: str) -> dict[str, str]:
    """Parse ``mca-status`` output into a flat mapping.

    Two shapes appear in the same document, confirmed on a Bullet AC IP67
    (2WA.v8.7.11): most pairs are one per line, but the **header line packs
    several onto one line separated by commas**::

        deviceName=Bullet AC IP67,deviceId=28:70:4E:...,firmwareVersion=2WA...
        apMac=00:00:00:00:00:00
        wlanOpmode=sta-ptp-ac

    Splitting only on newlines made ``deviceName`` swallow the rest of the header
    — the hostname tag published as
    ``"Bullet AC IP67,deviceId=28:70:4E:E2:9B:CB,firmwareVersion=2W..."`` — and
    silently lost every other field on that line.

    Commas are only treated as separators when what follows looks like a new
    ``key=`` pair, so a value containing a comma survives intact. Lines without
    an ``=`` are ignored, so banner text or a stray prompt cannot break the read.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        # Rebuild pairs, re-joining fragments that were not a new key=value.
        pairs: list[str] = []
        for fragment in line.split(","):
            if pairs and not _MCA_PAIR.match(fragment):
                pairs[-1] += "," + fragment
            else:
                pairs.append(fragment)
        for pair in pairs:
            key, sep, value = pair.partition("=")
            key = key.strip()
            if sep and key:
                result[key] = value.strip()
    return result


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested JSON to dotted keys, leaving lists intact.

    Lists are kept whole rather than indexed because the one list we care about
    (connected stations) is consumed as a list.
    """
    flat: dict[str, Any] = {}
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                flat.update(flatten(value, path))
            else:
                flat[path] = value
    elif prefix:
        flat[prefix] = obj
    return flat


def parse_mca_dump(text: str) -> dict[str, Any]:
    """Parse the ``mca-dump`` / ``status.cgi`` JSON document into a flat mapping."""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return flatten(payload)


def parse_proc_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """Parse ``/proc/net/dev`` into ``{interface: (rx_bytes, tx_bytes)}``."""
    counters: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue  # skips the two header lines
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            counters[name.strip()] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return counters


def parse_wstalist(text: str) -> list[dict[str, Any]]:
    """Parse the ``wstalist`` JSON array of associated stations."""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(payload, Mapping):
        # airOS 8 wraps it; airOS 6 returns a bare array.
        for key in ("sta", "stations", "wireless.sta"):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, Mapping)]
        return []
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, Mapping)]
    return []


def pick(source: Mapping[str, Any], *aliases: str) -> Any:
    """First present, non-empty value among ``aliases``.

    Matching is case-insensitive on the last dotted segment as well as the full
    key, so ``wireless.signal`` is found by asking for ``signal``.
    """
    lowered = {k.lower(): v for k, v in source.items()}
    tails: dict[str, Any] = {}
    for key, value in source.items():
        tails.setdefault(key.rsplit(".", 1)[-1].lower(), value)
    for alias in aliases:
        needle = alias.lower()
        for table in (lowered, tails):
            if needle in table:
                value = table[needle]
                if value not in (None, "", []):
                    return value
    return None


@dataclass
class Station:
    """One associated station (AP mode)."""

    mac: str | None = None
    hostname: str | None = None
    signal_dbm: float | None = None
    noise_dbm: float | None = None
    ccq_pct: float | None = None
    tx_rate_mbps: float | None = None
    rx_rate_mbps: float | None = None
    uptime_s: float | None = None
    distance_m: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Station":
        flat = flatten(data)
        return cls(
            mac=mac_or_none(pick(flat, "mac", "hwaddr", "apmac")),
            hostname=as_text(pick(flat, "name", "hostname", "remote.hostname")),
            signal_dbm=as_number(pick(flat, "signal", "rssi_dbm")),
            noise_dbm=as_number(pick(flat, "noisefloor", "noise")),
            ccq_pct=_scale_ccq(as_number(pick(flat, "ccq", "airmax.quality"))),
            tx_rate_mbps=as_number(pick(flat, "tx", "txrate", "tx_rate", "txlatency")),
            rx_rate_mbps=as_number(pick(flat, "rx", "rxrate", "rx_rate")),
            uptime_s=as_number(pick(flat, "uptime", "assoctime")),
            distance_m=as_number(pick(flat, "distance")),
        )

    def to_dict(self) -> dict[str, Any]:
        """The machine-readable form, for the ``stations_json`` tag.

        Separate from :meth:`describe`, which is prose for a human reading the
        app's UI. The network-overview widget needs the MAC to join an edge and
        the per-station figures to label it, and cannot get either back out of a
        formatted string.
        """
        return {
            "mac": self.mac,
            "hostname": self.hostname,
            "signal_dbm": self.signal_dbm,
            "noise_dbm": self.noise_dbm,
            "ccq_pct": self.ccq_pct,
            "tx_rate_mbps": self.tx_rate_mbps,
            "rx_rate_mbps": self.rx_rate_mbps,
            "uptime_s": self.uptime_s,
            "distance_m": self.distance_m,
        }

    def describe(self) -> str:
        bits = [self.mac or "?"]
        if self.hostname:
            bits.append(f"({self.hostname})")
        if self.signal_dbm is not None:
            bits.append(f"{self.signal_dbm:.0f} dBm")
        if self.tx_rate_mbps is not None and self.rx_rate_mbps is not None:
            bits.append(f"{self.tx_rate_mbps:.0f}/{self.rx_rate_mbps:.0f} Mbps")
        return " ".join(bits)


def _scale_ccq(value: float | None) -> float | None:
    """Normalise CCQ to a percentage.

    airOS reports CCQ either as 0-100 or as tenths (0-1000) depending on
    firmware. Anything over 100 is assumed to be tenths.
    """
    if value is None:
        return None
    return value / 10.0 if value > 100 else value


@dataclass
class Telemetry:
    """One snapshot of a radio's live state."""

    online: bool = False
    error: str | None = None
    sampled_at: float = 0.0

    # link quality
    signal_dbm: float | None = None
    noise_dbm: float | None = None
    ccq_pct: float | None = None
    quality_pct: float | None = None
    capacity_pct: float | None = None

    # rates and throughput
    tx_rate_mbps: float | None = None
    rx_rate_mbps: float | None = None
    tx_throughput_kbps: float | None = None
    rx_throughput_kbps: float | None = None

    # identity
    # The radio's own MAC, as it reports it. Equal to its ath0 (wireless) address
    # and to the MAC discovery reports, confirmed on a Bullet AC IP67 — which is
    # what lets a station's ``ap_mac`` join straight onto its AP's ``device_mac``.
    device_mac: str | None = None
    model: str | None = None
    platform: str | None = None
    firmware: str | None = None
    hostname: str | None = None
    uptime_s: float | None = None
    frequency_mhz: float | None = None
    chanbw_mhz: float | None = None
    essid: str | None = None
    wireless_mode: str | None = None

    # peers
    ap_mac: str | None = None
    distance_m: float | None = None
    station_count: int = 0
    stations: list[Station] = field(default_factory=list)

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def snr_db(self) -> float | None:
        """Signal-to-noise ratio, the single most useful link-health number.

        airOS does not report it directly on every firmware, so derive it.
        """
        if self.signal_dbm is None or self.noise_dbm is None:
            return None
        return self.signal_dbm - self.noise_dbm

    @classmethod
    def offline(cls, error: str) -> "Telemetry":
        return cls(online=False, error=error, sampled_at=time.time())

    @classmethod
    def from_status(
        cls,
        status: Mapping[str, Any],
        stations: Iterable[Mapping[str, Any]] = (),
        sampled_at: float | None = None,
    ) -> "Telemetry":
        """Build a reading from a flat status mapping plus an optional station list."""
        station_list = [Station.from_mapping(s) for s in stations]
        # Some firmwares carry the station list inside the status document.
        if not station_list:
            embedded = pick(status, "sta", "stations")
            if isinstance(embedded, list):
                station_list = [
                    Station.from_mapping(s) for s in embedded if isinstance(s, Mapping)
                ]

        return cls(
            online=True,
            sampled_at=sampled_at if sampled_at is not None else time.time(),
            signal_dbm=as_number(pick(status, "signal", "signal_dbm", "rssi_dbm")),
            noise_dbm=as_number(pick(status, "noisefloor", "noise_floor", "noise")),
            ccq_pct=_scale_ccq(as_number(pick(status, "ccq", "wlanPollingQuality"))),
            quality_pct=as_number(
                pick(status, "airmax.quality", "quality", "wlanPollingQuality")
            ),
            capacity_pct=as_number(
                pick(status, "airmax.capacity", "capacity", "wlanPollingCapacity")
            ),
            # Deliberately no bare "tx"/"rx" alias here: pick() also matches the
            # last dotted segment, so "tx" would happily bind to something like
            # interfaces.0.stats.tx (a byte counter) on a JSON status document.
            # In a wstalist station entry "tx" really is the rate, so Station
            # keeps it.
            tx_rate_mbps=as_number(pick(status, "txrate", "tx_rate", "wlanTxRate")),
            rx_rate_mbps=as_number(pick(status, "rxrate", "rx_rate", "wlanRxRate")),
            # ``deviceId`` is the flat ``mca-status`` spelling and the only one
            # observed on airOS 8.7.11 — ``mca-dump`` returns nothing at all on
            # that firmware, so the JSON document's field names are untested here.
            device_mac=mac_or_none(pick(status, "deviceId", "device_id", "hwaddr")),
            model=as_text(pick(status, "devmodel", "model", "board.name", "platform")),
            platform=as_text(pick(status, "board.shortname", "platform")),
            firmware=as_text(
                pick(status, "fwversion", "firmware", "version", "fwprefix")
            ),
            hostname=as_text(pick(status, "hostname", "devname", "deviceName")),
            uptime_s=as_number(pick(status, "uptime")),
            frequency_mhz=as_number(pick(status, "freq", "frequency", "wlanFrequency")),
            chanbw_mhz=as_number(pick(status, "chanbw", "chwidth", "wlanChannelWidth")),
            essid=as_text(pick(status, "essid", "ssid", "wlanEssid")),
            wireless_mode=as_text(pick(status, "mode", "wlanOpmode", "netrole")),
            # Normalised, not raw: an unassociated station reports all zeroes,
            # which must read as "no peer" rather than as an address.
            ap_mac=mac_or_none(pick(status, "apmac", "ap_mac", "wlanApMac")),
            distance_m=as_number(pick(status, "distance")),
            station_count=len(station_list)
            or int(as_number(pick(status, "count", "sta_count")) or 0),
            stations=station_list,
            raw=dict(status),
        )


class ThroughputTracker:
    """Turns interface byte counters into a rate.

    Kept as state across polls rather than derived per-sample, because a rate
    needs two readings. A counter that goes backwards means the radio rebooted,
    so that sample is discarded rather than reported as a wild negative spike.
    """

    #: Preference order — the wireless interface first, since that is the link
    #: we actually care about; bridge/ethernet only as a fallback.
    DEFAULT_INTERFACES = ("ath0", "wlan0", "br0", "eth0")

    def __init__(self, interfaces: tuple[str, ...] = DEFAULT_INTERFACES) -> None:
        self.interfaces = interfaces
        self._last: tuple[float, int, int] | None = None

    def reset(self) -> None:
        self._last = None

    def select(self, counters: Mapping[str, tuple[int, int]]) -> tuple[int, int] | None:
        for name in self.interfaces:
            if name in counters:
                return counters[name]
        return None

    def update(
        self, counters: Mapping[str, tuple[int, int]], now: float
    ) -> tuple[float | None, float | None]:
        """Feed a new sample, return ``(rx_kbps, tx_kbps)``.

        Returns ``(None, None)`` for the first sample, an unknown interface, or a
        counter reset — all cases where a rate cannot honestly be reported.
        """
        current = self.select(counters)
        if current is None:
            return None, None
        rx, tx = current
        previous = self._last
        self._last = (now, rx, tx)
        if previous is None:
            return None, None
        prev_time, prev_rx, prev_tx = previous
        elapsed = now - prev_time
        if elapsed <= 0:
            return None, None
        if rx < prev_rx or tx < prev_tx:
            return None, None  # counter reset -> the radio rebooted
        return (
            (rx - prev_rx) * 8 / 1000.0 / elapsed,
            (tx - prev_tx) * 8 / 1000.0 / elapsed,
        )
