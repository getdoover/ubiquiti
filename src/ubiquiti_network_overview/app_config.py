"""Config schema for the Ubiquiti Network Overview.

The app is installed once, on its own agent, and shows every radio in the
organisation on one graph. It never talks to a device: the whole overview is
assembled client side by the remote component from data the AirMax installs
already publish.
"""

from pathlib import Path

from pydoover import config
from pydoover.processor import ExtendedPermissionsConfig


class NetworkOverviewConfig(config.Schema):
    # Which devices this dashboard may read. Set `Apps Installed` to the AirMax
    # app and the platform fills DEVICE_MAP with every device running it — the
    # overview then finds its own radios instead of being told about them one at
    # a time.
    #
    # `app_installs__name` is the field that makes multi-radio devices work: a
    # Doovit may run several AirMax installs (an uplink and a downlink), each
    # publishing tags under its own app key, and this is what names those keys.
    # Without it the widget would have to guess at them by prefix.
    extended_permissions = ExtendedPermissionsConfig(
        extra_fields=[
            "id",
            "display_name",
            "group__id",
            "group__name",
            "latitude",
            "longitude",
            "app_installs__name",
            "app_installs__application_name",
        ]
    )

    stale_after_minutes = config.Integer(
        "Stale After (Minutes)",
        name="stale_after_minutes",
        default=10,
        minimum=1,
        description=(
            "A radio whose telemetry has not been updated for this long is drawn "
            "as stale rather than as its last-known state. Should be comfortably "
            "more than the AirMax poll interval (30 s by default)."
        ),
    )

    ignore_groups = config.GroupsConfig(
        "Ignored Groups",
        description=(
            "Devices in these groups are left off the graph. Useful for test or "
            "unallocated groups when the permission above is set broadly. Does "
            "not nest — list direct parent groups."
        ),
    )

    position = config.ApplicationPosition()


def export():
    NetworkOverviewConfig.export(
        Path(__file__).parents[2] / "doover_config.json", "ubiquiti_network_overview"
    )


if __name__ == "__main__":
    export()
