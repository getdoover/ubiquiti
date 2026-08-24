"""UBNT discovery TLV decoding.

The TLV type table is only partly documented by Ubiquiti, so these tests pin the
decoder's *behaviour* — including that it degrades rather than raises. Field
meanings themselves are confirmed against real hardware with
``airos discover --raw``.
"""

import struct

from ubiquiti_common import discovery
from ubiquiti_common.models import Platform


def tlv(tlv_type: int, value: bytes) -> bytes:
    return bytes([tlv_type]) + struct.pack(">H", len(value)) + value


def reply(*tlvs: bytes) -> bytes:
    payload = b"".join(tlvs)
    return b"\x01\x00" + struct.pack(">H", len(payload)) + payload


BULLET_M = reply(
    tlv(
        discovery.TLV_MAC_AND_IP,
        bytes.fromhex("0418d6aabbcc") + bytes([192, 168, 1, 20]),
    ),
    tlv(discovery.TLV_FIRMWARE, b"XM.ar7240.v6.3.11.34009.210325.1502"),
    tlv(discovery.TLV_HOSTNAME, b"BulletM2HP"),
    tlv(discovery.TLV_PRODUCT, b"BM2HP"),
    tlv(discovery.TLV_MODEL, b"Bullet M2HP"),
    tlv(discovery.TLV_UPTIME, struct.pack(">I", 98765)),
)

BULLET_AC = reply(
    tlv(discovery.TLV_MAC_AND_IP, bytes.fromhex("788a20112233") + bytes([10, 0, 0, 7])),
    tlv(discovery.TLV_FIRMWARE, b"WA.qca955x.v8.7.11.46972.230511.1211"),
    tlv(discovery.TLV_MODEL, b"Bullet AC"),
)


def test_parses_mac_and_ip_together():
    device = discovery.parse_reply(BULLET_M)
    assert device.mac == "04:18:d6:aa:bb:cc"
    assert device.ip == "192.168.1.20"


def test_extracts_platform_and_generation():
    m = discovery.parse_reply(BULLET_M)
    assert m.platform is Platform.XM
    assert m.generation == "airos6"
    ac = discovery.parse_reply(BULLET_AC)
    assert ac.platform is Platform.WA
    assert ac.generation == "airos8"


def test_extracts_text_fields_and_uptime():
    device = discovery.parse_reply(BULLET_M)
    assert device.model == "Bullet M2HP"
    assert device.product == "BM2HP"
    assert device.hostname == "BulletM2HP"
    assert device.uptime == 98765


def test_mac_only_reply_falls_back_to_sender_ip():
    data = reply(tlv(discovery.TLV_MAC, bytes.fromhex("0418d6aabbcc")))
    device = discovery.parse_reply(data, sender_ip="10.1.2.3")
    assert device.mac == "04:18:d6:aa:bb:cc"
    assert device.ip == "10.1.2.3"


def test_v2_version_tlv_used_when_firmware_absent():
    data = reply(
        tlv(discovery.TLV_MAC, bytes.fromhex("0418d6aabbcc")),
        tlv(discovery.TLV_VERSION_V2, b"XC.qca955x.v8.7.4.1234.000000.0000"),
    )
    assert discovery.parse_reply(data).platform is Platform.XC


def test_truncated_tlv_returns_what_was_read_rather_than_raising():
    device = discovery.parse_reply(BULLET_M[:20])
    assert device is not None
    assert device.mac == "04:18:d6:aa:bb:cc"
    assert device.firmware is None


def test_reply_without_mac_is_discarded():
    assert discovery.parse_reply(reply(tlv(discovery.TLV_HOSTNAME, b"x"))) is None
    assert discovery.parse_reply(b"") is None
    assert discovery.parse_reply(b"\x01\x00\x00\x02\xff") is None


def test_unknown_tlvs_are_retained_for_bench_inspection():
    data = reply(
        tlv(discovery.TLV_MAC, bytes.fromhex("0418d6aabbcc")),
        tlv(0x7F, b"mystery"),
    )
    device = discovery.parse_reply(data)
    assert device.raw_tlvs[0x7F] == [b"mystery"]


def test_unknown_platform_does_not_raise():
    data = reply(
        tlv(discovery.TLV_MAC, bytes.fromhex("0418d6aabbcc")),
        tlv(discovery.TLV_FIRMWARE, b"totally-unexpected"),
    )
    device = discovery.parse_reply(data)
    assert device.platform is Platform.UNKNOWN
    assert device.generation == "unknown"


# ------------------------------------------------------- end-to-end round trip


async def test_discover_round_trips_against_a_local_responder():
    """Probe emission, reply collection and parsing, through the real socket path.

    Without this, a "0 devices found" result on a live network is ambiguous — it
    could mean nothing is out there, or that the probe never left. This pins the
    plumbing so a negative sweep is trustworthy.
    """
    import asyncio

    from ubiquiti_common import discovery as disc

    probes_seen = []

    class Responder(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            probes_seen.append(data)
            self.transport.sendto(BULLET_M, addr)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        Responder, local_addr=("127.0.0.1", disc.DISCOVERY_PORT)
    )
    try:
        devices = await disc.discover(
            broadcast_addrs=["127.0.0.1"], timeout=1.0, bind_addr="127.0.0.1"
        )
    finally:
        transport.close()

    assert probes_seen, "no probe reached the responder"
    assert disc.PROBE_V1 in probes_seen and disc.PROBE_V2 in probes_seen
    assert len(devices) == 1
    assert devices[0].mac == "04:18:d6:aa:bb:cc"
    assert devices[0].model == "Bullet M2HP"
    assert devices[0].platform is Platform.XM


# ------------------------------------------------- real hardware capture (2026-08-21)
#
# Byte-for-byte from a Bullet AC IP67 on station-1's LAN. Everything below is a
# regression test for something this capture proved wrong in the reconstructed
# TLV handling, so prefer editing the code over editing these.

BULLET_AC_IP67_REAL = reply(
    tlv(discovery.TLV_MAC, bytes.fromhex("28704ee29bcb")),
    # Three advertised addresses: the LAN one, a link-local, and a secondary
    # bridge on a DIFFERENT (locally-administered) MAC.
    tlv(
        discovery.TLV_MAC_AND_IP,
        bytes.fromhex("28704ee29bcb") + bytes([192, 168, 1, 12]),
    ),
    tlv(
        discovery.TLV_MAC_AND_IP,
        bytes.fromhex("28704ee29bcb") + bytes([169, 254, 155, 203]),
    ),
    tlv(
        discovery.TLV_MAC_AND_IP,
        bytes.fromhex("2a704ee29bcb") + bytes([192, 168, 172, 1]),
    ),
    tlv(discovery.TLV_FIRMWARE, b"2WA.ar934x.v8.7.11.46972.220614.0419"),
    tlv(discovery.TLV_UPTIME, bytes.fromhex("00000243")),
    tlv(discovery.TLV_HOSTNAME, b"Bullet AC IP67"),
    tlv(discovery.TLV_PRODUCT, b"BulletAC-IP67"),
    tlv(discovery.TLV_ESSID, b""),
    tlv(discovery.TLV_WMODE, bytes.fromhex("02")),
    tlv(0x10, bytes.fromhex("e2c7")),
    tlv(0x13, bytes.fromhex("28704ee29bcb")),
    tlv(discovery.TLV_MODEL, b"Bullet AC IP67"),
    tlv(0x18, bytes.fromhex("00000000")),
)


def test_real_bullet_ac_identity():
    d = discovery.parse_reply(BULLET_AC_IP67_REAL, sender_ip="192.168.1.12")
    assert d.mac == "28:70:4e:e2:9b:cb"
    assert d.model == "Bullet AC IP67"
    assert d.product == "BulletAC-IP67"
    assert d.hostname == "Bullet AC IP67"
    assert d.uptime == 579


def test_real_firmware_leading_digit_still_resolves_platform():
    """`2WA.` not `WA.` — the digit made every AC radio parse as UNKNOWN."""
    d = discovery.parse_reply(BULLET_AC_IP67_REAL, sender_ip="192.168.1.12")
    assert d.platform is Platform.WA
    assert d.generation == "airos8"
    assert d.version.startswith("8.7.11")


def test_real_reply_prefers_the_address_it_arrived_from():
    d = discovery.parse_reply(BULLET_AC_IP67_REAL, sender_ip="192.168.1.12")
    assert d.ip == "192.168.1.12"
    # All candidates retained for diagnostics.
    assert d.advertised_ips == ["192.168.1.12", "169.254.155.203", "192.168.172.1"]


def test_real_reply_without_sender_skips_link_local_and_foreign_mac():
    """Without a sender address, never fall through to 169.254/16 or the
    secondary bridge — both are silent dead ends to SSH to."""
    d = discovery.parse_reply(BULLET_AC_IP67_REAL, sender_ip=None)
    assert d.ip == "192.168.1.12"


def test_link_local_only_device_does_not_masquerade_as_reachable():
    data = reply(
        tlv(discovery.TLV_MAC, bytes.fromhex("28704ee29bcb")),
        tlv(
            discovery.TLV_MAC_AND_IP,
            bytes.fromhex("28704ee29bcb") + bytes([169, 254, 155, 203]),
        ),
    )
    d = discovery.parse_reply(data, sender_ip=None)
    # Nothing better exists, so it is reported, but the caller can see what it is.
    assert d.ip == "169.254.155.203"
    assert discovery._is_link_local(d.ip)


def test_secondary_bridge_mac_is_not_mistaken_for_the_device():
    """0x01 is authoritative — not whichever 0x02 happens to come first."""
    data = reply(
        tlv(
            discovery.TLV_MAC_AND_IP,
            bytes.fromhex("2a704ee29bcb") + bytes([192, 168, 172, 1]),
        ),
        tlv(discovery.TLV_MAC, bytes.fromhex("28704ee29bcb")),
        tlv(
            discovery.TLV_MAC_AND_IP,
            bytes.fromhex("28704ee29bcb") + bytes([192, 168, 1, 12]),
        ),
    )
    d = discovery.parse_reply(data, sender_ip=None)
    assert d.mac == "28:70:4e:e2:9b:cb"
    assert d.ip == "192.168.1.12"


# ------------------------------------------------------- unicast fallback
#
# Broadcast is not always deliverable. Observed on Porgera Station 2: its br0
# bridges eth1 + wlan0, and the wireless hop forwards unicast but drops
# broadcast — ping and ARP worked, a unicast UBNT probe got 4/4 replies, and a
# broadcast probe got 0. The radios were reachable and completely invisible.


async def test_unicast_probe_finds_a_device_broadcast_would_miss():
    import asyncio

    from ubiquiti_common import discovery as disc

    got = []

    class Responder(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            got.append(data)
            self.transport.sendto(BULLET_M, addr)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        Responder, local_addr=("127.0.0.1", disc.DISCOVERY_PORT)
    )
    try:
        devices = await disc.unicast_probe(
            ["127.0.0.1"], timeout=1.0, bind_addr="127.0.0.1"
        )
    finally:
        transport.close()

    assert got, "no probe reached the responder"
    assert len(devices) == 1
    assert devices[0].mac == "04:18:d6:aa:bb:cc"


async def test_unicast_probe_with_no_addresses_is_a_noop():
    from ubiquiti_common import discovery as disc

    assert await disc.unicast_probe([], timeout=0.1) == []


def test_hosts_of_enumerates_a_slash_24():
    import ipaddress

    from ubiquiti_common import discovery as disc

    hosts = disc.hosts_of([ipaddress.ip_network("192.168.1.0/24")])
    assert len(hosts) == 254
    assert "192.168.1.1" in hosts and "192.168.1.254" in hosts
    assert "192.168.1.0" not in hosts and "192.168.1.255" not in hosts


def test_hosts_of_skips_networks_over_the_cap():
    """Never silently partial: an oversized network is skipped, not truncated."""
    import ipaddress

    from ubiquiti_common import discovery as disc

    assert disc.hosts_of([ipaddress.ip_network("10.0.0.0/16")]) == []
    mixed = disc.hosts_of(
        [ipaddress.ip_network("10.0.0.0/16"), ipaddress.ip_network("192.168.5.0/24")]
    )
    assert len(mixed) == 254


def test_hosts_of_dedupes_overlapping_networks():
    import ipaddress

    from ubiquiti_common import discovery as disc

    hosts = disc.hosts_of(
        [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("192.168.1.0/25")]
    )
    assert len(hosts) == len(set(hosts)) == 254


def test_unicast_probe_paces_its_sends_and_repeats():
    """A single 500-datagram burst lost replies: a sweep of 253 addresses found
    only 3 of 7 radios present, so discovery became a coin toss per pass.

    Pinned at the signature level because the failure is invisible in a unit test
    with one responder — it only shows up against real hardware under load.
    """
    import inspect

    from ubiquiti_common import discovery as disc

    sig = inspect.signature(disc.unicast_probe).parameters
    assert "batch" in sig and sig["batch"].default <= 32, (
        "sends must be batched small; a single burst loses replies"
    )
    assert "batch_pause" in sig and sig["batch_pause"].default > 0
    assert "rounds" in sig and sig["rounds"].default >= 2, (
        "probe at least twice; one round is unreliable"
    )


async def test_unicast_probe_still_finds_a_device_when_paced():
    """The pacing must not break the happy path."""
    import asyncio

    from ubiquiti_common import discovery as disc

    class Responder(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.transport.sendto(BULLET_M, addr)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        Responder, local_addr=("127.0.0.1", disc.DISCOVERY_PORT)
    )
    try:
        devices = await disc.unicast_probe(
            ["127.0.0.1"], timeout=1.0, bind_addr="127.0.0.1"
        )
    finally:
        transport.close()
    assert len(devices) == 1, "a repeated probe must still dedupe to one device"
    assert devices[0].mac == "04:18:d6:aa:bb:cc"
