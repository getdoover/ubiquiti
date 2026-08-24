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
DEFAULT_OVERRIDES = [prov.Override("radio.1.freq", "5800")]


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
        self.passwd_calls = []


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

    async def set_password(self, username, password):
        self.radio.passwd_calls.append((username, password))
        # A real radio hashes it with a fresh salt; the value is opaque to us.
        return "$1$fakesalt$" + str(len(self.radio.passwd_calls))

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


def make(overrides=None, expected_model="", **settings_kwargs):
    settings_kwargs.setdefault("dry_run", False)
    settings_kwargs.setdefault("manage_addresses", False)
    settings_kwargs.setdefault("retry_backoff", 0)
    settings_kwargs.setdefault("reboot_wait", 0)
    # Off by default here so the write path is exercised; the delay has its own
    # tests below.
    settings_kwargs.setdefault("deployment_delay", 0)
    settings_kwargs.setdefault("credentials", [Credential("admin", "pw")])
    p = prov.Provisioner(prov.Settings(**settings_kwargs))
    p.load(
        prov.TargetSpec(
            mac=MAC,
            overrides=list(DEFAULT_OVERRIDES) if overrides is None else overrides,
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
    p = make(overrides=[prov.Override("radio.1.freq", "0")])  # already 0
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
            overrides=[prov.Override("radio.1.freq", "5200")],  # different intent
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

    # Same MAC, same overrides -> same fingerprint.
    p.load(
        prov.TargetSpec(
            mac=MAC,
            overrides=list(DEFAULT_OVERRIDES),
        )
    )
    assert p.state is TargetState.FAILED, "identical config must not unpark"
    radio.sticky = True
    await p.run_pass([device()])
    assert radio.writes == writes, "must not push again"


async def test_intent_fingerprint_is_stable_for_equivalent_config():
    a = prov.intent_fingerprint([prov.Override("a", "1")])
    b = prov.intent_fingerprint([prov.Override("a", "1")])
    c = prov.intent_fingerprint([prov.Override("a", "2")])
    d = prov.intent_fingerprint([prov.Override("b", "1")])
    assert a == b
    assert a != c, "a changed override value must change the fingerprint"
    assert a != d, "a changed override key must change the fingerprint"


async def test_platform_is_reported_but_no_longer_guarded(radio):
    """Platform is discovered and published, but does not gate provisioning.

    With one install per radio the MAC already pins the device, so the platform
    guard was redundant; `expected_model` remains for the MAC-typo case.
    """
    p = make()
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING, "a WA radio must not be refused"
    assert p.record.platform == "WA", "platform is still recorded for the UI"


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
    p = make(overrides=[prov.Override("radio.1.freq", "0")])
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


def test_build_overlay_uses_values_literally():
    overlay = prov.build_overlay(
        [
            prov.Override("radio.1.freq", "5800"),
            prov.Override("wireless.1.ssid", "SPAN-LINK"),
            prov.Override("wireless.1.security.type", "none"),
        ]
    )
    assert overlay == {
        "radio.1.freq": "5800",
        "wireless.1.ssid": "SPAN-LINK",
        "wireless.1.security.type": "none",
    }


def test_build_overlay_allows_an_empty_value():
    assert prov.build_overlay([prov.Override("unms.uri", "")]) == {"unms.uri": ""}


def test_build_overlay_skips_blank_keys():
    assert prov.build_overlay([prov.Override("   ", "x")]) == {}


def test_build_overlay_strips_surrounding_whitespace_from_keys():
    assert prov.build_overlay([prov.Override("  radio.1.freq  ", "5800")]) == {
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
def test_build_overlay_refuses_invalid_keys(key):
    with pytest.raises(prov.OverlayError, match="invalid airOS config key"):
        prov.build_overlay([prov.Override(key, "5800")])


@pytest.mark.parametrize(
    "value", ["{{ ip }}", "{{ssid}}", "prefix-{{ x }}", "}} broken"]
)
def test_build_overlay_refuses_leftover_template_placeholders(value):
    """Nothing renders these any more, so writing one verbatim would flash
    garbage — e.g. netconf.3.ip={{ ip }} costs the radio its address."""
    with pytest.raises(prov.OverlayError, match="template placeholder"):
        prov.build_overlay([prov.Override("netconf.3.ip", value)])


async def test_leftover_placeholder_fails_the_target_without_writing(radio):
    p = make(overrides=[prov.Override("netconf.3.ip", "{{ ip }}")])
    await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0


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


# ------------------------------------------------------------- deployment delay
#
# A fleet-wide deploy must be able to reach every radio before any link drops:
# on a chained network, reconfiguring an uplink first cuts off the stations that
# have not received their config yet.


async def test_delay_holds_the_write_and_reports_the_wait(radio):
    p = make(deployment_delay=300)
    result = await p.run_pass([device()])
    assert p.state is TargetState.DRIFTED
    assert radio.writes == 0
    assert "holding" in result
    assert "holding" in p.record.message


async def test_delay_does_not_consume_an_attempt(radio):
    """Waiting is not a failed try — otherwise a long delay would exhaust the
    attempt ceiling before ever touching the radio."""
    p = make(deployment_delay=300)
    for _ in range(5):
        await p.run_pass([device()])
    assert p.record.attempts == 0
    assert p.state is TargetState.DRIFTED


async def test_write_proceeds_once_the_delay_has_elapsed(radio):
    p = make(deployment_delay=300)
    await p.run_pass([device()])
    assert radio.writes == 0

    p.record.intent_since -= 400  # delay window has passed
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING
    assert radio.writes == 1


async def test_telemetry_and_diff_still_publish_while_holding(radio):
    """The whole point: the operator watches the links stay up and sees what is
    pending, while nothing is written yet."""
    p = make(deployment_delay=300)
    await p.run_pass([device()])
    assert p.telemetry.online is True
    assert "radio.1.freq" in p.record.last_diff
    assert radio.writes == 0


async def test_zero_delay_applies_immediately(radio):
    p = make(deployment_delay=0)
    await p.run_pass([device()])
    assert p.state is TargetState.APPLYING
    assert radio.writes == 1


async def test_dry_run_wins_over_the_delay(radio):
    """Dry run is the stronger statement — no need to wait to write nothing."""
    p = make(dry_run=True, deployment_delay=300)
    await p.run_pass([device()])
    assert p.state is TargetState.WOULD_APPLY
    assert radio.writes == 0


async def test_converged_radio_is_not_held(radio):
    """Nothing to apply means nothing to wait for."""
    p = make(overrides=[prov.Override("radio.1.freq", "0")], deployment_delay=300)
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED


async def test_editing_the_config_restarts_the_delay(radio):
    """A live config edit must re-arm the window, not just a container restart —
    otherwise a mid-flight edit applies instantly on the next pass."""
    p = make(deployment_delay=300)
    await p.run_pass([device()])
    p.record.intent_since -= 400  # window elapsed for the old intent

    p.load(prov.TargetSpec(mac=MAC, overrides=[prov.Override("radio.1.freq", "5200")]))
    await p.run_pass([device()])
    assert radio.writes == 0, "new intent must serve a fresh delay"
    assert p.state is TargetState.DRIFTED


# ------------------------------------------------------ password hash guard
#
# airOS stores users.N.password as a crypt hash and compares crypt(entered)
# against it. Verified against a real radio: regenerating its stored value with
# `openssl passwd -1 -salt EC25aZzE 'dredge101!'` reproduced it byte for byte.
#
# So a passphrase written into that key can never match anything, and the radio
# is locked out until someone presses its reset button. On a mast at Porgera that
# is a site visit, so this is refused rather than written.


@pytest.mark.parametrize(
    "value",
    [
        "dredge101!",  # the actual passphrase — the dangerous case
        "",  # empty
        "hunter2",
        "$1$onlytwoparts",  # malformed
        "notahash$1$x$y",  # hash-ish but not anchored
    ],
)
def test_password_override_refuses_anything_but_a_crypt_hash(value):
    with pytest.raises(prov.OverlayError, match="crypt hash"):
        prov.build_overlay([prov.Override("users.1.password", value)])


@pytest.mark.parametrize(
    "value",
    [
        "$1$EC25aZzE$xu1o7GxJU2vdvIE/iEP4/0",  # real value off a radio
        "$1$tL963iDU$SXu0h02ZZYfnoZcPkIlK21",  # factory value
        "$6$somesalt$somelongdigestvalue",  # sha512-crypt, also acceptable
    ],
)
def test_password_override_accepts_a_crypt_hash(value):
    assert prov.build_overlay([prov.Override("users.1.password", value)]) == {
        "users.1.password": value
    }


def test_guard_applies_to_any_user_index():
    with pytest.raises(prov.OverlayError, match="crypt hash"):
        prov.build_overlay([prov.Override("users.2.password", "plaintext")])


def test_guard_does_not_touch_other_keys():
    """Only the password field is hash-only; PSKs and communities are plaintext."""
    overlay = prov.build_overlay(
        [
            prov.Override("aaa.1.wpa.psk", "a-real-passphrase"),
            prov.Override("snmp.community", "public"),
            prov.Override("users.1.name", "admin"),
        ]
    )
    assert overlay["aaa.1.wpa.psk"] == "a-real-passphrase"
    assert overlay["users.1.name"] == "admin"


async def test_plaintext_password_fails_the_target_without_writing(radio):
    p = make(overrides=[prov.Override("users.1.password", "dredge101!")])
    await p.run_pass([device()])
    assert p.state is TargetState.FAILED
    assert radio.writes == 0, "must never reach the radio"


# ------------------------------------------------------- credential enforcement
#
# Verified on real hardware (Pump 8 AP, 2WA.v8.7.11): BusyBox `passwd` writes only
# to /etc/passwd, which is tmpfs on a read-only squashfs root — so the radio's own
# hash must be persisted to users.N.password or the reboot erases it. The radio
# does the hashing; we only store it.
#
# probe() is the convergence signal: it reports which credential authenticated, so
# no hashing or config diff is needed to decide, and enforcement can only fire
# when some credential already worked — the app cannot lock itself out.

ENFORCE = Credential("admin", "newpass")


def make_enforcing(**kw):
    kw.setdefault("credentials", [Credential("ubnt", "ubnt")])
    kw.setdefault("enforce_credential", ENFORCE)
    return make(**kw)


async def test_wrong_login_is_enforced_at_push_time(radio):
    p = make_enforcing()
    await p.run_pass([device()])
    assert radio.passwd_calls == [("ubnt", "newpass")], "radio must do the hashing"
    assert radio.staged["users.1.name"] == "admin"
    assert radio.staged["users.1.password"].startswith("$1$"), "hash must be persisted"
    assert radio.reboots == 1
    assert p.state is TargetState.APPLYING


async def test_correct_login_is_left_alone(radio):
    """probe() authenticating with the enforced credential is the whole check."""
    p = make(
        credentials=[ENFORCE],
        enforce_credential=ENFORCE,
        overrides=[prov.Override("radio.1.freq", "0")],
    )  # config already matches
    await p.run_pass([device()])
    assert p.state is TargetState.CONVERGED
    assert radio.passwd_calls == []
    assert radio.writes == 0


async def test_wrong_login_alone_is_enough_to_act(radio):
    """Config matching must not mask a wrong login."""
    p = make_enforcing(overrides=[prov.Override("radio.1.freq", "0")])
    await p.run_pass([device()])
    assert p.state is not TargetState.CONVERGED
    assert radio.passwd_calls == [("ubnt", "newpass")]


async def test_dry_run_never_touches_the_password(radio):
    """passwd changes the live login immediately, so it must not run on a pass
    that writes nothing — that leaves live and flash disagreeing."""
    p = make_enforcing(dry_run=True)
    await p.run_pass([device()])
    assert radio.passwd_calls == []
    assert radio.writes == 0
    assert p.state is TargetState.WOULD_APPLY
    assert "login" in p.record.last_diff


async def test_deployment_delay_never_touches_the_password(radio):
    p = make_enforcing(deployment_delay=300)
    await p.run_pass([device()])
    assert radio.passwd_calls == [], "must not change the live login while holding"
    assert radio.writes == 0
    assert p.state is TargetState.DRIFTED


async def test_no_enforced_credential_means_no_password_work(radio):
    p = make(enforce_credential=None)
    await p.run_pass([device()])
    assert radio.passwd_calls == []


async def test_enforcement_is_idempotent_across_passes(radio):
    """After the push the radio answers on the enforced credential, so the next
    pass must do nothing."""
    p = make_enforcing()
    await p.run_pass([device()])
    assert radio.passwd_calls == [("ubnt", "newpass")]
    # The radio now authenticates as admin/newpass.
    p.settings.credentials = [ENFORCE]
    await p.run_pass([device()])
    assert len(radio.passwd_calls) == 1, "must not re-set an already-correct login"
