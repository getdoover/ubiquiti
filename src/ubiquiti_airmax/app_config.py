"""Config schema for the Ubiquiti AirMax app.

One install manages one radio. The MAC identifies it, the overrides say what it
should look like, and the variables let a Solution share one override set across
many installs while each supplies its own site values.
"""

from pathlib import Path

from pydoover import config

# Keys airOS rewrites after we set them (hashes, obscured secrets). Comparing
# them would leave the radio permanently unconverged, which with autonomous
# provisioning means a reboot loop. They are still pushed, just not verified.
DEFAULT_VERIFY_EXCLUDE = [
    "users.*.password",
    "*.psk",
    "*.wpa.psk",
    "*.key",
    "snmp.*.community",
]


class Credential(config.Object):
    """One SSH login to try.

    Radios move between factory and configured credentials as they are reset and
    reprovisioned, so every credential is tried in order rather than assuming
    which one applies.
    """

    username = config.String("Username", name="username", default="ubnt")
    password = config.String("Password", name="password", default="ubnt")
    label = config.String(
        "Label",
        name="label",
        default="",
        description="Optional note, e.g. 'factory default' or 'site standard'.",
    )


class Override(config.Object):
    """One airOS config key to set, and the value to set it to.

    The value is a Jinja2 expression rendered against the variables below, so one
    override set serves many radios::

        key:   wireless.1.ssid
        value: {{ ssid }}

    An undefined variable is an error, not a blank — a half-rendered value must
    never reach flash.
    """

    key = config.String(
        "Key",
        name="key",
        description=(
            "airOS config key, e.g. 'radio.1.freq' or 'netconf.1.ip'. Dotted and "
            "1-indexed where it represents a list."
        ),
    )
    # Attribute is `val`, not `value`: pydoover reserves `value` on config
    # elements (it is the accessor property). The JSON key stays "value".
    val = config.String(
        "Value",
        name="value",
        default="",
        description="Value.",
    )


class AirMaxConfig(config.Schema):
    # Declaration order sets UI position, so this sits first deliberately: it is
    # the switch that decides whether the app writes to a radio at all.
    dry_run = config.Boolean(
        "Dry Run",
        name="dry_run",
        default=False,
        description=(
            "Report the diff but never write to the radio. Telemetry still works."
        ),
    )

    # -------------------------------------------------------------- the radio
    mac = config.String(
        "MAC Address",
        name="mac",
        description=(
            "The radio this install manages. Accepts 04:18:D6:AA:BB:CC, "
            "04-18-d6-aa-bb-cc or 0418d6aabbcc. Run 'airos discover' to find it — "
            "the number on the device label is a serial, not a MAC."
        ),
    )
    expected_model = config.String(
        "Expected Model",
        name="expected_model",
        default="",
        description=(
            "Optional. Provisioning is refused unless the discovered model contains "
            "this string — a guard against a MAC typo hitting a real radio."
        ),
    )
    overrides = config.Array(
        "Config Overrides",
        name="overrides",
        element=Override("Override"),
        default=[],
        description=(
            "The airOS keys to set. Everything else on the radio is left alone. "
            "Leave empty to run as telemetry-only, with no config changes at all."
        ),
    )
    # ------------------------------------------------------------------ safety
    max_attempts = config.Integer(
        "Max Attempts",
        name="max_attempts",
        default=3,
        minimum=1,
        maximum=20,
        description=(
            "After this many failed pushes the radio is parked and left alone. This "
            "is what stops a config that never converges becoming a reboot loop."
        ),
    )
    retry_backoff = config.Integer(
        "Retry Backoff (s)",
        name="retry_backoff",
        default=300,
        minimum=10,
        description="Minimum wait before retrying after a failed attempt.",
    )
    failed_retry_after = config.Integer(
        "Failed Retry After (s)",
        name="failed_retry_after",
        default=3600,
        minimum=0,
        description=(
            "How long a parked radio waits before trying again, so a transient "
            "failure heals itself. 0 means never retry. Editing the overrides or "
            "variables also clears a parked radio immediately."
        ),
    )

    # -------------------------------------------------------------- networking
    interface = config.String(
        "Provisioning Interface",
        name="interface",
        default="eth0",
        description=(
            "LAN interface to discover and provision on. MUST NOT be the interface "
            "carrying this device's default route — the app refuses to start if it is."
        ),
    )
    manage_addresses = config.Boolean(
        "Manage Interface Addresses",
        name="manage_addresses",
        default=True,
        description=(
            "Temporarily add a secondary address so a factory radio on 192.168.1.20 "
            "is reachable from another subnet. Needs NET_ADMIN. Removed again after "
            "each attempt."
        ),
    )
    poll_interval = config.Integer(
        "Poll Interval (s)",
        name="poll_interval",
        default=30,
        minimum=5,
        description=(
            "How often to sweep for the radio and read its telemetry. Also the "
            "resolution of the graphs in the UI."
        ),
    )
    discovery_timeout = config.Number(
        "Discovery Timeout (s)",
        name="discovery_timeout",
        default=3.0,
        description="How long to listen for discovery replies each sweep.",
    )
    reboot_wait = config.Integer(
        "Reboot Wait (s)",
        name="reboot_wait",
        default=120,
        minimum=30,
        description=(
            "How long to wait for the radio to come back after a reboot before "
            "verifying. It returns on its NEW address, which discovery re-finds."
        ),
    )
    ssh_port = config.Integer("SSH Port", name="ssh_port", default=22)

    # ------------------------------------------------------------ credentials
    credentials = config.Array(
        "Credentials",
        name="credentials",
        element=Credential("Credential"),
        default=[{"username": "ubnt", "password": "ubnt", "label": "factory default"}],
        description="Tried in order until one authenticates.",
    )
    verify_exclude = config.Array(
        "Verify Exclude Keys",
        name="verify_exclude",
        element=config.String("Key Pattern", name="pattern"),
        default=DEFAULT_VERIFY_EXCLUDE,
        description=(
            "fnmatch patterns for keys that are pushed but not verified, because "
            "the radio rewrites them (password hashes, obscured PSKs)."
        ),
    )


def export():
    AirMaxConfig.export(
        Path(__file__).parents[2] / "doover_config.json", "ubiquiti_airmax"
    )


if __name__ == "__main__":
    export()
