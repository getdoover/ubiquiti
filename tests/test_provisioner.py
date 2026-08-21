"""Reconcile-pass behaviour for the single-target provisioner.

These use a fake radio rather than hardware. The tests that matter most are
:func:`test_non_converging_config_is_bounded` and the parked-target pair — with
no dry run, a config that never converges must stop rebooting the radio rather
than loop forever.
"""

import pytest

from ubiquiti_common import cfg
from ubiquiti_common.airos import Credential, DeviceIdentity
from ubiquiti_common.models import DiscoveredDevice, Platform
from ubiquiti_common.telemetry import Telemetry
from ubiquiti_airmax import provisioner as prov
from ubiquiti_airmax.app_state import TargetState

FACTORY_CFG = "radio.1.freq=0\nwireless.1.ssid=ubnt\nnetconf.3.ip=192.168.1.12\n"
MAC = "28:70:4e:e2:9b:cb"
DEFAULT_OVERRIDES = [prov.Override("radio.1.freq", "{{ freq }}")]


class FakeRadio:
    """Records what was done to it. ``sticky=False`` never accepts a change."""

    def __init__(self, config_text=FACTORY_CFG, sticky=True):
        self.config = cfg.parse(config_text)
        self.sticky = sticky
        self.writes = 0
        self.commits = 0
        self.reboots = 0
        self.staged = None
        self.telemetry_reads = 0


class FakeClient:
    def __init__(self, radio):
        self.radio = radio

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read_telemetry(self, tracker=None, now=None):
        self.radio.telemetry_reads += 1
        return Telemetry(online=True, signal_dbm=-58.0)

    async def read_config(self):
        return dict(self.radio.config)

    async def write_config(self, config):
        self.radio.writes += 1
        self.radio.staged = dict(config)

    async def commit(self):
        self.radio.commits += 1

    async def reboot(self):
        self.radio.reboots += 1
        if self.radio.sticky and self.radio.staged is not None:
            self.radio.config = dict(self.radio.staged)


@pytest.fixture
def radio(monkeypatch):
    fake = FakeRadio()

    async def fake_probe(host, credentials, port=22, connect_timeout=10.0):
        return credentials[0], DeviceIdentity(
            firmware="2WA.ar934x.v8.7.11",
            platform=Platform.WA,
            version="8.7.11",
            hostname="Bullet AC IP67",
        )

    monkeypatch.setattr(prov, "probe", fake_probe)
    monkeypatch.setattr(prov, "AirOSClient", lambda *a, **k: FakeClient(fake))
    return fake


def device(
    mac=MAC, ip="192.168.1.12", firmware="2WA.ar934x.v8.7.11", model="Bullet AC IP67"
):
    return DiscoveredDevice(mac=mac, ip=ip, firmware=firmware, model=model)


def make(
    overrides=None, variables=None, platform="any", expected_model="", **settings_kwargs
):
    settings_kwargs.setdefault("dry_run", False)
    settings_kwargs.setdefault("manage_addresses", False)
    settings_kwargs.setdefault("retry_backoff", 0)
    settings_kwargs.setdefault("reboot_wait", 0)
    settings_kwargs.setdefault("credentials", [Credential("admin", "pw")])
    p = prov.Provisioner(prov.Settings(**settings_kwargs))
    p.load(
        prov.TargetSpec(
            mac=MAC,
            platform=platform,
            overrides=list(DEFAULT_OVERRIDES) if overrides is None else overrides,
            variables={"freq": "5800"} if variables is None else variables,
            expected_model=expected_model,
        )
    )
    return p


# ------------------------------------------------------------------- discovery


async def test_target_not_on_the_wire_stays_pending(radio):
    p = make()
    await p.run_pass([])
    assert p.state is TargetState.PENDING
    assert radio.writes == 0


async def test_unlisted_mac_is_never_touched(radio):
    p = make()
    await p.run_pass([device(mac="aa:bb:cc:dd:ee:ff")])
    assert p.state is TargetState.PENDING
    assert radio.writes == 0


async def test_device_without_ip_is_unreachable(radio):
    p = make()
    await p.run_pass([device(ip=None)])
    assert p.state is TargetState.UNREACHABLE
    assert radio.writes == 0


async def test_bad_mac_in_config_yields_no_target(radio):
    p = prov.Provisioner(prov.Settings(manage_addresses=False))
    p.load(prov.TargetSpec(mac="nonsense", overrides=list(DEFAULT_OVERRIDES)))
    assert p.spec is None
    await p.run_pass([device()])
    assert radio.writes == 0


# ------------------------------------------------------------------ convergence


async def test_already_matching_config_converges_without_writing(radio):
    p = make(variables={"freq": "0"})  # FACTORY_CFG already has freq=0
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED
    assert radio.writes == 0


async def test_no_overrides_converges_without_reading_config(radio):
    p = make(overrides=[])
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED
    assert radio.writes == 0


async def test_successful_push_then_verifies_on_the_next_pass(radio):
    p = make()
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING
    assert (radio.writes, radio.commits, radio.reboots) == (1, 1, 1)
    await p.run_pass([device()])  # sticky radio now matches
    assert p.state is TargetState.CONVERGED
    assert radio.writes == 1


async def test_only_override_keys_are_written(radio):
    p = make()
    await p.run_pass([device()])
    # The overlay must not drop keys the device set for itself.
    assert radio.staged["wireless.1.ssid"] == "ubnt"
    assert radio.staged["netconf.3.ip"] == "192.168.1.12"
    assert radio.staged["radio.1.freq"] == "5800"


# ------------------------------------------------------------------------ safety


async def test_dry_run_never_writes(radio):
    p = make(dry_run=True)
    await p.run_pass([device()])
    assert p.state is TargetState.WOULD_APPLY
    assert radio.writes == 0
    assert "radio.1.freq" in p.record.last_diff


async def test_non_converging_config_is_bounded(radio):
    """The reboot-loop guard: attempts are capped and the target is parked."""
    radio.sticky = False  # radio never accepts the change
    p = make(max_attempts=3, failed_retry_after=0)
    for _ in range(10):
        await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 3, "must stop pushing once max_attempts is reached"
    assert radio.reboots == 3


async def test_parked_target_stays_parked_when_cooldown_disabled(radio):
    radio.sticky = False
    p = make(max_attempts=1, failed_retry_after=0)
    for _ in range(5):
        await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    writes = radio.writes
    await p.run_pass([device()])
    assert radio.writes == writes, "failed_retry_after=0 means never retry"


async def test_parked_target_retries_after_cooldown(radio):
    radio.sticky = False
    p = make(max_attempts=1, failed_retry_after=1)
    for _ in range(4):
        await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    writes = radio.writes

    # Cooldown elapsed (last_attempt pushed into the past), and the radio now
    # accepts changes, so it should try again and converge.
    p.record.last_attempt -= 10
    radio.sticky = True
    await p.run_pass([device()])
    assert radio.writes == writes + 1


async def test_intent_change_revives_a_parked_radio(radio):
    """Editing the config must give a parked target a fresh set of tries."""
    radio.sticky = False
    p = make(max_attempts=1, failed_retry_after=0)
    for _ in range(3):
        await p.run_pass([device()])
    assert p.state is TargetState.FAILED

    radio.sticky = True
    p.load(
        prov.TargetSpec(
            mac=MAC,
            overrides=[prov.Override("radio.1.freq", "{{ freq }}")],
            variables={"freq": "5200"},  # different intent
        )
    )
    assert p.state is TargetState.PENDING
    assert p.record.attempts == 0
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING


async def test_identical_config_reload_does_not_revive_a_parked_radio(radio):
    """A config re-push with no actual change must not reset the attempt count.

    Otherwise any config sync — or a restart that reloads the same config —
    silently becomes an unbounded retry, which is exactly what the attempt
    ceiling exists to prevent.
    """
    radio.sticky = False
    p = make(max_attempts=1, failed_retry_after=0)
    for _ in range(3):
        await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    writes = radio.writes

    # Same MAC, same overrides, same variables -> same fingerprint.
    p.load(
        prov.TargetSpec(
            mac=MAC,
            overrides=list(DEFAULT_OVERRIDES),
            variables={"freq": "5800"},
        )
    )
    assert p.state is TargetState.FAILED, "identical config must not unpark"
    radio.sticky = True
    await p.run_pass([device()])
    assert radio.writes == writes, "must not push again"


async def test_intent_fingerprint_is_stable_for_equivalent_config():
    a = prov.intent_fingerprint([prov.Override("a", "1")], {"x": "y"}, "WA")
    b = prov.intent_fingerprint([prov.Override("a", "1")], {"x": "y"}, "WA")
    c = prov.intent_fingerprint([prov.Override("a", "2")], {"x": "y"}, "WA")
    d = prov.intent_fingerprint([prov.Override("a", "1")], {"x": "y"}, "XM")
    assert a == b
    assert a != c and a != d


async def test_platform_mismatch_is_refused(radio):
    p = make(platform="XM")  # airOS 6 overrides, airOS 8 radio
    await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0
    assert "platform" in p.record.message


async def test_expected_model_guard_is_refused(radio):
    p = make(expected_model="Rocket M5")
    await p.run_pass([device(model="Bullet AC IP67")])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0


async def test_expected_model_guard_passes_on_substring(radio):
    p = make(expected_model="Bullet AC")
    await p.run_pass([device(model="Bullet AC IP67")])
    assert p.state is TargetState.APPLYING


async def test_invalid_key_fails_without_writing(radio):
    p = make(overrides=[prov.Override("radio.1 freq", "5800")])
    await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0


async def test_undefined_template_variable_fails_loudly(radio):
    p = make(overrides=[prov.Override("radio.1.freq", "{{ nope }}")], variables={})
    await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0


async def test_ssh_failure_marks_unreachable_not_failed(radio, monkeypatch):
    from ubiquiti_common.airos import AirOSError

    async def boom(*a, **k):
        raise AirOSError("auth failed")

    monkeypatch.setattr(prov, "probe", boom)
    p = make()
    await p.run_pass([device()])
    assert p.state is TargetState.UNREACHABLE
    assert radio.writes == 0


async def test_unhandled_error_does_not_kill_the_pass(radio, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(prov, "probe", boom)
    p = make()
    result = await p.run_pass([device()])  # must not raise
    assert p.state is TargetState.UNREACHABLE
    assert "something unexpected" in result


# --------------------------------------------------------------------- telemetry


async def test_telemetry_is_read_even_when_config_matches(radio):
    """Telemetry is why the UI exists — it must publish with no config work."""
    p = make(variables={"freq": "0"})
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED
    assert radio.telemetry_reads == 1
    assert p.telemetry.online is True


async def test_telemetry_reports_offline_when_not_discovered(radio):
    p = make()
    await p.run_pass([])
    assert p.telemetry is not None
    assert p.telemetry.online is False


async def test_telemetry_reports_offline_on_ssh_failure(radio, monkeypatch):
    from ubiquiti_common.airos import AirOSError

    async def boom(*a, **k):
        raise AirOSError("auth failed")

    monkeypatch.setattr(prov, "probe", boom)
    p = make()
    await p.run_pass([device()])
    assert p.telemetry.online is False


# --------------------------------------------------------------- overlay render


def test_render_overlay_renders_each_value_independently():
    overlay = prov.render_overlay(
        [
            prov.Override("radio.1.freq", "{{ freq }}"),
            prov.Override("wireless.1.ssid", "{{ ssid }}"),
            prov.Override("wireless.1.security.type", "none"),
        ],
        {"freq": "5800", "ssid": "SPAN-LINK"},
    )
    assert overlay == {
        "radio.1.freq": "5800",
        "wireless.1.ssid": "SPAN-LINK",
        "wireless.1.security.type": "none",
    }


def test_render_overlay_allows_an_empty_value():
    assert prov.render_overlay([prov.Override("unms.uri", "")], {}) == {"unms.uri": ""}


def test_render_overlay_skips_blank_keys():
    assert prov.render_overlay([prov.Override("   ", "x")], {}) == {}


def test_render_overlay_strips_surrounding_whitespace_from_keys():
    assert prov.render_overlay([prov.Override("  radio.1.freq  ", "5800")], {}) == {
        "radio.1.freq": "5800"
    }


@pytest.mark.parametrize(
    "key",
    [
        "radio.1 freq",  # space would break the key=value line
        "radio.1.freq=x",  # embedded '=' would render two keys
        "radio.1.freq\nother",  # newline injection
        "radio/1/freq",
    ],
)
def test_render_overlay_refuses_invalid_keys(key):
    with pytest.raises(prov.OverlayError, match="invalid airOS config key"):
        prov.render_overlay([prov.Override(key, "5800")], {})


def test_render_overlay_reports_which_key_failed_to_render():
    with pytest.raises(prov.OverlayError, match="wireless.1.ssid"):
        prov.render_overlay([prov.Override("wireless.1.ssid", "{{ missing }}")], {})


# ------------------------------------------------- reboot window (realistic)


async def test_applying_survives_the_radio_being_unreachable_mid_reboot(
    radio, monkeypatch
):
    """The realistic reboot path: the radio drops off SSH while it restarts.

    The earlier happy-path test is optimistic — a real radio is unreachable for
    a minute after ``reboot``. If that dropped the APPLYING state, the next pass
    would treat the push as fresh drift and push again, burning attempts for no
    reason.
    """
    p = make(reboot_wait=600)  # long window, so we are inside it throughout
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING
    assert radio.writes == 1

    from ubiquiti_common.airos import AirOSError

    async def unreachable(*a, **k):
        raise AirOSError("connection refused")

    real_probe = prov.probe
    monkeypatch.setattr(prov, "probe", unreachable)
    for _ in range(3):
        await p.run_pass([device()])
    assert p.state is TargetState.APPLYING, "must hold APPLYING through the reboot"
    assert radio.writes == 1, "must not re-push while the radio is rebooting"
    assert p.telemetry.online is False

    # Radio comes back, and the push is verified rather than repeated.
    monkeypatch.setattr(prov, "probe", real_probe)
    p.settings.reboot_wait = 0
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED
    assert radio.writes == 1


async def test_unreachable_outside_the_reboot_window_is_reported(radio, monkeypatch):
    """Outside a reboot, an unreachable radio is a real problem worth showing."""
    from ubiquiti_common.airos import AirOSError

    async def unreachable(*a, **k):
        raise AirOSError("connection refused")

    monkeypatch.setattr(prov, "probe", unreachable)
    p = make()
    await p.run_pass([device()])
    assert p.state is TargetState.UNREACHABLE


# ----------------------------------------------------------- address holder


async def test_address_holder_adds_once_and_reuses(monkeypatch):
    """Adding/removing per pass would be thousands of netlink cycles a day."""
    from ubiquiti_common import netif

    added, removed, reachable = [], [], {"value": False}

    async def is_reachable(interface, ip):
        return reachable["value"]

    async def add_address(interface, cidr, label="doover-prov"):
        added.append(cidr)
        reachable["value"] = True  # the address we just added makes it routable

    async def remove_address(interface, cidr):
        removed.append(cidr)

    monkeypatch.setattr(netif, "is_reachable", is_reachable)
    monkeypatch.setattr(netif, "add_address", add_address)
    monkeypatch.setattr(netif, "remove_address", remove_address)

    holder = prov.AddressHolder()
    for _ in range(5):
        await holder.ensure("eth0", "192.168.1.20")
    assert added == ["192.168.1.254/24"], "must add exactly once"
    assert removed == []

    await holder.release()
    assert removed == added


async def test_address_holder_swaps_when_the_radio_moves(monkeypatch):
    from ubiquiti_common import netif

    added, removed = [], []

    async def never_reachable(interface, ip):
        return False

    async def add_address(interface, cidr, label="doover-prov"):
        added.append(cidr)

    async def remove_address(interface, cidr):
        removed.append(cidr)

    monkeypatch.setattr(netif, "is_reachable", never_reachable)
    monkeypatch.setattr(netif, "add_address", add_address)
    monkeypatch.setattr(netif, "remove_address", remove_address)

    holder = prov.AddressHolder()
    await holder.ensure("eth0", "192.168.1.20")
    await holder.ensure("eth0", "10.4.5.6")
    assert added == ["192.168.1.254/24", "10.4.5.254/24"]
    assert removed == ["192.168.1.254/24"], "old address must be dropped"
