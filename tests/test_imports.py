"""Import smoke tests — these are what CI relies on to catch a broken package."""


def test_common_imports():
    import ubiquiti_common  # noqa: F401
    from ubiquiti_common import airos, cfg, cli, discovery, models, netif  # noqa: F401


def test_app_imports():
    import ubiquiti_airmax  # noqa: F401
    from ubiquiti_airmax import app_config, app_state, app_tags, app_ui, provisioner  # noqa: F401


def test_config_schema_exports():
    from ubiquiti_airmax.app_config import AirMaxConfig

    schema = AirMaxConfig.to_schema()
    props = schema["properties"]
    # The safety-critical fields must survive any schema refactor.
    for key in (
        "mac",
        "dry_run",
        "max_attempts",
        "failed_retry_after",
        "interface",
        "overrides",
    ):
        assert key in props, f"{key} missing from exported config schema"
    # Deliberately off: an install exists to converge a radio, and leaving it on
    # by default meant every new install silently did nothing.
    assert props["dry_run"]["default"] is False
    # Ordered first on purpose — it is the switch that decides whether the app
    # writes to a radio at all.
    assert next(iter(props)) == "dry_run", "Dry Run must be the first config field"
    assert "variables" not in props, "template variables were removed"
    # The radio this install manages is the one thing an operator must supply.
    assert schema["required"] == ["mac"]


def test_ui_has_no_provisioning_controls():
    """Provisioning is autonomous; a button would only ask for what it already did."""
    from ubiquiti_airmax.app_ui import AirMaxUI

    schema = AirMaxUI(None, None, None).to_schema()

    def walk(node):
        for name, child in (node.get("children") or {}).items():
            yield name, child.get("type")
            yield from walk(child)

    types = {kind for _, kind in walk(schema)}
    assert "uiButton" not in types, "provisioning must not be button-triggered"
    assert "uiAction" not in types
    # But the telemetry the UI exists for must be there.
    names = {name for name, _ in walk(schema)}
    for expected in ("snr", "link", "throughput", "peers", "device"):
        assert expected in names, f"{expected} missing from the UI"


def test_setup_does_not_guard_the_provisioning_interface():
    """No default-route check anywhere in startup.

    Adding a secondary address does not disturb an existing default route, and an
    earlier guard here crash-looped a healthy install on station-1, whose br0
    carries both the uplink and the radio's subnet. `manage_addresses` is the
    off-switch. The interface element itself is still used, for discovery
    broadcasts and for the helper address.
    """
    import inspect

    from ubiquiti_airmax.application import AirMaxApplication

    source = inspect.getsource(AirMaxApplication.setup)
    for gone in ("assert_safe_interface", "carries_default_route"):
        assert gone not in source, f"setup() must not call {gone}"
    assert "self.config.interface.value" in source, (
        "the interface config element is still used"
    )


# ------------------------------------------------------- layered override sets


def test_both_override_layers_exist_as_separate_keys():
    """Two distinct keys, not one array.

    Doover's `deep_merge` replaces arrays rather than combining them, so a config
    profile writing `overrides` would wipe the per-install list. Separate keys let
    a profile own the shared layer and the install own its own.
    """
    from ubiquiti_airmax.app_config import AirMaxConfig

    props = AirMaxConfig.to_schema()["properties"]
    assert "overrides" in props
    assert "profile_overrides" in props
    for key in ("overrides", "profile_overrides"):
        item = props[key]["items"]["properties"]
        assert set(item) == {"key", "value"}, f"{key} items must be key/value pairs"


def test_profile_overrides_renders_last():
    """It is the shared layer, so it sits at the bottom of the form."""
    from ubiquiti_airmax.app_config import AirMaxConfig

    order = list(AirMaxConfig.to_schema()["properties"])
    assert order[0] == "dry_run"
    assert order[-1] == "profile_overrides"


def test_install_overrides_win_over_profile_overrides():
    """Precedence is what makes the split useful: a profile states the shared
    intent, and one install can deviate without editing the profile."""
    from ubiquiti_airmax.provisioner import Override, build_overlay

    profile = [
        Override("radio.1.mode", "master"),
        Override("wireless.1.ssid", "PORGERA-AP"),
        Override("radio.1.txpower", "22"),
    ]
    install = [Override("radio.1.txpower", "10")]  # this radio runs quieter

    overlay = build_overlay(profile + install)
    assert overlay["radio.1.txpower"] == "10", "per-install layer must win"
    assert overlay["wireless.1.ssid"] == "PORGERA-AP", "profile layer still applies"
    assert overlay["radio.1.mode"] == "master"
    assert len(overlay) == 3, "a shadowed key must not appear twice"


def test_either_layer_alone_still_works():
    from ubiquiti_airmax.provisioner import Override, build_overlay

    assert build_overlay([Override("a.b", "1")] + []) == {"a.b": "1"}
    assert build_overlay([] + [Override("a.b", "2")]) == {"a.b": "2"}
    assert build_overlay([]) == {}
