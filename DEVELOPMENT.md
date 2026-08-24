# Development

## Layout

```
pyproject.toml               uv workspace root; one entry point per app
Dockerfile                   the image for the DEV (device) apps
build.sh                     package.zip + widget bundle for the PRO (cloud) apps
doover_config.json           one key per app
deployment/
  docker-compose.yml         host networking + NET_ADMIN (both required)
packages/ubiquiti_common/    shared driver — new apps reuse this
  src/ubiquiti_common/
    models.py                platform/firmware parsing, DiscoveredDevice
    cfg.py                   airOS system.cfg parse / merge / diff
    discovery.py             UBNT discovery over UDP 10001
    netif.py                 reaching radios outside our subnet
    airos.py                 SSH driver: read, stage, commit, reboot, telemetry
    telemetry.py             mca-dump / mca-status / wstalist parsing
    cli.py                   `airos` bench tool
src/ubiquiti_airmax/         the auto-config app
  provisioner.py             the reconcile pass — start here
  app_state.py               target state + attempt accounting
src/ubiquiti_network_overview/   the fleet network-overview processor
  application.py             hosts the widget; does no per-device work
  app_config.py              which devices it may read (extended permissions)
  app_ui.py                  one uiRemoteComponent, nothing else
  __init__.py                lambda handler
widget/                      the React remote component the overview renders
  rsbuild.config.ts          module federation; exposes ./NetworkOverviewWidget
  src/NetworkOverviewWidget.tsx
  assets/UbiquitiNetworkWidget.js   build output (gitignored; CI builds it)
templates/                   starting points for overlays (not live config)
assets/icon.png              app icon, referenced by icon_url in doover_config
                             (Ubiquiti Networks lockup, flattened onto white and
                              squared; source is 250x250 so it is kept at 256)
tests/                       unit tests; no hardware needed
```

## Commands

```bash
uv sync --all-extras --dev
uv run pytest tests -q
uv run export-config-airmax     # write config_schema into doover_config.json
uv run export-ui-airmax         # write ui_schema into doover_config.json
uv run export-config-overview   # the same pair, for the network-overview app
uv run export-ui-overview
uv run airos discover --iface en0
uv run airos status --host 192.168.1.20 --raw --samples 2
```

Re-run the matching `export-*` pair after touching an app's `app_config.py` or
`app_ui.py` — an app cannot be published with a stale schema, and CI validates
every app's schemas on every run, not just the one that changed.

## Two kinds of app in one repo

`ubiquiti_airmax` is a `DEV` app: a Docker image that runs on a Doovit.
`ubiquiti_network_overview` is a `PRO` app: a `package.zip` plus a React remote
component, running in the cloud on its own agent.

`doover app discover` sorts them out from the app blocks, and the shared CI
workflow runs the image job for one and the package job for the other — no
workflow changes were needed to add the second app. Check it after editing
`doover_config.json`:

```bash
doover app discover . --json
```

`ubiquiti_airmax` must report `builds_image: true`; `ubiquiti_network_overview`
must report `builds_package: true` and `widget: true`.

## Working on the widget

```bash
cd widget
npm install
npm run build          # -> assets/UbiquitiNetworkWidget.js
npm run watch          # rebuild on change
npm run serve          # serve assets/ on :8003 to host it locally
npm run typecheck      # rspack only strips types; this is what checks them
```

`./build.sh` at the repo root does the Python packaging and then builds the
widget, which is what CI runs.

The widget bundles its own `doover-js` rather than sharing the host's, and
re-provides the host's live client via `peekDooverClient()`. `@tanstack/react-query`
**must** stay a shared singleton or the widget's `useQueryClient()` cannot see
the provider the host renders.

## Testing against a real radio

There is deliberately no radio simulator: the config key names that matter can
only be confirmed against hardware. The unit tests cover parsing, rendering,
diffing and every safety gate with a fake radio; the push/reboot path is
validated by hand.

**On macOS**, `network_mode: host` in Docker Desktop does not give a container
real L2 access, so discovery inside the container will find nothing. Two options:

1. Run the bench CLI natively — it is plain Python and works fine. `--iface`
   needs iproute2 (Linux only), so aim it explicitly instead:

   ```bash
   ifconfig | grep broadcast          # find your LAN broadcast address
   uv run airos discover --broadcast 192.168.50.255 --raw
   ```
2. Run the container on the Doovit or another Linux box.

### A factory radio may hear you but be unable to answer

A factory-default radio sits on `192.168.1.20/24` with no gateway. It *receives*
the discovery broadcast (that is L2) but cannot route a reply to a source address
outside its own subnet, so the sweep comes back empty. On the Doovit the app
solves this with `NET_ADMIN` and a temporary secondary address; on a Mac, do it
by hand:

```bash
sudo ifconfig en0 alias 192.168.1.254 255.255.255.0
uv run airos discover --broadcast 192.168.1.255 --broadcast <lan-broadcast> --raw
sudo ifconfig en0 -alias 192.168.1.254            # remove it afterwards
```

If discovery finds nothing at all, check the ARP table for a Ubiquiti OUI before
assuming the tool is at fault — a radio present but silent has had
`discovery.status=disabled` set, which is a different problem from an absent one.

For iterating on app code on a device, use the fast loop from
`doover-device-app-dev.md` — `docker cp` the changed file into the running
container and restart it. Changes to `pyproject.toml`, the `Dockerfile` or
`deployment/docker-compose.yml` need the full commit → CI → image path.

## CI

`.github/workflows/doover-app.yml` is a thin call into the shared
`getdoover/workflows/.github/workflows/app.yml@main`, generated by
`doover app migrate`. Don't hand-roll workflows here — the shared one already
does lint, test, schema validation, multi-arch image build, publish and release
via GitHub OIDC trusted publishing (no stored Doover credential).

It discovers what to build by running `doover app discover . --json`, and handles
both repo layouts — several apps in one `doover_config.json` (what we use) and
several self-contained app directories. So adding an app to this repo needs no CI
change at all.

Useful inputs on the shared workflow if we need them later: `lint-blocking: true`
once the repo is reliably clean, `runs-on: ubuntu-24.04-arm` to build arm64
natively instead of under QEMU, and `staging: true` to also release to staging.

## Adding a second app to this repo

1. `src/<new_app>/` with the usual `application.py` / `app_config.py` /
   `app_tags.py` / `app_ui.py`.
2. Add its three entry points to `[project.scripts]` with a distinct suffix.
3. Add `src/<new_app>` to `[tool.hatch.build.targets.wheel] packages`.
4. Add a new top-level key to `doover_config.json` with its own `key`,
   `run_command`, `export_config_command` and `export_ui_command`.
5. Put anything shared in `packages/ubiquiti_common/` rather than importing
   across app packages.

No CI change is needed — confirm the new app shows up in
`doover app discover . --json`, which is what the shared workflow keys off.

`doover app discover` lists every app the repo declares and how to build it.

## Gotchas worth knowing

- **The base image is Alpine, not Debian.** Package installs are
  `apk add --no-cache`; `apt-get` exits 127. Alpine's `ip` is BusyBox with no
  JSON support, so the Dockerfile installs real `iproute2` — `netif.py` calls
  `ip -j` and fails at runtime without it. Build and smoke-test locally rather
  than letting CI find out:

  ```bash
  docker build -t ubiquiti-airmax:test .
  docker run --rm --entrypoint ip ubiquiti-airmax:test -j route show default
  docker run --rm --entrypoint python ubiquiti-airmax:test -c "import ubiquiti_airmax"
  ```


- **Legacy SSH crypto.** airOS 6 runs an old dropbear whose kex, cipher and
  host-key algorithms modern asyncssh disables by default. `airos.py` sets the
  algorithm lists explicitly; without them, connections fail at key exchange with
  an unhelpful error.
- **No SFTP.** BusyBox on these radios has no sftp-server, so config is written
  by piping to `cat` over an exec channel. The byte count is verified before
  `cfgmtd` — a truncated write committed to flash means the reset button.
- **Config schema style.** pydoover only registers config elements declared at
  *class* level; assigning them in `__init__` silently exports an empty schema.
  Structured array items are `config.Object` subclasses with explicit `name=` on
  every field, so the JSON keys stay stable and readable.
- **The number on a Ubiquiti label is not a MAC.** It is a batch code followed by
  the MAC: a real Bullet AC IP67 shipped as `2450BJ28704EE29BCB`, whose last 12
  hex digits are `28:70:4e:e2:9b:cb`. `normalise_mac` strips non-hex and keeps the
  trailing 12, so operators can paste the label as-is. Truncation is logged, since
  a typo that lengthens the input silently targets a different radio.
- **No verification exclusions.** Every override is both compared and written.
  An earlier exclusion list applied to `diff` but not `merge`, which let a key be
  written to a radio without ever being checked — so drift on it was invisible. If
  a key should not be managed, leave it out of the overrides. A key that genuinely
  cannot converge is bounded by `max_attempts` and parks with the offending keys
  named.
- **`0000:0000` is airOS's *unset* PSK value, not a mask.** It appears verbatim in
  the factory image config (`/usr/etc/system.cfg`), so a real PSK written to that
  key should read back verbatim. An override of `0000:0000` is a no-op that looks
  like you are managing the key — write a real value or omit it.
- **A station's PSK is not `aaa.1.wpa.psk`.** In `radio.1.mode=managed` the key in
  play is `wpasupplicant.profile.1.network.1.psk` (with
  `wireless.1.security.type` and `...key_mgmt.1.name`). `aaa.1.wpa.psk` is the
  AP-side key and is inert on a station.
