"""Telemetry parsing.

The field-name aliases in telemetry.py are reconstructed from observed airOS
output, not documented, so these tests pin the *decoder's behaviour* — that it
strips units, normalises CCQ, derives SNR and degrades rather than raising.
Field meanings themselves get confirmed against real hardware with
``airos status --raw``.
"""

import json

import pytest

from ubiquiti_common import telemetry as t

MCA_STATUS = """\
deviceName=BulletM2HP
essid=SPAN-LINK
freq=5800 MHz
signal=-68
noise=-94
ccq=970
wlanTxRate=130 Mbps
wlanRxRate=117 Mbps
uptime=98765
chanbw=20
this line has no equals sign
"""

MCA_DUMP = json.dumps(
    {
        "host": {"hostname": "bullet-north", "fwversion": "WA.v8.7.11", "uptime": 4321},
        "wireless": {
            "essid": "SPAN-LINK",
            "mode": "sta",
            "signal": -71,
            "noisef": -96,
            "ccq": 94,
            "txrate": 144,
            "rxrate": 130,
            "frequency": 5745,
            "chwidth": 40,
            "apmac": "04:18:d6:11:22:33",
            "distance": 1200,
        },
    }
)


# ---------------------------------------------------------------- primitives


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-68 dBm", -68.0),
        ("5800MHz", 5800.0),
        ("130 Mbps", 130.0),
        ("97", 97.0),
        (97, 97.0),
        (12.5, 12.5),
        ("n/a", None),
        ("", None),
        (None, None),
        (True, None),
    ],
)
def test_as_number_strips_units_and_degrades(raw, expected):
    assert t.as_number(raw) == expected


@pytest.mark.parametrize(
    "raw,expected", [(970, 97.0), (97, 97.0), (1000, 100.0), (0, 0.0), (None, None)]
)
def test_ccq_normalised_from_tenths(raw, expected):
    assert t._scale_ccq(raw) == expected


# -------------------------------------------------------------- flat parsing


def test_parse_mca_status_ignores_junk_lines():
    parsed = t.parse_mca_status(MCA_STATUS)
    assert parsed["signal"] == "-68"
    assert parsed["freq"] == "5800 MHz"
    assert "this line has no equals sign" not in parsed


def test_parse_mca_status_on_empty_input():
    assert t.parse_mca_status("") == {}


def test_telemetry_from_mca_status():
    tel = t.Telemetry.from_status(t.parse_mca_status(MCA_STATUS))
    assert tel.online is True
    assert tel.signal_dbm == -68
    assert tel.noise_dbm == -94
    assert tel.snr_db == 26
    assert tel.ccq_pct == 97
    assert tel.tx_rate_mbps == 130
    assert tel.rx_rate_mbps == 117
    assert tel.frequency_mhz == 5800
    assert tel.chanbw_mhz == 20
    assert tel.essid == "SPAN-LINK"
    assert tel.uptime_s == 98765


# -------------------------------------------------------------- json parsing


def test_parse_mca_dump_flattens_nested_json():
    flat = t.parse_mca_dump(MCA_DUMP)
    assert flat["wireless.signal"] == -71
    assert flat["host.hostname"] == "bullet-north"


def test_parse_mca_dump_on_garbage():
    assert t.parse_mca_dump("not json") == {}
    assert t.parse_mca_dump("[1,2,3]") == {}
    assert t.parse_mca_dump("") == {}


def test_telemetry_from_mca_dump_finds_nested_fields():
    """`pick` matches on the last dotted segment, so wireless.signal answers 'signal'."""
    tel = t.Telemetry.from_status(t.parse_mca_dump(MCA_DUMP))
    assert tel.signal_dbm == -71
    assert tel.ccq_pct == 94
    assert tel.tx_rate_mbps == 144
    assert tel.frequency_mhz == 5745
    assert tel.chanbw_mhz == 40
    assert tel.hostname == "bullet-north"
    assert tel.firmware == "WA.v8.7.11"
    assert tel.ap_mac == "04:18:d6:11:22:33"
    assert tel.distance_m == 1200
    assert tel.wireless_mode == "sta"


def test_snr_is_none_when_either_half_is_missing():
    assert t.Telemetry.from_status({"signal": "-68"}).snr_db is None
    assert t.Telemetry.from_status({"noise": "-94"}).snr_db is None
    assert t.Telemetry.from_status({}).snr_db is None


def test_offline_reading_carries_the_reason():
    tel = t.Telemetry.offline("not seen on the wire")
    assert tel.online is False
    assert tel.error == "not seen on the wire"
    assert tel.signal_dbm is None


# ------------------------------------------------------------------ stations


def test_parse_wstalist_bare_array():
    raw = json.dumps([{"mac": "04:18:d6:11:22:33", "signal": -71, "ccq": 880}])
    stations = t.parse_wstalist(raw)
    assert len(stations) == 1
    station = t.Station.from_mapping(stations[0])
    assert station.mac == "04:18:d6:11:22:33"
    assert station.signal_dbm == -71
    assert station.ccq_pct == 88


def test_parse_wstalist_wrapped_object():
    raw = json.dumps({"sta": [{"mac": "aa:bb:cc:dd:ee:ff", "signal": -60}]})
    assert len(t.parse_wstalist(raw)) == 1


def test_parse_wstalist_degrades():
    assert t.parse_wstalist("not json") == []
    assert t.parse_wstalist("{}") == []
    assert t.parse_wstalist("") == []


def test_station_count_comes_from_the_list():
    stations = [{"mac": "a", "signal": -60}, {"mac": "b", "signal": -70}]
    tel = t.Telemetry.from_status({"signal": "-65"}, stations)
    assert tel.station_count == 2
    assert len(tel.stations) == 2


def test_stations_embedded_in_the_status_document_are_found():
    flat = t.parse_mca_dump(
        json.dumps({"wireless": {"signal": -70, "sta": [{"mac": "a"}, {"mac": "b"}]}})
    )
    tel = t.Telemetry.from_status(flat)
    assert tel.station_count == 2


# ---------------------------------------------------------------- throughput


PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0
  ath0: 1000000    1000    0    0    0     0          0         0   500000     500    0    0    0     0       0          0
  eth0:  200000     200    0    0    0     0          0         0   100000     100    0    0    0     0       0          0
"""


def test_parse_proc_net_dev():
    counters = t.parse_proc_net_dev(PROC_NET_DEV)
    assert counters["ath0"] == (1000000, 500000)
    assert counters["eth0"] == (200000, 100000)
    assert "lo" in counters


def test_throughput_needs_two_samples():
    tracker = t.ThroughputTracker()
    counters = t.parse_proc_net_dev(PROC_NET_DEV)
    assert tracker.update(counters, 0.0) == (None, None)
    # 1_000_000 more rx bytes over 10s = 800 kbps
    later = {"ath0": (2000000, 1000000)}
    rx, tx = tracker.update(later, 10.0)
    assert rx == pytest.approx(800.0)
    assert tx == pytest.approx(400.0)


def test_throughput_prefers_the_wireless_interface():
    tracker = t.ThroughputTracker()
    assert tracker.select({"eth0": (1, 2), "ath0": (3, 4)}) == (3, 4)
    assert tracker.select({"eth0": (1, 2)}) == (1, 2)
    assert tracker.select({"wg0": (1, 2)}) is None


def test_counter_reset_is_discarded_not_reported_as_negative():
    """A radio reboot restarts the counters; that must not surface as a spike."""
    tracker = t.ThroughputTracker()
    tracker.update({"ath0": (1000000, 500000)}, 0.0)
    assert tracker.update({"ath0": (10, 5)}, 10.0) == (None, None)


def test_zero_elapsed_time_is_not_a_division_error():
    tracker = t.ThroughputTracker()
    tracker.update({"ath0": (100, 100)}, 5.0)
    assert tracker.update({"ath0": (200, 200)}, 5.0) == (None, None)


def test_tracker_reset_forces_a_fresh_baseline():
    tracker = t.ThroughputTracker()
    tracker.update({"ath0": (100, 100)}, 0.0)
    tracker.reset()
    assert tracker.update({"ath0": (200, 200)}, 10.0) == (None, None)
