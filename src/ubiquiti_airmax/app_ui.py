"""Doover UI for the AirMax app — live telemetry for one radio.

There are deliberately no provisioning controls. The reconciler applies config
once, when required, and then idles; a button would only be a way to ask it to do
something it has already decided about. Provisioning surfaces as status, plus a
warning indicator when a radio has been parked and needs a human.

Note on warning indicators: ``hidden`` is bound to a *positive-logic tag*
(``config_ok``), not mutated at runtime. The declarative UI compiles to a static
schema, so assigning ``self.ui.x.hidden`` in the app does nothing at all.
"""

from pathlib import Path

from pydoover import ui

from .app_tags import AirMaxTags as T


class AirMaxUI(ui.UI):
    # ---- history chart, above the live readouts ---------------------------
    history = ui.Multiplot(
        "Link History",
        name="history",
        series=[
            ui.Series(
                "SNR", value=T.snr, units="dB", colour=ui.Colour.red, active=True
            ),
            ui.Series(
                "Signal",
                value=T.signal,
                units="dBm",
                colour=ui.Colour.blue,
                active=True,
            ),
            ui.Series(
                "Noise Floor",
                value=T.noise_floor,
                units="dBm",
                colour=ui.Colour.grey,
                active=False,
            ),
            ui.Series(
                "RX Throughput",
                value=T.rx_throughput,
                units="kbps",
                colour=ui.Colour.purple,
                shared_axis=False,
                active=False,
            ),
            # Off the shared dB axis: a 0/1 series plotted against dBm would
            # flatten the signal traces to a straight line.
            ui.Series(
                "Device Connected",
                value=T.online,
                data_type="boolean",
                colour=ui.Colour.limegreen,
                shared_axis=False,
                active=False,
            ),
        ],
        position=0,
    )

    # ---- at a glance ------------------------------------------------------
    # `online` is the radio answering its status commands, i.e. the Ubiquiti
    # device is reachable — not the Doovit's own connectivity.
    online = ui.BooleanVariable(
        "Device Connected", value=T.online, name="online", position=1
    )

    # SNR is the headline number: it is what actually predicts whether a link
    # will carry traffic, and unlike raw signal it is meaningful without knowing
    # the local noise environment.
    snr = ui.NumericVariable(
        "Signal / Noise",
        value=T.snr,
        name="snr",
        units="dB",
        precision=0,
        form=ui.Widget.radial,
        position=2,
        ranges=[
            ui.Range("Unusable", 0, 10, ui.Colour.red, show_on_graph=True),
            ui.Range("Marginal", 10, 20, ui.Colour.yellow, show_on_graph=True),
            ui.Range("Good", 20, 30, ui.Colour.blue, show_on_graph=True),
            ui.Range("Excellent", 30, 60, ui.Colour.green, show_on_graph=True),
        ],
    )

    # ---- warnings — bound to positive-logic tags, hidden when all is well --
    unreachable_warning = ui.WarningIndicator(
        "Radio not reachable",
        name="unreachable_warning",
        hidden=T.reachable_ok,
        position=20,
    )
    config_warning = ui.WarningIndicator(
        "Config could not be applied — needs attention",
        name="config_warning",
        hidden=T.config_ok,
        position=21,
    )

    # ---- link quality -----------------------------------------------------
    throughput = ui.Submodule(
        "Rates & Throughput",
        name="throughput",
        position=31,
        children=[
            ui.NumericVariable(
                "TX Rate", value=T.tx_rate, name="tx_rate", units="Mbps", precision=0
            ),
            ui.NumericVariable(
                "RX Rate", value=T.rx_rate, name="rx_rate", units="Mbps", precision=0
            ),
            ui.NumericVariable(
                "TX Throughput",
                value=T.tx_throughput,
                name="tx_throughput",
                units="kbps",
                precision=0,
            ),
            ui.NumericVariable(
                "RX Throughput",
                value=T.rx_throughput,
                name="rx_throughput",
                units="kbps",
                precision=0,
            ),
            ui.NumericVariable(
                "Link Latency",
                value=T.latency,
                name="latency",
                units="ms",
                precision=0,
            ),
        ],
    )

    # ---- collapsible detail ----------------------------------------------
    peers = ui.Submodule(
        "Peers",
        name="peers",
        position=40,
        is_collapsed=True,
        children=[
            ui.NumericVariable(
                "Stations Connected",
                value=T.station_count,
                name="station_count",
                precision=0,
            ),
            ui.TextVariable("Stations", value=T.stations, name="stations"),
            ui.TextVariable("AP MAC", value=T.ap_mac, name="ap_mac"),
            ui.NumericVariable(
                "Distance", value=T.distance, name="distance", units="m", precision=0
            ),
        ],
    )

    device = ui.Submodule(
        "Device",
        name="device",
        position=41,
        is_collapsed=True,
        children=[
            ui.TextVariable("Model", value=T.model, name="model"),
            ui.TextVariable("Platform", value=T.platform, name="platform"),
            ui.TextVariable("IP Address", value=T.ip_address, name="ip_address"),
            ui.TextVariable("ESSID", value=T.essid, name="essid"),
            ui.TextVariable("Mode", value=T.wireless_mode, name="wireless_mode"),
            ui.NumericVariable(
                "Frequency",
                value=T.frequency,
                name="frequency",
                units="MHz",
                precision=0,
            ),
            ui.NumericVariable(
                "Channel Width",
                value=T.channel_width,
                name="channel_width",
                units="MHz",
                precision=0,
            ),
            ui.NumericVariable(
                "Radio Uptime", value=T.uptime, name="uptime", units="h", precision=1
            ),
            ui.Timestamp("Radio Booted", value=T.started_at_ms, name="started_at"),
        ],
    )


def export():
    AirMaxUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json",
        "ubiquiti_airmax",
    )


if __name__ == "__main__":
    export()
