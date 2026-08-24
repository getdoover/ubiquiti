import pytest

from ubiquiti_common.models import Platform, normalise_mac, parse_firmware


@pytest.mark.parametrize(
    "raw",
    ["04:18:D6:AA:BB:CC", "04-18-d6-aa-bb-cc", "0418d6aabbcc", "04 18 d6 aa bb cc"],
)
def test_normalise_mac_accepts_common_shapes(raw):
    assert normalise_mac(raw) == "04:18:d6:aa:bb:cc"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-a-mac",
        "0418d6aabbc",  # 11 hex digits — nothing to recover
        "zz:zz:zz",
    ],
)
def test_normalise_mac_rejects_too_short(raw):
    """Short input is an error; there is nothing to truncate to."""
    with pytest.raises(ValueError):
        normalise_mac(raw)


# --------------------------------------------------- over-long label input
#
# Ubiquiti prints the MAC on the device label behind a batch code. The first
# example is verbatim from a real Bullet AC IP67, and the expected MAC is the one
# that radio actually reported over UBNT discovery.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2450BJ28704EE29BCB", "28:70:4e:e2:9b:cb"),  # real device label
        ("2450bj28704ee29bcb", "28:70:4e:e2:9b:cb"),  # same, lowercased
        ("0418d6aabbccdd", "18:d6:aa:bb:cc:dd"),  # 14 hex digits
        ("BATCH-99 04:18:D6:AA:BB:CC", "04:18:d6:aa:bb:cc"),
    ],
)
def test_normalise_mac_keeps_the_last_twelve_hex_digits(raw, expected):
    assert normalise_mac(raw) == expected


def test_truncation_is_logged_not_silent(caplog):
    """A typo that makes the input over-long targets a different radio, so the
    operator has to be able to see the interpretation."""
    import logging

    with caplog.at_level(logging.INFO, logger="ubiquiti_common.models"):
        normalise_mac("2450BJ28704EE29BCB")
    assert any("last 12 hex digits" in r.getMessage() for r in caplog.records)


def test_exact_length_mac_is_not_logged(caplog):
    """Discovery normalises a MAC per reply — that path must stay quiet."""
    import logging

    with caplog.at_level(logging.INFO, logger="ubiquiti_common.models"):
        normalise_mac("28:70:4E:E2:9B:CB")
    assert caplog.records == []


def test_parse_firmware_airos6():
    platform, chipset, version = parse_firmware("XM.ar7240.v6.3.11.34009.210325.1502")
    assert platform is Platform.XM
    assert chipset == "ar7240"
    assert version.startswith("6.3.11")


def test_parse_firmware_airos8():
    platform, _, version = parse_firmware("WA.qca955x.v8.7.11.46972.230511.1211")
    assert platform is Platform.WA
    assert version.startswith("8.7.11")


@pytest.mark.parametrize("raw", [None, "", "garbage", "XM-ar7240-v6"])
def test_parse_firmware_degrades(raw):
    assert parse_firmware(raw) == (Platform.UNKNOWN, None, None)


def test_generation_split_matches_the_airmax_m_ac_divide():
    for code in ("XM", "XW", "TI", "XN"):
        assert Platform(code).generation == "airos6"
    for code in ("XC", "WA"):
        assert Platform(code).generation == "airos8"
