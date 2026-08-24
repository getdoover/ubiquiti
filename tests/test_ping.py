"""ICMP round-trip measurement.

The sample outputs below are copied verbatim from `ping` in the real base image
(BusyBox v1.37.0), plus the two other formats the parser has to survive: iputils
on a Linux host and macOS on a dev machine. Guessing at these formats is how the
first version silently returned no RTT at all.
"""

import asyncio

import pytest

from ubiquiti_common import ping as P

# Real output, spaneng/doover_device_base, BusyBox v1.37.0.
BUSYBOX_OK = """PING 127.0.0.1 (127.0.0.1): 56 data bytes

--- 127.0.0.1 ping statistics ---
3 packets transmitted, 3 packets received, 0% packet loss
round-trip min/avg/max = 0.098/0.204/0.402 ms
"""

# Same binary, host that answers nothing. Note there is NO round-trip line at
# all — the parser must not depend on one existing.
BUSYBOX_LOST = """PING 192.0.2.1 (192.0.2.1): 56 data bytes

--- 192.0.2.1 ping statistics ---
2 packets transmitted, 0 packets received, 100% packet loss
"""

IPUTILS_OK = """--- 10.0.0.1 ping statistics ---
5 packets transmitted, 5 received, 0% packet loss, time 4005ms
rtt min/avg/max/mdev = 1.234/2.345/3.456/0.678 ms
"""

MACOS_OK = """--- 10.0.0.1 ping statistics ---
5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 0.052/0.075/0.098/0.019 ms
"""

PARTIAL_LOSS = """--- 10.0.0.1 ping statistics ---
5 packets transmitted, 4 packets received, 20% packet loss
round-trip min/avg/max = 1.000/2.000/3.000 ms
"""


def test_busybox_healthy():
    r = P.parse(BUSYBOX_OK, "127.0.0.1")
    assert (r.transmitted, r.received) == (3, 3)
    assert (r.rtt_min_ms, r.rtt_avg_ms, r.rtt_max_ms) == (0.098, 0.204, 0.402)
    assert r.loss_pct == 0.0
    assert r.ok


def test_busybox_total_loss_has_no_rtt_line():
    r = P.parse(BUSYBOX_LOST, "192.0.2.1")
    assert (r.transmitted, r.received) == (2, 0)
    assert r.loss_pct == 100.0
    assert not r.ok
    # None, not 0.0 — nothing came back, which is not the same as a 0 ms trip.
    assert r.rtt_avg_ms is None


@pytest.mark.parametrize("text", [IPUTILS_OK, MACOS_OK])
def test_other_ping_implementations_parse(text):
    """iputils says `mdev`, macOS says `stddev`. Pinning the fourth column's
    name to `mdev` made this return no RTT on a dev Mac."""
    r = P.parse(text, "10.0.0.1")
    assert r.received == 5
    assert r.rtt_avg_ms is not None


def test_partial_loss_reports_both_halves():
    r = P.parse(PARTIAL_LOSS, "10.0.0.1")
    assert r.loss_pct == 20.0
    assert r.rtt_avg_ms == 2.0, "a link can drop packets and still time well"
    assert r.ok


def test_loss_is_derived_from_counts_not_the_printed_percentage():
    """The two implementations format the percentage differently, so the counts
    are the trustworthy source."""
    r = P.parse("10 packets transmitted, 7 packets received, 99% packet loss", "x")
    assert r.loss_pct == 30.0


def test_no_counts_means_no_measurement():
    assert P.parse("", "x").loss_pct is None


@pytest.mark.parametrize("target", ["", "   ", "not a host!", "10.0.0.1; rm -rf /"])
def test_unusable_targets_are_refused_before_spawning_anything(target):
    with pytest.raises(P.PingError):
        asyncio.run(P.ping(target))


@pytest.mark.parametrize("target", ["10.0.0.1", "192.168.1.82", "radio.local"])
def test_usable_targets_accepted(target):
    assert P._valid_target(target)


def test_missing_ping_binary_raises_rather_than_reading_as_a_bad_link(monkeypatch):
    """A missing binary is a deployment fault. Reporting it as 100% loss would
    put a fake outage on every graph in the fleet."""

    async def no_binary(*a, **k):
        raise FileNotFoundError("ping")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_binary)
    with pytest.raises(P.PingError, match="not available"):
        asyncio.run(P.ping("10.0.0.1"))


def test_real_localhost_round_trip():
    """End-to-end against the actual ping on this machine."""
    r = asyncio.run(P.ping("127.0.0.1", count=2))
    assert r.ok and r.rtt_avg_ms is not None and r.loss_pct == 0.0


# ------------------------------------------------------- peer address resolution
#
# `_peer_address` only reads self.config.peer_address and self.telemetry.stations,
# so a stub stands in for the whole application.

from types import SimpleNamespace  # noqa: E402

from ubiquiti_common.telemetry import Station, Telemetry  # noqa: E402
from ubiquiti_airmax.application import AirMaxApplication  # noqa: E402


def _stub(configured="", stations=()):
    return SimpleNamespace(
        config=SimpleNamespace(peer_address=SimpleNamespace(value=configured)),
        telemetry=SimpleNamespace(stations=list(stations)),
    )


def test_ap_finds_its_peer_without_any_config():
    """The common case: an AP reads its stations' addresses out of wstalist."""
    app = _stub(stations=[Station(mac="00:11:22:33:44:55", ip="192.168.1.81")])
    assert AirMaxApplication._peer_address(app) == "192.168.1.81"


def test_configured_peer_beats_the_discovered_one():
    app = _stub(
        configured="192.168.1.72",
        stations=[Station(mac="00:11:22:33:44:55", ip="192.168.1.81")],
    )
    assert AirMaxApplication._peer_address(app) == "192.168.1.72"


def test_client_with_no_config_has_no_peer():
    """A client's mca-status names its AP by MAC, not address, so there is
    nothing to derive — better no reading than a wrong one."""
    assert AirMaxApplication._peer_address(_stub()) is None


def test_stations_without_an_address_are_skipped():
    app = _stub(stations=[Station(mac="aa:bb:cc:dd:ee:ff"), Station(ip="192.168.1.91")])
    assert AirMaxApplication._peer_address(app) == "192.168.1.91"


def test_whitespace_only_config_is_not_a_peer():
    assert AirMaxApplication._peer_address(_stub(configured="   ")) is None


def test_station_ip_is_read_from_wstalist_lastip():
    """airOS spells it `lastip`."""
    assert Station.from_mapping({"lastip": "192.168.1.81"}).ip == "192.168.1.81"
    assert Station.from_mapping({"mac": "x"}).ip is None


def test_station_ip_reaches_the_network_overview():
    assert Station(ip="192.168.1.81").to_dict()["ip"] == "192.168.1.81"


def test_airmax_queue_latency_is_still_parsed_but_not_what_we_publish():
    """`wlanTxLatency` stays parsed (it is a congestion signal), but the
    published `latency` tag must be the ICMP round trip. Publishing the airMAX
    figure gave a constant 0 across the whole fleet."""
    import inspect

    assert Telemetry.from_status({"wlanTxLatency": "7"}).latency_ms == 7.0
    src = inspect.getsource(AirMaxApplication._publish)
    assert "self.tags.latency.set(p.rtt_avg_ms" in src
    # Comments are stripped first: the code deliberately *mentions* the field it
    # is not publishing, and a naive substring check trips over the explanation.
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "tel.latency_ms" not in code, "must not publish airMAX queue latency"


def test_link_is_not_measured_when_the_radio_is_offline():
    import inspect

    src = inspect.getsource(AirMaxApplication._measure_link)
    assert "if not self.telemetry.online" in src
    # A failure to run ping must not masquerade as a dead link.
    assert "except PingError" in src
