"""UI schema for the Ubiquiti Network Overview.

One remote component and nothing else. Every element on the page — the graph,
the per-link figures, the drill-in panel — is rendered by the widget, because
all of it derives from other agents' data that a static UI schema cannot reach.
"""

from pathlib import Path

from pydoover import ui


class NetworkOverviewUI(ui.UI, default_open=True):
    widget = ui.RemoteComponent(
        name="UbiquitiNetwork",
        display_name="Network Overview",
        component_url="$config.app().dv_widget_url",
        scope="UbiquitiNetworkWidget",
        module="./NetworkOverviewWidget",
        # The dashboard agent's deployment config carries DEVICE_MAP under this
        # app's key; the widget needs the key to find it.
        app_key="$config.app().APP_KEY",
    )


def export():
    NetworkOverviewUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "ubiquiti_network_overview"
    )


if __name__ == "__main__":
    export()
