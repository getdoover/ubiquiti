"""airOS ``system.cfg`` parsing, overlay merging and diffing.

airOS stores its whole configuration as a flat, newline-delimited ``key=value``
file. Keys are dotted, and 1-indexed where they represent a list::

    netconf.1.devname=br0
    netconf.1.ip=192.168.1.20
    netconf.2.devname=eth0
    radio.1.freq=5800

There is no nesting, no quoting and no comment syntax in a device-produced file.

We deliberately work in *overlay* mode rather than shipping whole files: read the
device's live ``/tmp/running.cfg``, apply only the keys we care about, write it
back. That inherits every model- and firmware-specific key (chain counts, radio
calibration, per-platform defaults) for free, and means one template survives
both firmware drift and the XM/XW/AC split.
"""

from __future__ import annotations

from typing import Mapping

# Keys the device rewrites after we set them, so they can never compare equal to
# what we pushed. Verifying them would leave a target permanently "unconverged",
# and with auto-reconcile on that becomes a reboot loop. Excluded from
# verification only — they are still pushed.
Config = dict[str, str]


def parse(text: str) -> Config:
    """Parse a ``system.cfg`` / ``running.cfg`` body into an ordered dict.

    Lines without an ``=`` are ignored. A repeated key takes its last value,
    which is what airOS itself does. Insertion order is preserved so that a
    round-trip through :func:`render` stays diffable against the original.
    """
    result: Config = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        result[key.strip()] = value.strip()
    return result


def render(config: Mapping[str, str]) -> str:
    """Render a config mapping back to airOS file format (trailing newline)."""
    return "".join(f"{k}={v}\n" for k, v in config.items())


def merge(base: Mapping[str, str], overlay: Mapping[str, str]) -> Config:
    """Return ``base`` with ``overlay`` applied.

    Keys already in ``base`` are updated in place, preserving their original
    position. Genuinely new keys are appended in sorted order so successive runs
    produce a stable file rather than a churning one.
    """
    merged: Config = dict(base)
    new_keys = []
    for key, value in overlay.items():
        if key in merged:
            merged[key] = value
        else:
            new_keys.append(key)
    for key in sorted(new_keys):
        merged[key] = overlay[key]
    return merged


def diff(
    current: Mapping[str, str],
    desired: Mapping[str, str],
) -> dict[str, tuple[str | None, str]]:
    """Keys in ``desired`` that ``current`` does not already satisfy.

    Returns ``{key: (current_value_or_None, desired_value)}``. Keys present in
    ``current`` but absent from ``desired`` are ignored — this is an overlay, so
    we never propose removing anything the device set for itself.

    There is no exclusion list. Every key in the overlay is both compared and
    written, which is what keeps :func:`diff` and :func:`merge` operating on the
    same key set: an exclusion that applied to one and not the other let a key be
    written without ever being checked. If a key should not be managed, leave it
    out of the overrides.
    """
    result: dict[str, tuple[str | None, str]] = {}
    for key, want in desired.items():
        have = current.get(key)
        if have != want:
            result[key] = (have, want)
    return result


def format_diff(changes: Mapping[str, tuple[str | None, str]]) -> str:
    """Human-readable diff for logs and the Doover UI."""
    if not changes:
        return "(no changes)"
    lines = []
    for key in sorted(changes):
        have, want = changes[key]
        if have is None:
            lines.append(f"+ {key}={want}")
        else:
            lines.append(f"~ {key}: {have} -> {want}")
    return "\n".join(lines)
