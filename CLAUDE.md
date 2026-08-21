# Ubiquiti Doover apps

Multi-app repo (leachate-telemetry pattern): one Dockerfile, one image, one
`doover_config.json` with a key per app, suffixed entry points in
`pyproject.toml`. Shared device-driver code lives in `packages/ubiquiti_common/`
as a uv workspace member — put anything reusable there rather than importing
across app packages.

## Commands

```bash
uv run pytest tests -q
uv run export-config-airmax     # required after editing app_config.py
uv run export-ui-airmax         # required after editing app_ui.py
uv run airos discover --iface en0
uv run airos status --host <ip> --raw   # verify telemetry field aliases
```

## Apps

| App | Entry point | Package |
|-----|-------------|---------|
| Ubiquiti AirMax (telemetry + autoconfig) | `doover-app-run-airmax` | `src/ubiquiti_airmax/` |

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
- **Values render per-key, not per-file.** `render_overlay` runs Jinja2 over each
  value on its own with `StrictUndefined`, and validates the key against
  `_VALID_KEY` first. Both the app and the `airos` CLI go through it.
- **Dry Run defaults on.** It suppresses writes but never telemetry.
- **Bounded attempts, bounded recovery.** `max_attempts` is the reboot-loop
  guard. A parked radio revives only two ways, both bounded: the intent
  fingerprint changes (operator edited overrides/variables), or
  `failed_retry_after` elapses. Never add an unbounded retry.
  Pinned by `test_non_converging_config_is_bounded`,
  `test_intent_change_revives_a_parked_radio`,
  `test_identical_config_reload_does_not_revive_a_parked_radio`.
- **Only the configured MAC is ever written to.**
- **The interface guard is fatal where it matters.** Adding a helper address to
  the interface carrying the default route can cut the device's own uplink, so
  `netif.reachable()` refuses at the moment an address would be added. Sharing
  that interface is only a *startup warning*: station-1's `br0` is
  192.168.1.10/24 and carries the default route, with the radio at .12 already
  reachable — nothing needs adding, so nothing is at risk. Do not promote this
  back to a startup failure; it would block that (normal) deployment.

## Environment facts

- **Config schema:** pydoover only registers elements declared at *class* level.
  Assigning them in `__init__` exports an empty schema — silently. Array items
  are `config.Object` subclasses with an explicit `name=` on every field.
  A free-form `config.Object` exposes arbitrary keys as synthesised sub-elements,
  not a dict — see `_object_to_dict` in `application.py`.
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

## Verified hardware facts (Bullet AC IP67, 2026-08-21)

Captured from a live radio on station-1. Don't re-derive these from docs.

- Firmware string is `2WA.ar934x.v8.7.11...` — a **leading board-revision digit**.
  `/etc/version` shows `2WA.v8.7.11` too. Platform is `WA`; parsing the digit as
  part of the code made every AC radio resolve to `Platform.UNKNOWN`, which made
  the template platform guard refuse them all.
- Discovery replies carry **several `0x02` MAC+IP TLVs**: the LAN address, a
  `169.254/16` link-local, and a secondary bridge on a *different*
  locally-administered MAC (`2a:70:...` vs `28:70:...`). `0x01` is the
  authoritative identity; prefer the reply's sender address for reachability.
  Unknown TLVs still to identify: `0x10`, `0x13` (repeats MAC), `0x18`.
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
