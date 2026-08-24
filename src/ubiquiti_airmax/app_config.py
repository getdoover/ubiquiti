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
    apply_to_radio = config.Boolean(
        "Set This On The Radio",
        name="apply_to_radio",
        default=False,
        description=(
            "Enforce this credential on the radio, so every radio ends up with the "
            "same login. The password is stored on the radio as a crypt hash, which "
            "the app generates — a radio already using this password is left alone "
            "rather than needlessly rewritten and rebooted. Flag exactly one "
            "credential; this one must stay in the list or the app locks itself out."
        ),
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

    deployment_delay = config.Integer(
        "Deployment Delay",
        name="deployment_delay",
        default=30,
        minimum=0,
        description=(
            "Seconds to wait before applying a new config, measured from when this "
            "install received it. The app still discovers, reads telemetry and "
            "reports the pending diff during the wait — it just holds the write. "
            "Lets a whole network be updated and deployed together: every radio "
            "receives its config while the links are still up, then they all apply. "
            "0 applies immediately."
        ),
    )

    # -------------------------------------------------------------- the radio
    mac = config.String(
        "MAC Address",
        name="mac",
        description=(
            "The radio this install manages. Accepts 04:18:D6:AA:BB:CC, "
            "04-18-d6-aa-bb-cc or 0418d6aabbcc. You can also paste the whole "
            "string from the device label (e.g. 2450BJ28704EE29BCB) — Ubiquiti "
            "prefixes the MAC with a batch code, and the last 12 hex digits are "
            "taken. Run 'airos discover' to read it off the radio directly."
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
    uplink_mac = config.String(
        "Uplink MAC",
        name="uplink_mac",
        default="",
        description=(
            "Optional. The MAC of the radio this one links up to, for the network "
            "overview. Leave empty — the link is normally read off the radio "
            "itself. Set it only when a radio will not report its peer, which "
            "otherwise leaves it drawn as unconnected."
        ),
    )
    peer_address = config.String(
        "Link Peer Address",
        name="peer_address",
        default="",
        description=(
            "IP of the radio at the FAR end of this link, pinged each pass to "
            "measure real round-trip latency and packet loss. Leave blank on an "
            "AP: it learns its stations' addresses itself. A client cannot, so "
            "set it there. Pinging this radio's own address measures the LAN "
            "hop, not the link."
        ),
    )

    overrides = config.Array(
        "Config Overrides",
        name="overrides",
        element=Override("Override"),
        default=[],
        description=(
            "The airOS keys to set for THIS radio. Everything else on the radio is "
            "left alone. Applied on top of Profile Overrides, so a key set here "
            "wins. Leave both empty to run as telemetry-only."
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

    # ------------------------------------------------- shared, profile-supplied
    #
    # A second array purely so the two layers survive a Doover config merge.
    # `deep_merge` replaces arrays rather than combining them, so a config profile
    # writing `overrides` would wipe the per-install list (and vice versa). Two
    # distinct keys merge cleanly: the profile owns this one, the install owns the
    # other, and the app concatenates them.
    profile_overrides = config.Array(
        "Profile Overrides",
        name="profile_overrides",
        element=Override("Profile Override"),
        default=[],
        description=(
            "Exactly the same as Config Overrides — airOS keys to set — but kept as "
            "a separate list so a Doover config profile can supply a shared layer "
            "without overwriting the per-install one. Put settings common to every "
            "radio of this role here (mode, SSID, PSK, frequency, country) and "
            "per-radio settings in Config Overrides, which are applied afterwards "
            "and win on any key set in both."
        ),
    )


def export():
    AirMaxConfig.export(
        Path(__file__).parents[2] / "doover_config.json", "ubiquiti_airmax"
    )


if __name__ == "__main__":
    export()
