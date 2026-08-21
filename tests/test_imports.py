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
