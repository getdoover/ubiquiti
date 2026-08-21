"""airOS config parse / merge / diff."""

import pytest

from ubiquiti_common import cfg

FACTORY = """\
radio.1.devname=ath0
radio.1.freq=0
radio.1.txpower=20
netconf.1.devname=br0
netconf.1.ip=192.168.1.20
netconf.1.netmask=255.255.255.0
users.1.name=ubnt
users.1.password=$1$salt$factoryhash
wireless.1.ssid=ubnt
"""


def test_parse_ignores_blank_and_malformed_lines():
    parsed = cfg.parse("a=1\n\n   \nnot-a-pair\n# comment\nb=2\n")
    assert parsed == {"a": "1", "b": "2"}


def test_parse_keeps_empty_values():
    assert cfg.parse("radio.1.ssid=\n") == {"radio.1.ssid": ""}


def test_last_value_wins():
    assert cfg.parse("a=1\na=2\n") == {"a": "2"}


def test_render_roundtrips():
    text = cfg.render(cfg.parse(FACTORY))
    assert cfg.parse(text) == cfg.parse(FACTORY)


def test_merge_preserves_position_of_existing_keys():
    base = cfg.parse("a=1\nb=2\nc=3\n")
    merged = cfg.merge(base, {"b": "22"})
    assert list(merged) == ["a", "b", "c"]
    assert merged["b"] == "22"


def test_merge_appends_new_keys_sorted_for_stability():
    base = cfg.parse("a=1\n")
    merged = cfg.merge(base, {"z": "26", "m": "13"})
    assert list(merged) == ["a", "m", "z"]


def test_merge_does_not_mutate_base():
    base = cfg.parse("a=1\n")
    cfg.merge(base, {"a": "2"})
    assert base["a"] == "1"


def test_diff_reports_only_desired_keys():
    current = cfg.parse(FACTORY)
    desired = {"radio.1.freq": "5800", "wireless.1.ssid": "ubnt"}
    changes = cfg.diff(current, desired)
    # ssid already matches, and nothing else in FACTORY is proposed for removal.
    assert changes == {"radio.1.freq": ("0", "5800")}


def test_diff_marks_new_keys_with_none():
    changes = cfg.diff({}, {"aaa.bbb": "1"})
    assert changes == {"aaa.bbb": (None, "1")}


def test_diff_honours_exclusions():
    current = cfg.parse(FACTORY)
    desired = {"users.1.password": "plaintext", "radio.1.freq": "5800"}
    changes = cfg.diff(current, desired, exclude=cfg.DEFAULT_VERIFY_EXCLUDE)
    assert "users.1.password" not in changes
    assert "radio.1.freq" in changes


@pytest.mark.parametrize(
    "key,expected",
    [
        ("users.1.password", True),
        ("wireless.1.wpa.psk", True),
        ("aaa.1.psk", True),
        ("radio.1.freq", False),
    ],
)
def test_is_excluded(key, expected):
    assert cfg.is_excluded(key, cfg.DEFAULT_VERIFY_EXCLUDE) is expected


def test_format_diff_is_readable():
    out = cfg.format_diff({"a": ("1", "2"), "b": (None, "3")})
    assert "~ a: 1 -> 2" in out
    assert "+ b=3" in out
    assert cfg.format_diff({}) == "(no changes)"
