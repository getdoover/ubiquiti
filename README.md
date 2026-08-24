# ubiquiti

Doover apps to manage and interface with Ubiquiti devices.

This is a multi-app repo: one image, one `doover_config.json`, one app per entry
point in `pyproject.toml`. Shared device-driver code lives in
`packages/ubiquiti_common/` so a second app doesn't fork it.

| App | Entry point | What it does |
|-----|-------------|--------------|
| **Ubiquiti AirMax** (`ubiquiti_airmax`) | `doover-app-run-airmax` | Live telemetry + autonomous config for one airMAX radio |

## Ubiquiti AirMax

Live telemetry for one airMAX radio, plus autonomous config. **One install per
radio** — the MAC identifies it, and the UI is that radio's dashboard.

```
poll (every 30s) → discover MAC → SSH → read telemetry ─────► UI
                                    └─► diff config vs overrides
                                            ↓ (only if drifted)
                                    push → cfgmtd → reboot
                                            ↓
                                    verify on a later pass
```

### Telemetry

The UI shows what the link is actually doing. SNR is the headline figure because
it predicts whether a link will carry traffic, and unlike raw signal it means
something without knowing the local noise floor.

| Group | Shows |
|-------|-------|
| At a glance | Online, SNR (radial gauge), attention warning |
| Link Quality | Signal, noise floor, airMAX CCQ / quality / capacity |
| Rates & Throughput | Negotiated TX/RX rate, plus actual throughput from byte counters |
| Peers | Connected stations with per-station signal and rates; or AP MAC and distance |
| Device | Model, platform, firmware, ESSID, mode, frequency, channel width, uptime |
| Configuration | State, detail, attempts, pending changes |

Read from `mca-dump` (JSON, preferred) falling back to `mca-status` (flat
key=value), plus `wstalist` for stations and `/proc/net/dev` for throughput.
Field names differ between airOS 6 and 8 and are undocumented, so the parser
works off alias lists and keeps every raw field — confirm them against hardware
with `airos status --raw`.

Leave **Config Overrides** empty and the app is a pure monitor: it reads
telemetry and never writes anything.

### Configuration is autonomous

There are no provisioning buttons. The reconciler applies config once, when
required, and then idles — a button would only be a way to ask it to do something
it has already decided about.

**Overlay, not whole-file.** Overrides are a list of airOS keys to set. The app
reads the radio's live `/tmp/running.cfg`, applies only those keys, and writes it
back, so everything model- and firmware-specific (chain counts, calibration,
per-platform defaults) is preserved. Values are **literal** — one install manages
one radio, so there is nothing to parameterise.

Overrides come in two layers, applied in order:

| Layer | Field | Owned by | Holds |
|---|---|---|---|
| 1 | **Profile Overrides** | a Doover config profile | settings shared by every radio of a role — mode, SSID, PSK, frequency, country |
| 2 | **Config Overrides** | the individual install | settings for this radio only — applied second, so it wins on any shared key |

They are separate fields rather than one list because Doover's config merge
*replaces* arrays instead of combining them: a profile writing `overrides` would
wipe the install's list. Two keys merge cleanly.

An example of each:

| Override | Value |
|---|---|
| `radio.1.freq` | `5800` |
| `wireless.1.ssid` | `SPAN-LINK` |
| `wireless.1.security.type` | `none` |

Both halves are validated before anything is written. A key with whitespace or an
embedded `=` is refused, since a config the radio half-reads is worse than one it
rejects. A value still containing `{{ }}` is refused too: nothing renders it, so
it would be flashed verbatim — an override of `netconf.3.ip={{ ip }}` would cost
the radio its address on next boot.

### Scope

airOS radios reachable over SSH — airMAX M on airOS 6 (XM/XW/TI/XN) and airMAX AC
on airOS 8 (XC/WA). The apply sequence is identical across both, which is why one
driver handles every Bullet.

Not covered: a factory-fresh airOS 8 unit still behind its EULA / country-code /
password wizard (get it to where SSH answers first), and the other Ubiquiti
config systems entirely — LTU, airFiber 60, EdgeOS/UISP routers and switches,
UniFi. Those need their own adapters.

### Safety

Configuring radios is close to irreversible — a bad network block means a trip to
the reset button — so the defaults are conservative:

| Control | Default | Effect |
|---------|---------|--------|
| **Dry Run** | on | Diff and report; never write. Telemetry still works. |
| **Max Attempts** | 3 | A radio that never converges is parked instead of rebooting forever. |
| **Failed Retry After** | 1 h | A parked radio retries once, later, so transient failures self-heal. 0 = never. |
| Expected Model | optional | Refuses to provision unless the discovered model matches — catches a MAC typo. |
| Interface guard | — | Refuses to add a helper address to the interface carrying the default route (warns at startup if shared). |

The attempt ceiling matters more than it looks, because it is the *only* guard
against a key that cannot converge. There is deliberately no exclusion list: every
override is both compared and written, so a value can never be pushed without
also being checked. If a key should not be managed, leave it out of the overrides.

If some key does turn out to read back differently from what was written, the
radio pushes and reboots at most **Max Attempts** times, then parks with a message
naming the unconverged keys and raises an attention warning. Bounded and
diagnosable, rather than silent.

**Recovering a parked radio needs no button.** Editing the overrides or variables
is new intent, which clears the attempt count and retries immediately; otherwise
the cooldown does it. Both are bounded, so neither can become a slow reboot loop.

### Deployment requirements

`deployment/docker-compose.yml` declares both, and neither is optional:

- `network_mode: host` — UBNT discovery is an L2 broadcast and will not cross a
  docker bridge.
- `cap_add: NET_ADMIN` — a factory radio sits on `192.168.1.20`, so reaching it
  from another subnet means temporarily adding an address in its range. The
  address is removed again after each attempt.

Point **Provisioning Interface** at a dedicated LAN port. Adding
`192.168.1.0/24` to the interface carrying the Doovit's default route can
black-hole the Doover connection — and what you'd lose remote access to is the
device doing the provisioning. The app checks and refuses, but pick correctly
anyway.

## Bench tool

The driver is also a standalone CLI, so a radio on the desk can be worked on
without the Doover harness at all:

```bash
uv run airos discover --iface en0 --raw              # Linux
uv run airos discover --broadcast 192.168.50.255    # or aim it explicitly
uv run airos dump --host 192.168.1.20 > factory.cfg
uv run airos cfgdiff factory.cfg configured.cfg     # the diff IS your template
uv run airos diff --host 192.168.1.20 --template t.j2 --var ssid=SPAN
uv run airos push --host 192.168.1.20 --template t.j2 --var ssid=SPAN --commit --reboot
```

`push` without `--commit` is a dry run. See `templates/README.md` for how to
build a template from a real radio rather than guessing key names.

## Development

See `DEVELOPMENT.md`.
