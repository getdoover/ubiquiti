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
    assert props["dry_run"]["default"] is True, "dry run must default to on"
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


def test_setup_does_not_hard_fail_on_a_shared_provisioning_interface():
    """A Doovit that bridges LAN and uplink onto one interface must still start.

    station-1's `br0` is 192.168.1.10/24, carries the default route, and has the
    target radio already reachable at .12 — nothing needs adding, so nothing is at
    risk. Calling `assert_safe_interface` from `setup()` crash-looped a perfectly
    healthy install (deployed image v2, 3 restarts). The refusal belongs in
    `netif.reachable()`, at the moment an address would actually be added.

    This has regressed twice, so it is pinned at the source level.
    """
    import inspect

    from ubiquiti_airmax.application import AirMaxApplication

    source = inspect.getsource(AirMaxApplication.setup)
    assert "assert_safe_interface" not in source, (
        "setup() must not call assert_safe_interface — it makes a shared "
        "LAN/uplink interface fatal and crash-loops the app on startup"
    )
    assert "carries_default_route" in source, (
        "setup() should warn via carries_default_route() instead"
    )


def test_reachable_still_refuses_where_it_matters():
    """The guard must remain fatal at the point an address would be added."""
    import inspect

    from ubiquiti_common import netif

    assert "assert_safe_interface" in inspect.getsource(netif.reachable), (
        "netif.reachable() must refuse before adding an address to the "
        "default-route interface"
    )
