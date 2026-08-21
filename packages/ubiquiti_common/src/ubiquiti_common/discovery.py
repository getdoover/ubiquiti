"""UBNT device discovery over UDP 10001.

Ubiquiti radios answer a small broadcast probe with a TLV-encoded description of
themselves. Crucially the reply carries the device's *current* IP even when that
IP is outside our subnet — which is how a factory-default radio sitting on
192.168.1.20 gets found on a 10.x LAN. See :mod:`ubiquiti_common.netif` for
making that address actually reachable afterwards.

Wire format
-----------
Probe (v1)::   01 00 00 00
Probe (v2)::   02 08 00 00

Reply::        <version:1> <cmd:1> <payload_len:2>  then repeated TLVs of
               <type:1> <length:2> <value:length>

The TLV type table below is assembled from observed traffic and is only partly
documented by Ubiquiti. Every parsed reply therefore keeps its raw TLVs
(:attr:`DiscoveredDevice.raw_tlvs`) so ``airos discover --raw`` can confirm field
meanings against real hardware rather than trusting this table.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct

from .models import DiscoveredDevice, normalise_mac

log = logging.getLogger(__name__)

DISCOVERY_PORT = 10001
PROBE_V1 = b"\x01\x00\x00\x00"
PROBE_V2 = b"\x02\x08\x00\x00"

TLV_MAC = 0x01
TLV_MAC_AND_IP = 0x02
TLV_FIRMWARE = 0x03
TLV_UPTIME = 0x0A
TLV_HOSTNAME = 0x0B
TLV_PRODUCT = 0x0C
TLV_ESSID = 0x0D
TLV_WMODE = 0x0E
TLV_MODEL = 0x14
TLV_VERSION_V2 = 0x16

_TEXT_TLVS = {
    TLV_FIRMWARE: "firmware",
    TLV_HOSTNAME: "hostname",
    TLV_PRODUCT: "product",
    TLV_ESSID: "essid",
    TLV_MODEL: "model",
}


def parse_tlvs(payload: bytes) -> dict[int, list[bytes]]:
    """Split a discovery payload into ``{tlv_type: [values]}``.

    Tolerant by design: a malformed or truncated TLV stops parsing and returns
    what was read so far rather than raising, because one odd device on the wire
    must not stop the rest of a sweep being useful.
    """
    tlvs: dict[int, list[bytes]] = {}
    offset = 0
    end = len(payload)
    while offset + 3 <= end:
        tlv_type = payload[offset]
        (length,) = struct.unpack_from(">H", payload, offset + 1)
        offset += 3
        if length < 0 or offset + length > end:
            log.debug(
                "truncated TLV type=0x%02x len=%d, stopping parse", tlv_type, length
            )
            break
        tlvs.setdefault(tlv_type, []).append(payload[offset : offset + length])
        offset += length
    return tlvs


def _is_link_local(addr: str) -> bool:
    return addr.startswith("169.254.")


def _best_address(
    mac: str, advertised: list[tuple[str, str]], sender_ip: str | None
) -> str | None:
    """Pick the address we should actually try to reach the radio on.

    ``sender_ip`` wins whenever we have it: the reply demonstrably arrived from
    there, so it is reachable by construction. Otherwise prefer an advertised
    address belonging to the primary MAC, skipping 169.254/16 link-locals and the
    secondary-bridge addresses that ride on a different MAC — connecting to one of
    those instead of the LAN address is a silent dead end.
    """
    if sender_ip:
        return sender_ip
    for candidate_mac, addr in advertised:
        if candidate_mac == mac and not _is_link_local(addr):
            return addr
    for _, addr in advertised:
        if not _is_link_local(addr):
            return addr
    return advertised[0][1] if advertised else None


def parse_reply(data: bytes, sender_ip: str | None = None) -> DiscoveredDevice | None:
    """Build a :class:`DiscoveredDevice` from one discovery reply datagram.

    Returns ``None`` if the datagram carries no usable MAC, which is the only
    field we genuinely require.
    """
    if len(data) < 4:
        return None
    payload = data[4:]
    tlvs = parse_tlvs(payload)
    if not tlvs:
        return None

    # The primary MAC comes from 0x01 when present. Real radios send several 0x02
    # (MAC+IP) TLVs — the LAN address, a 169.254/16 link-local, and a secondary
    # bridge on a *different* (locally-administered) MAC — so 0x01 is the
    # authoritative identity and 0x02 is a list of candidates, not a single fact.
    mac: str | None = None
    for value in tlvs.get(TLV_MAC, []):
        if len(value) >= 6:
            mac = normalise_mac(value[:6].hex())
            break

    advertised: list[tuple[str, str]] = []
    for value in tlvs.get(TLV_MAC_AND_IP, []):
        if len(value) >= 10:
            advertised.append(
                (normalise_mac(value[:6].hex()), socket.inet_ntoa(value[6:10]))
            )
    if mac is None and advertised:
        mac = advertised[0][0]
    if mac is None:
        return None

    ip = _best_address(mac, advertised, sender_ip)

    fields: dict[str, str] = {}
    for tlv_type, name in _TEXT_TLVS.items():
        values = tlvs.get(tlv_type)
        if values:
            fields[name] = (
                values[0].decode("utf-8", errors="replace").strip("\x00").strip()
            )

    uptime = None
    uptime_values = tlvs.get(TLV_UPTIME)
    if uptime_values and len(uptime_values[0]) >= 4:
        (uptime,) = struct.unpack(">I", uptime_values[0][:4])

    # Fall back to the v2-only version TLV if the v1 firmware string was absent.
    if not fields.get("firmware"):
        v2 = tlvs.get(TLV_VERSION_V2)
        if v2:
            fields["firmware"] = (
                v2[0].decode("utf-8", errors="replace").strip("\x00").strip()
            )

    return DiscoveredDevice(
        mac=mac,
        ip=ip,
        uptime=uptime,
        advertised_ips=[addr for _, addr in advertised],
        raw_tlvs=tlvs,
        **fields,
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.replies: dict[str, DiscoveredDevice] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            device = parse_reply(data, sender_ip=addr[0])
        except Exception:  # a single bad packet must not kill the sweep
            log.debug("undecodable discovery reply from %s", addr[0], exc_info=True)
            return
        if device is None:
            return
        # Later replies win: a v2 reply generally carries more fields than v1.
        existing = self.replies.get(device.mac)
        if existing is None or (device.firmware and not existing.firmware):
            self.replies[device.mac] = device

    def error_received(self, exc: Exception) -> None:
        log.debug("discovery socket error: %s", exc)


async def discover(
    broadcast_addrs: list[str] | None = None,
    timeout: float = 3.0,
    probes: tuple[bytes, ...] = (PROBE_V1, PROBE_V2),
    bind_addr: str = "0.0.0.0",
) -> list[DiscoveredDevice]:
    """Broadcast discovery probes and collect replies for ``timeout`` seconds.

    ``broadcast_addrs`` should be the per-subnet broadcast addresses of the
    provisioning interface (see :func:`ubiquiti_common.netif.broadcast_addresses`).
    ``255.255.255.255`` is always probed as well, which is what reaches a radio
    whose address is outside every local subnet.
    """
    addrs = list(broadcast_addrs or [])
    if "255.255.255.255" not in addrs:
        addrs.append("255.255.255.255")

    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind((bind_addr, 0))

    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol, sock=sock
    )
    try:
        for probe in probes:
            for addr in addrs:
                try:
                    transport.sendto(probe, (addr, DISCOVERY_PORT))
                except OSError as exc:
                    log.debug("could not probe %s: %s", addr, exc)
            await asyncio.sleep(0.1)
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    devices = sorted(protocol.replies.values(), key=lambda d: d.mac)
    log.info("discovery found %d device(s) on %s", len(devices), ", ".join(addrs))
    return devices


async def find_by_mac(
    mac: str,
    broadcast_addrs: list[str] | None = None,
    timeout: float = 3.0,
) -> DiscoveredDevice | None:
    """Convenience wrapper: discover, then return the device with this MAC."""
    wanted = normalise_mac(mac)
    for device in await discover(broadcast_addrs=broadcast_addrs, timeout=timeout):
        if device.mac == wanted:
            return device
    return None
