# Ubiquiti Doover apps

Multi-app repo (leachate-telemetry pattern): one `doover_config.json` with a key
per app, suffixed entry points in `pyproject.toml`. Shared device-driver code
lives in `packages/ubiquiti_common/` as a uv workspace member — put anything
reusable there rather than importing across app packages.

**Two deploy kinds live here**, which is why there is both a `Dockerfile` and a
`build.sh`:

* **`DEV`** — a device app, shipped as a Docker image and run on a Doovit.
* **`PRO`** — a cloud processor, shipped as a `package.zip` plus a React remote
  component, run on its own agent.

`doover app discover` tells them apart from the app block (`builds_image` vs
`builds_package`), and the shared CI workflow runs the right job for each. This
needs no workflow changes — see **CI and publishing**.

## Commands

```bash
uv run pytest tests -q
uv run export-config-airmax     # required after editing app_config.py
uv run export-ui-airmax         # required after editing app_ui.py
uv run export-config-overview   # same, for the network-overview app
uv run export-ui-overview
uv run airos discover --iface en0
uv run airos status --host <ip> --raw   # verify telemetry field aliases

doover app discover . --json    # confirm both apps resolve to the right kind
./build.sh                      # package.zip + widget bundle (processor app)
npm --prefix widget run typecheck   # rspack strips types, it does not check them
```

## Apps

| App | Kind | Entry point | Package |
|-----|------|-------------|---------|
| Ubiquiti AirMax (telemetry + autoconfig) | `DEV` | `doover-app-run-airmax` | `src/ubiquiti_airmax/` |
| Ubiquiti Network Overview (fleet link graph) | `PRO` | `ubiquiti_network_overview.handler` | `src/ubiquiti_network_overview/` + `widget/` |

## Start here

`src/ubiquiti_airmax/provisioner.py` is the core. **One install manages one
radio** (`allow_many: true`); the MAC in config identifies it.

It is a **reconciler**, not a script: each pass discovers the MAC, reads
telemetry, and pushes config only if reality and intent differ. Verification
happens on a *later* pass rather than by blocking through the reboot — that is
what makes "the radio came back on a different address" work, since every pass
re-discovers.

The app is telemetry-first: the UI exists to show the link, and config
convergence is a background concern that reports its state.

## Invariants — do not weaken these without discussion

- **No provisioning controls in the UI.** Config is applied once, when required,
  and the app then idles. A button would only ask it to do what it already
  decided. `tests/test_imports.py::test_ui_has_no_provisioning_controls` pins
  this. Provisioning surfaces as status plus a `WarningIndicator`.
- **Telemetry is read before any config work**, and on every pass, including when
  converged and when overrides are empty. The UI must not go blank because there
  is nothing to provision.
- **Overlay, never whole-file.** Overrides are a list of `{key, value}`, not a
  `system.cfg`. Read `/tmp/running.cfg`, apply those keys, write back. Never ship
  a complete config; it drops model-specific keys.
- **Discovery falls back to unicast.** Broadcast is not always deliverable: a
  Doovit bridging its LAN onto a wireless interface (`br0` = `eth1` + `wlan0`,
  seen on Porgera Station 2) forwards unicast but drops broadcast, so a radio is
  reachable by ping/ARP/SSH and invisible to a broadcast probe. `_run_pass` tries
  broadcast, then — only if the target MAC is absent — the last-known IP, the
  configured `netconf.3.ip`, and finally a unicast sweep of the interface's
  subnet. `hosts_of` **skips** a network above its cap rather than truncating, so
  a sweep is never silently partial.
- **Deployment Delay holds the first write of a new intent.** Measured from
  `TargetRecord.intent_since` — set on a fresh record (redeploy) *and* on a
  fingerprint change (live config edit) — so a fleet-wide deploy reaches every
  radio before any link drops. On a chained network, reconfiguring an uplink first
  cuts off stations that have not received their config yet. Checked after dry run
  and **before** the attempt ceiling: waiting must never consume an attempt.
  Telemetry and the pending diff keep publishing throughout.
- **Two override layers, on purpose.** `profile_overrides` (shared, bottom of the
  form) and `overrides` (per-install). They are separate config keys because
  Doover's `deep_merge` *replaces* arrays rather than combining them — a config
  profile writing `overrides` would silently wipe the per-install list. The app
  concatenates them profile-first in `_layered_overrides()`, so a key in both
  takes the per-install value. Pinned by
  `test_install_overrides_win_over_profile_overrides` and
  `test_both_override_layers_exist_as_separate_keys`.
- **Override values are literal.** `build_overlay` does no templating: one install
  manages one radio, so there is nothing to parameterise. It validates keys
  against `_VALID_KEY` and **refuses any value containing `{{` or `}}`** — nothing
  renders those now, so a leftover placeholder would be flashed verbatim.
  The `airos` CLI still renders `--var` into its own template *files*; that is a
  bench convenience, so render to literals before transcribing into app config.
- **Dry Run defaults OFF and is the first config field.** An install exists to
  converge a radio; defaulting it on meant every new install silently did
  nothing. It suppresses writes but never telemetry.
- **Bounded attempts, bounded recovery.** `max_attempts` is the reboot-loop
  guard. A parked radio revives only two ways, both bounded: the intent
  fingerprint changes (operator edited overrides/variables), or
  `failed_retry_after` elapses. Never add an unbounded retry.
  Pinned by `test_non_converging_config_is_bounded`,
  `test_intent_change_revives_a_parked_radio`,
  `test_identical_config_reload_does_not_revive_a_parked_radio`.
- **Only the configured MAC is ever written to.**
- **No default-route guard, deliberately.** Adding a secondary address only adds
  a connected route for that subnet; it does not disturb an existing default
  route. A guard here was wrong twice — first fatal at startup (crash-looped
  station-1, whose `br0` carries both the uplink and the radio's subnet), then as
  a `netif.reachable()` refusal that would have blocked a factory radio on that
  same interface. `manage_addresses` is the off-switch. Pinned by
  `test_no_default_route_guard_remains` and
  `test_setup_does_not_guard_the_provisioning_interface`.
  The genuine risk is *subnet overlap* — a helper address in a range already
  routed elsewhere — which the old check never detected anyway.

## Ubiquiti Network Overview

`src/ubiquiti_network_overview/` plus `widget/`. One install, on its own agent,
drawing every airMAX radio in the organisation as a link graph.

The Python side is almost empty on purpose — config, UI schema, and an
`on_deployment` connection ping. All of it is deliberate:

- **It never talks to a device, and nothing is installed on one for it to work.**
  It reads the `tag_values` and `doover_connection` aggregates of the devices in
  `DEVICE_MAP` through the browser's own client. Adding a per-device component
  would throw that property away.
- **Topology is computed in the widget, not in a pass here.** Edges change when a
  radio re-associates, which the widget sees immediately on its live tag
  subscription. Recomputing on a lambda invocation would be slower *and* staler.
- **Devices are found via `apps_installed`, not listed by hand.** Point the
  extended-permissions config at the AirMax app and the platform fills
  `DEVICE_MAP` with every device running it.
- **App keys come from `app_installs__name`, never from prefix-matching
  `tag_values`.** An install's key is operator-chosen — `airmax_upstream`, not
  `ubiquiti_airmax_1` — so guessing would miss exactly the multi-radio devices
  this view exists to draw. One Doovit routinely runs an uplink and a downlink.
- **It hard-depends on the AirMax topology tags** (`radio_mac`, `ap_mac`,
  `stations_json`, `uplink_mac`). A radio whose install predates them has no
  identity to draw, so the fleet must be on a release that publishes them.
  Pinned by `test_topology_tags_are_published`.
- **The UI schema's `module`/`scope` must match `widget/rsbuild.config.ts`.** A
  mismatch fails no build — the app publishes, the bundle loads, the panel
  renders empty. Pinned by `test_overview_ui_module_matches_the_widget_bundle`.
- **`agentId` comes from `useRemoteParams()`, `app_key` from the `uiElement`
  prop.** Not interchangeable, and `params.agent_id` does not exist. Getting
  either wrong makes DEVICE_MAP unreadable and every device vanish behind a
  message blaming the operator's config. Cost half a debugging session once.

### The diagram

`widget/src/lib/topology.ts` is the core, and the only part with unit tests
(`vitest`) — it is where a wrong answer is dangerous, because a wrong graph
renders confidently instead of erroring.

- **Normalisation lives inside `buildTopology`, not in its callers.** The join is
  a string comparison between MACs arriving from three places — a tag written by
  a current AirMax release, a tag written by an older one (upper-case), and an
  operator-typed config field. A mismatch yields an empty graph with no error
  anywhere. Pinned by the mixed-case test.
- **Two kinds of link, and they are not the same thing.** A *wireless* hop
  (`station.ap_mac == ap.radio_mac`) carries all the stats. A *LAN* link is two
  radios cabled together inside one Doovit — no stats exist, so it is drawn thin
  and dashed and unlabelled. A device's radios are joined as a *path*, not a
  mesh, so a three-radio site does not sprout meaningless edges.
- **Hops are discovered from both ends and deduplicated.** A station names its AP
  and an AP names its stations; either alone draws the hop, which matters because
  the two ends are polled independently. When both report, the AP-side signal is
  kept alongside the station's — the disagreement between the ends is itself the
  diagnosis.
- **Layout is per *device*, not per radio** (`lib/layout.ts`). dagre ranks the
  Doovits by RF hops and radios stack inside their box. Ranking a dozen boxes is
  far more stable than two dozen loose nodes, so the diagram does not reshuffle
  when one radio drops out. Deliberately hierarchical, not force-directed: the
  topology is a daisy chain, and a physics layout of a chain drifts between
  renders and hides which hop is bad.
- **A radio with no `radio_mac` is never dropped**, it goes to the tray below the
  diagram. That is every radio until the topology-tag release reaches the fleet.
- **SNR bands are conventional, not reported by the hardware.** `SNR_BANDS` in
  `lib/appearance.ts` is the single place to change them.

## Environment facts

- **Config schema:** pydoover only registers elements declared at *class* level.
  Assigning them in `__init__` exports an empty schema — silently. Array items
  are `config.Object` subclasses with an explicit `name=` on every field.
  A free-form `config.Object` exposes arbitrary keys as synthesised sub-elements
  rather than a dict, so reading one back needs a per-element walk. Nothing uses
  that shape now — override values are literal, with no variables to substitute.
- **The pass runs in `main_loop`** at `poll_interval`, wrapped in
  `PASS_TIMEOUT`. It does not block through a reboot — a push returns
  immediately and verification happens on a later pass — so a pass is a discovery
  sweep plus a few SSH commands. There is no background worker.
- **airOS 6 needs legacy SSH algorithms** explicitly enabled in asyncssh, and has
  no SFTP. Both handled in `airos.py`.
- **No radio simulator.** Unit tests use a fake radio for every safety gate; the
  push path is validated against real hardware. On macOS, container host
  networking cannot do L2 discovery — run the `airos` CLI natively instead
  (`airos discover --broadcast <lan-broadcast>`, since `--iface` needs iproute2).
- **Telemetry field names are reconstructed, not documented.** `telemetry.py`
  maps `mca-dump` / `mca-status` output through alias lists and keeps every raw
  field. Confirm aliases against hardware with `airos status --raw` before
  trusting a metric; the tests pin decoder *behaviour*, not field meanings.
- **Tags can and should hold None.** Declare telemetry tags `default=None` and
  publish None for a metric the radio did not report. Never publish 0 for a
  missing value — 0 dBm is an extremely *strong* signal, not an absent one.
- **When the radio is unreachable, leave telemetry tags alone.** `_publish`
  returns early after the always-published status tags, so the UI shows stale
  readings rather than a misleading clean zero (the starlink-manager convention).
- **`WarningIndicator(hidden=...)` binds to a positive-logic tag** (`config_ok`,
  `reachable_ok`). Assigning `self.ui.x.hidden` at runtime does nothing — the
  declarative UI compiles to a static schema.
- **Every telemetry tag needs `log_on`,** or a 30 s poll either floods the
  historian or logs nothing. Numeric tags accept `Delta`/`Cross`/`Rise`/`Fall`
  only; `AnyChange` is booleans and strings only. `live=True` on fast movers.
- **The provisioning address is sticky.** `AddressHolder` adds it once and keeps
  it while the radio is off-subnet; add/remove per pass would be thousands of
  netlink cycles a day.

- **`spaneng/doover_device_base` is Alpine Linux** (3.23.4 as of 2026-08-21), not
  Debian. Use `apk add --no-cache`, never `apt-get` — it is absent and the build
  fails with exit 127. Several other apps in `~/doover-apps` still carry
  `apt-get` in their Dockerfiles and will fail if rebuilt.
- **Alpine's default `ip` is BusyBox and has no `-j`/JSON.** `netif.py` depends on
  `ip -j addr show` / `ip -j route show`, so the Dockerfile installs real
  `iproute2` (691 KiB). Without it the build passes and every interface lookup
  fails at *runtime*. Verify with
  `docker run --rm --entrypoint ip <image> -j route show default`.

## CI and publishing

`.github/workflows/doover-app.yml` delegates to the shared
`getdoover/workflows/.github/workflows/app.yml@main` and was generated by
`doover app migrate`. **Do not replace it with hand-rolled workflows** — the
shared one covers lint, test, schema validation, multi-arch build, publish and
release through GitHub OIDC, and it discovers apps via `doover app discover`.

`image_name` is `registry.doover.com/apps/ubiquiti_airmax:main` (the Doover
registry, set by `doover app migrate` — not ghcr).
`deployment/docker-compose.yml` must match it.

`organisation_id` is present and `null` on purpose: the API requires the key, and
null is what keeps the app public and unowned. Do not delete the key — a missing
one fails publish with `{"organisation_id":["This field is required."]}`.

**The processor app block must pin `id`.** `doover app publish` PATCHes
`/applications/{id}/` when an id is present and otherwise POSTs an upsert the
control plane resolves from the payload's own identifiers. `ubiquiti_airmax`
survives without an id because it carries a `key`; `ubiquiti_network_overview`
has neither by default, and CI then fails with a bare
`HTTP 404 ... {"detail":"No Application matches the given query."}` that names
nothing. Recover the value with `doover app get <app-name>`. Note the `export-*`
commands rewrite `doover_config.json`, so check a hand-added key survives an
export. Pinned by `test_overview_app_block_pins_an_identifier`.

The processor app needs **no workflow change**: the shared workflow's
`publish-package` job fans out over whatever `doover app discover` reports as
`builds_package`, runs `./build.sh`, and uploads the zip. Verify a change to the
app blocks with `doover app discover . --json` — `ubiquiti_airmax` must come back
`builds_image: true` and `ubiquiti_network_overview` `builds_package: true,
widget: true`.

**`build.sh` deliberately does not zip `src/`.** `uv export` emits the project
itself as a non-editable dependency, so `uv pip install --target packages_export`
already vendors `ubiquiti_network_overview/` as a real package — hence the
handler is `ubiquiti_network_overview.handler`, not `src.…`. Adding `zip
package.zip src` on top puts a second copy of every module in the archive and
which one imports comes down to `sys.path` order.

The zip is ~9 MB because `uv export` vendors every project dependency, including
`asyncssh`, `cryptography`, `jinja2` and `transitions` — all of which only the
AirMax device app uses. Harmless against the lambda size limit; trimming it means
moving the device-only deps into an optional extra and changing how the
Dockerfile installs them.

## Verified hardware facts (Bullet AC IP67, 2026-08-21)

Captured from a live radio on station-1. Don't re-derive these from docs.

- Firmware string is `2WA.ar934x.v8.7.11...` — a **leading board-revision digit**.
  `/etc/version` shows `2WA.v8.7.11` too. Platform is `WA`; parsing the digit as
  part of the code made every AC radio resolve to `Platform.UNKNOWN`. Platform is
  now reported only (tag + UI), never a provisioning gate — the MAC pins the
  radio and `expected_model` covers a MAC typo — but the parse still has to be
  right or the UI shows every AC radio as unknown.
- Discovery replies carry **several `0x02` MAC+IP TLVs**: the LAN address, a
  `169.254/16` link-local, and an entry on a *different* locally-administered MAC
  (`2a:70:...` vs `28:70:...`). `0x01` is the authoritative identity; prefer the
  reply's sender address for reachability.
  Unknown TLVs still to identify: `0x10`, `0x13` (repeats MAC), `0x18`.
- **The `2a:70:...` MAC is `ath1`, not a secondary bridge** — corrected
  2026-08-24 by reading `/sys/class/net/*/address` on a live radio. The full set
  on a Bullet AC IP67, and the reason the network graph joins on the MAC it does:

  | Interface | MAC | What it is |
  |---|---|---|
  | `ath0` | `28:70:4e:e2:96:b9` | the 5 GHz airMAX link — **same as `deviceId` and the discovery MAC** |
  | `ath1` | `2a:70:4e:e2:96:b9` | 2.4 GHz management AP, `ESSID:"BulletAC-IP67:<mac>"`, always Master |
  | `br0`  | `28:70:4e:e2:96:b9` | bridge of eth0 + ath0 |
  | `eth0` | `28:70:4e:e3:96:b9` | LAN — **fourth octet differs** (`e2` → `e3`) |

  So the identity MAC *is* the wireless MAC: a station's `apMac` (its AP's BSSID)
  matches that AP's `radio_mac` directly, with no derivation. Reading `eth0` or
  `ath1` instead would produce an identity nothing ever matches, and every edge
  in the network graph would silently disappear.
- **`mca-dump` returns nothing on this firmware.** Every field comes from the
  flat `mca-status` fallback, so there is no `interfaces[]` array to read and the
  JSON document's field names in `telemetry.py` are untested against this model.
  `deviceId` is the radio's own MAC, upper-cased — normalise before comparing.
- **An unassociated station reports `apMac=00:00:00:00:00:00`.** Confirmed across
  a bench of seven idle radios. `mac_or_none` maps that to `None`; taken
  literally it becomes one phantom node that every idle radio in a fleet appears
  to link to.
- **`netconf` indexes are per-interface and not stable.** On this model
  `netconf.1`=ath0, `netconf.2`=eth0, `netconf.3`=br0. The LAN IP is on br0.
- Keys that do NOT exist on airOS 8: `wireless.1.security` (it is
  `wireless.1.security.type`), `wireless.1.mode`, `wireless.1.authmode`,
  `wireless.1.addmtikie`, `system.hostname`, `dhcpc.status`, `netconf.1.ip`.
- Station mode (`radio.1.mode=managed`) associates via `wpasupplicant.profile.*`;
  `aaa.1.wpa.psk` exists but is not the key in play.
- SSH is dropbear offering `curve25519-sha256`, so the legacy algorithm lists in
  `airos.py` matter for airOS 6, not for AC units.

## Repo conventions

Read `DEVELOPMENT.md` before adding an app. Doover-wide guidance lives in the
global `~/.claude/CLAUDE.md` and the `doover-facts` knowledge base.
