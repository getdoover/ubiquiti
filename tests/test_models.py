import pytest

from ubiquiti_common.models import Platform, normalise_mac, parse_firmware


@pytest.mark.parametrize(
    "raw",
    ["04:18:D6:AA:BB:CC", "04-18-d6-aa-bb-cc", "0418d6aabbcc", "04 18 d6 aa bb cc"],
)
def test_normalise_mac_accepts_common_shapes(raw):
    assert normalise_mac(raw) == "04:18:d6:aa:bb:cc"


@pytest.mark.parametrize("raw", ["", "not-a-mac", "0418d6aabbc", "0418d6aabbccdd"])
def test_normalise_mac_rejects_junk(raw):
    with pytest.raises(ValueError):
        normalise_mac(raw)


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
