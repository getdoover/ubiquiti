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
    # The declared-uplink escape hatch for the network overview. Optional, and
    # empty by default: the link is normally read off the radio itself.
    assert props["uplink_mac"]["default"] == ""
    assert "uplink_mac" not in schema["required"]


def test_topology_tags_are_published():
    """The network overview joins every edge on these.

    They are a hard dependency of the overview app, not a nicety: an install
    that does not publish `radio_mac` has no identity to draw, and one that does
    not publish `stations_json` cannot label the AP side of its links.
    """
    from pydoover.tags import Tag
    from ubiquiti_airmax.app_tags import AirMaxTags

    for name in ("radio_mac", "uplink_mac", "stations_json", "ap_mac"):
        assert isinstance(getattr(AirMaxTags, name, None), Tag), (
            f"{name} is not a published tag"
        )


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


# ---------------------------------------------------- ubiquiti_network_overview
#
# The processor app. It ships as a package.zip rather than an image, so there is
# no container smoke test to catch a broken import — these stand in for it.


def test_overview_imports():
    import ubiquiti_network_overview  # noqa: F401
    from ubiquiti_network_overview import app_config, app_ui, application  # noqa: F401


def test_overview_exposes_a_lambda_handler():
    """`lambda_config.Handler` in doover_config.json names this function."""
    import json

    import ubiquiti_network_overview

    assert callable(ubiquiti_network_overview.handler)

    config = json.loads(_repo_root().joinpath("doover_config.json").read_text())
    handler = config["ubiquiti_network_overview"]["lambda_config"]["Handler"]
    module, _, attr = handler.rpartition(".")
    assert module == "ubiquiti_network_overview", (
        "the handler must name the package as build.sh vendors it — a `src.` "
        "prefix means the zip carries a second copy of these modules"
    )
    assert attr == "handler"


def test_overview_config_grants_device_permission():
    """Without this the dashboard has no devices to read."""
    from ubiquiti_network_overview.app_config import NetworkOverviewConfig

    props = NetworkOverviewConfig.to_schema()["properties"]
    assert "dv_proc_extended_permissions" in props


def test_overview_ui_module_matches_the_widget_bundle():
    """The UI schema's `module` must be a module rsbuild actually exposes.

    A mismatch does not fail any build: the app publishes, the bundle loads, and
    the panel renders empty. Pinning both ends against each other is the only
    thing that catches a rename.
    """
    import json
    import re

    config = json.loads(_repo_root().joinpath("doover_config.json").read_text())
    child = config["ubiquiti_network_overview"]["ui_schema"]["children"][
        "UbiquitiNetwork"
    ]
    assert child["type"] == "uiRemoteComponent"

    rsbuild = _repo_root().joinpath("widget/rsbuild.config.ts").read_text()
    exposed = set(re.findall(r"'(\./[A-Za-z0-9_]+)':", rsbuild))
    assert child["module"] in exposed, (
        f"{child['module']} is not exposed by rsbuild.config.ts ({exposed})"
    )

    scope = re.search(r"name:\s*'([A-Za-z0-9_]+)'", rsbuild).group(1)
    assert child["scope"] == scope


def test_overview_ui_passes_its_app_key_to_the_widget():
    """The widget reads `app_key` off the uiElement prop, not from route params.

    DEVICE_MAP and this dashboard's own config both live under that key in the
    agent's `deployment_config`. Without it the widget has nothing to look up and
    renders as though no devices were ever granted — which reads as a
    configuration mistake rather than the missing prop it actually is.
    """
    import json

    config = json.loads(_repo_root().joinpath("doover_config.json").read_text())
    child = config["ubiquiti_network_overview"]["ui_schema"]["children"][
        "UbiquitiNetwork"
    ]
    assert child.get("app_key") == "$config.app().APP_KEY"


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


def test_overview_app_block_pins_an_identifier():
    """A PRO app block must carry `id`, or publishing 404s.

    `doover app publish` PATCHes /applications/{id}/ when an id is pinned, and
    otherwise POSTs an upsert the control plane resolves from the payload's
    identifiers. `ubiquiti_airmax` gets away without an id because it carries a
    `key`; this app has neither by default, and the failure is a bare
    `HTTP 404 ... {"detail":"No Application matches the given query."}` from CI
    that says nothing about the cause.

    The value is the app record created on the control plane — see
    `doover app get ubiquiti_network_overview`. Only the presence is asserted:
    the app being recreated should not fail the suite.
    """
    import json

    config = json.loads(_repo_root().joinpath("doover_config.json").read_text())
    app = config["ubiquiti_network_overview"]
    assert app.get("id") or app.get("key"), (
        "pin `id` (or `key`) in the ubiquiti_network_overview block — without "
        "one, `doover app publish` cannot resolve the existing application"
    )
