"""Tags published by the AirMax app.

One install manages one radio, so these are flat per-radio values rather than
per-target maps. That is deliberate: a flat numeric tag graphs and alerts
properly in Doover, which a nested object does not.

Two conventions, both borrowed from starlink-manager:

* ``default=None`` and publishing ``None`` for a metric the radio did not report.
  Publishing 0 instead would draw an offline radio as 0 dBm signal — which is an
  extremely *strong* signal, not a missing one.
* ``log_on`` on everything worth history. Without it a 30 s poll either floods
  the historian or logs nothing useful. Deltas are set to the smallest change
  that actually means something on a radio link.
"""

from pydoover.tags import AnyChange, Delta, Tag, Tags


class AirMaxTags(Tags):
    # ------------------------------------------------------------- link health
    # live=True on the fast-moving values so the UI updates promptly.
    online = Tag("boolean", default=False, live=True, log_on=AnyChange())
    signal = Tag("number", default=None, live=True, log_on=Delta(amount=3.0))
    noise_floor = Tag("number", default=None, live=True, log_on=Delta(amount=3.0))
    snr = Tag("number", default=None, live=True, log_on=Delta(amount=2.0))
    ccq = Tag("number", default=None, live=True, log_on=Delta(amount=5.0))
    quality = Tag("number", default=None, log_on=Delta(amount=5.0))
    capacity = Tag("number", default=None, log_on=Delta(amount=5.0))

    # ------------------------------------------------------ rates + throughput
    tx_rate = Tag("number", default=None, live=True, log_on=Delta(amount=10.0))
    rx_rate = Tag("number", default=None, live=True, log_on=Delta(amount=10.0))
    # Real ICMP round trip to the radio at the far end of the link, in ms.
    #
    # NOT airOS's own latency field. `wlanTxLatency` is airMAX TX *queue* latency:
    # legitimately 0 on an uncongested link, at 1 ms resolution, so it can express
    # neither link length (10 km of air is 0.067 ms) nor link health. It is still
    # parsed in telemetry.py, and deliberately not published.
    #
    # Delta must stay well under 1 ms or a healthy link's whole range never logs.
    latency = Tag("number", default=None, live=True, log_on=Delta(amount=0.5))
    #: The half of the ping result that usually matters more. A link can hold a
    #: fine RTT while dropping a fifth of its packets.
    packet_loss = Tag("number", default=None, live=True, log_on=Delta(amount=5.0))
    tx_throughput = Tag("number", default=None, live=True, log_on=Delta(amount=100.0))
    rx_throughput = Tag("number", default=None, live=True, log_on=Delta(amount=100.0))

    # ------------------------------------------------------- identity + uptime
    # AnyChange catches firmware upgrades, hardware swaps and re-addressing.
    model = Tag("string", default=None, log_on=AnyChange())
    platform = Tag("string", default=None, log_on=AnyChange())
    firmware = Tag("string", default=None, log_on=AnyChange())
    hostname = Tag("string", default=None, log_on=AnyChange())
    ip_address = Tag("string", default=None, log_on=AnyChange())
    essid = Tag("string", default=None, log_on=AnyChange())
    wireless_mode = Tag("string", default=None, log_on=AnyChange())
    frequency = Tag("number", default=None, log_on=Delta(amount=1.0))
    channel_width = Tag("number", default=None, log_on=Delta(amount=1.0))
    uptime = Tag("number", default=None)
    # ms-since-epoch of the radio's last boot, derived from uptime. A reboot
    # resets uptime, so started_at jumps — Delta tolerates measurement jitter
    # while still logging a row when the radio actually restarts.
    started_at_ms = Tag("integer", default=None, log_on=Delta(amount=5000))
    last_seen = Tag("number", default=None)

    # ------------------------------------------------------------------- peers
    station_count = Tag("integer", default=None, log_on=Delta(amount=1))
    stations = Tag("string", default=None)
    ap_mac = Tag("string", default=None, log_on=AnyChange())
    distance = Tag("number", default=None, log_on=Delta(amount=50.0))

    # ---------------------------------------------------------------- topology
    # What a fleet-wide network view needs to draw this radio and its links.
    # Published as tags rather than left in each device's deployment config so a
    # dashboard picks them up in the one batched `tag_values` read it already
    # does, instead of a per-device config fetch.
    #
    # `radio_mac` is the node's identity and, in AP mode, the BSSID a station
    # reports as its `ap_mac` — so an edge is `station.ap_mac == ap.radio_mac`.
    # It is published even when the radio is unreachable: a node that has gone
    # dark still belongs on the graph, and dropping its identity would make it
    # vanish instead.
    radio_mac = Tag("string", default=None, log_on=AnyChange())
    # Operator-declared uplink, for a radio whose firmware will not tell us who
    # it is associated with. Empty normally.
    uplink_mac = Tag("string", default=None, log_on=AnyChange())
    # The machine-readable twin of `stations` above. No `log_on`, matching that
    # tag: the per-station figures move every pass, so logging it would fill the
    # historian with a JSON blob nothing queries.
    stations_json = Tag("string", default=None)

    # ------------------------------------------------------------ provisioning
    config_state = Tag("string", default=None, log_on=AnyChange())
    config_message = Tag("string", default=None, log_on=AnyChange())
    config_diff = Tag("string", default=None)
    config_attempts = Tag("integer", default=0, log_on=Delta(amount=1))
    # Positive-logic mirror for WarningIndicator(hidden=...): the indicator is
    # hidden while this is True. Runtime mutation of `.hidden` does nothing on a
    # declarative UI — the schema is static, so it must be bound to a tag.
    config_ok = Tag("boolean", default=True, log_on=AnyChange())
    reachable_ok = Tag("boolean", default=True, log_on=AnyChange())
