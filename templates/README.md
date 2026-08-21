# Overlay templates

These files are **starting points for the bench tool**, not the live config. At
runtime the overrides live in the app's Doover config (Config Overrides) for the
one radio that install manages; these files are the `key=value` form the bench
tool reads, and what you transcribe into that list once you're happy with them.

Both use the same renderer: each *value* is rendered as its own Jinja2
expression, so `radio.1.freq={{ freq }}` in a file and a `radio.1.freq` /
`{{ freq }}` row in the UI behave identically.

## Getting the key names right

Don't hand-write these from documentation. airOS key names drift between
airOS 6 and airOS 8, and between platforms within a generation, so the only
authoritative source is the radio in front of you.

```bash
# 1. capture a factory-reset radio
airos dump --host 192.168.1.20 > /tmp/factory.cfg

# 2. configure a radio of the SAME platform by hand through its web UI until it
#    is exactly what you want, then capture it
airos dump --host 192.168.1.20 > /tmp/configured.cfg

# 3. the diff IS your template
airos cfgdiff /tmp/factory.cfg /tmp/configured.cfg > templates/my-template.cfg.j2

# 4. parameterise it: replace site-specific values with {{ jinja }} variables
```

Do this once per platform you deploy — the platform is the first field of the
firmware string, which `airos discover` prints. Note that field can carry a
board-revision digit (`2WA.ar934x.v8...`, seen on Bullet AC IP67); the platform
code is the letters, `WA`.

### Two traps this caught on real hardware

**`netconf` indexes are the radio's, not a convention.** They are per-interface
and the ordering varies. On a Bullet AC IP67, `netconf.1` is `ath0` (wireless),
`netconf.2` is `eth0`, and `netconf.3` is `br0` — the bridge that actually holds
the LAN address. Templating `netconf.1.ip` would put the address on the wireless
interface. Always confirm first:

```bash
airos dump --host <ip> | grep 'netconf\..\.devname'
```

**Plenty of plausible keys simply do not exist.** On airOS 8 there is no
`wireless.1.security` (it is `wireless.1.security.type`), no `system.hostname`,
no `dhcpc.status`. And in station mode the association PSK lives under
`wpasupplicant.profile.*`, not `aaa.*`. A key that doesn't exist gets *created*
as a dead entry rather than erroring, so it silently does nothing.

| Platform | Generation | Bullets |
|----------|-----------|---------|
| `XM`, `XW`, `TI`, `XN` | airOS 6 | Bullet M2/M5, M2HP/M5HP, M2-Ti/M5-Ti |
| `XC`, `WA` | airOS 8 | Bullet AC |

A "Bullet M2HP" ships as either XM or XW depending on hardware revision, so a
template built against one is not guaranteed to fit the other. Set the template's
Platform field so the app refuses a mismatch rather than pushing to it.

## Verifying before you trust it

```bash
airos diff --host <ip> --template templates/my-template.cfg.j2 --var ssid=SPAN-LINK
```

That renders the template and diffs it against the live radio without writing
anything. When the diff shows only what you intend to change, transcribe the
lines into the app's Config Overrides list: the part left of `=` is the Key, the
part right of it is the Value.
