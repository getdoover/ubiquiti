"""Ubiquiti Network Overview — one graph of every airMAX radio in the org.

This processor does no per-device work, and deliberately so. Every radio's
telemetry is already published by its ``ubiquiti_airmax`` install; the topology
between them is derivable from three of those tags (``radio_mac``, ``ap_mac``,
``stations_json``); and both are readable from the browser. So the graph, the
per-link figures and the drill-in panel are all built client side in the
``NetworkOverviewWidget`` remote component, from the ``tag_values`` and
``doover_connection`` aggregates of the devices in ``DEVICE_MAP``.

Two consequences worth keeping in mind before adding anything here:

* **Nothing is installed on a device for this app to work.** It reads what the
  fleet already publishes. Adding a per-device component would undo that.
* **A pass here would be the wrong place for topology.** Edges change when a
  radio re-associates, which the widget sees on its live tag subscription
  immediately; recomputing them on a lambda invocation would be both slower and
  staler.

The processor therefore exists to host the widget (via the static UI schema),
to own the device-permission config the platform expands into ``DEVICE_MAP``,
and to keep its own agent looking online when it is deployed.
"""

import logging
from datetime import datetime, timezone

from pydoover.models import (
    ConnectionConfig,
    ConnectionDetermination,
    ConnectionStatus,
    ConnectionType,
    DeploymentEvent,
)
from pydoover.models.data.connection import ConnectionDisplay
from pydoover.processor import Application

from .app_config import NetworkOverviewConfig
from .app_ui import NetworkOverviewUI

log = logging.getLogger(__name__)


class NetworkOverviewApp(Application):
    config_cls = NetworkOverviewConfig
    ui_cls = NetworkOverviewUI

    async def on_deployment(self, event: DeploymentEvent):
        """Ping this agent's connection so the dashboard does not read as offline.

        The agent hosts a dashboard rather than a device, so nothing else ever
        pings it — without this it would show as an offline device in the very
        fleet views it sits alongside.
        """
        await self.api.ping_connection_at(
            datetime.now(timezone.utc),
            ConnectionStatus.continuous_online_no_ping,
            ConnectionDetermination.online,
            user_agent="ubiquiti;network-overview",
        )
        await self.api.update_connection_config(
            ConnectionConfig(ConnectionType.periodic, display=ConnectionDisplay.never)
        )
        log.info("pinged connection for network-overview agent %s", self.agent_id)
