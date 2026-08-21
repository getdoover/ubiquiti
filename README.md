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
per-platform defaults) is preserved. Each override's *value* is a Jinja2
expression rendered against the install's variables, which lets a Solution share
one override set across many radios while each install supplies its own values:

| Override | Value |
|---|---|
| `radio.1.freq` | `{{ freq }}` |
| `wireless.1.ssid` | `{{ ssid }}` |
| `wireless.1.security` | `WPA2` |

An undefined variable is an error, not a blank. Keys are validated too:
whitespace or an embedded `=` is refused rather than written, since a config the
radio half-reads is worse than one it rejects.

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
| Platform guard | — | Overrides declared for one platform are refused on another. |
| Expected Model | optional | Refuses to provision unless the discovered model matches — catches a MAC typo. |
| Interface guard | — | The app refuses to start if the provisioning interface carries this device's default route. |

The attempt ceiling matters more than it looks. Some keys never compare equal to
what was pushed because the radio rewrites them (password hashes, obscured
PSKs) — those belong in **Verify Exclude Keys**, which ships populated. Anything
else that will not converge burns its attempts and stops, showing an attention
warning in the UI.

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
