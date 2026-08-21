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
