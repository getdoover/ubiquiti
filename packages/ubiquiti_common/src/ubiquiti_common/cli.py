"""``airos`` — bench tool for working against a real radio.

Drives the same code the Doover app uses, without the app harness, so a radio on
the bench can be discovered, diffed and pushed to directly. This is also how the
TLV table in :mod:`ubiquiti_common.discovery` gets confirmed against real
hardware.

    airos discover --iface en0 --raw
    airos dump --host 192.168.1.20 > factory.cfg
    airos status --host 192.168.1.20 --raw --samples 2
    airos cfgdiff factory.cfg configured.cfg > templates/bullet-m-cpe.cfg
    airos diff --host 192.168.1.20 --template templates/bullet-m-cpe.cfg --var ssid=SPAN
    airos push --host 192.168.1.20 --template templates/bullet-m-cpe.cfg --var ssid=SPAN --commit --reboot
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from jinja2 import StrictUndefined, Template

from . import cfg, discovery, netif
from .airos import AirOSClient, AirOSError, Credential
from .telemetry import ThroughputTracker

log = logging.getLogger("airos")

DEFAULT_CREDENTIALS = [Credential("ubnt", "ubnt")]


def _render(template_path: Path, variables: dict[str, str]) -> cfg.Config:
    """Render a template file into a config overlay.

    A template file is one ``key=value`` per line — exactly the shape ``airos
    cfgdiff`` emits — where each *value* may be a Jinja2 expression. Rendering
    per value rather than over the whole file keeps this identical to what the
    app does with its Overrides list.

    StrictUndefined: a typo'd variable must fail here, not silently render an
    empty value into a config we are about to flash.
    """
    overlay = cfg.parse(template_path.read_text())
    return {
        key: Template(value, undefined=StrictUndefined).render(**variables)
        for key, value in overlay.items()
    }


def _parse_vars(pairs: list[str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--var expects key=value, got {pair!r}")
        variables[key.strip()] = value
    return variables


def _credentials(args) -> list[Credential]:
    if args.user:
        return [Credential(args.user, args.password or "")]
    return DEFAULT_CREDENTIALS


async def cmd_discover(args) -> int:
    broadcasts = list(args.broadcast) or None
    if not broadcasts and args.iface:
        try:
            broadcasts = await netif.broadcast_addresses(args.iface)
        except netif.NetifError as exc:
            # --iface needs iproute2, which is Linux-only. On a dev Mac, pass
            # --broadcast explicitly (see `ifconfig`).
            log.warning(
                "could not read addresses for %s (%s); using 255.255.255.255",
                args.iface,
                exc,
            )
    devices = await discovery.discover(broadcast_addrs=broadcasts, timeout=args.timeout)
    if not devices:
        print("no devices answered", file=sys.stderr)
        return 1
    for device in devices:
        print(device.describe())
        if args.raw:
            for tlv_type in sorted(device.raw_tlvs):
                for value in device.raw_tlvs[tlv_type]:
                    printable = value.decode("utf-8", errors="replace").strip("\x00")
                    print(
                        f"    0x{tlv_type:02x} [{len(value):3d}] {value.hex()}  {printable!r}"
                    )
    return 0


async def cmd_dump(args) -> int:
    async with AirOSClient(args.host, _credentials(args)[0]) as client:
        identity = await client.identify()
        print(
            f"# {args.host} {identity.firmware} ({identity.platform.value})",
            file=sys.stderr,
        )
        sys.stdout.write(cfg.render(await client.read_config()))
    return 0


def cmd_cfgdiff(args) -> int:
    """Diff two captured configs — the output *is* a starting template."""
    before = cfg.parse(Path(args.before).read_text())
    after = cfg.parse(Path(args.after).read_text())
    changes = cfg.diff(before, after)
    if not changes:
        print("# identical", file=sys.stderr)
        return 0
    print(
        f"# {len(changes)} key(s) differ: {args.before} -> {args.after}",
        file=sys.stderr,
    )
    for key in sorted(changes):
        print(f"{key}={changes[key][1]}")
    return 0


async def cmd_diff(args) -> int:
    desired = _render(Path(args.template), _parse_vars(args.var))
    async with AirOSClient(args.host, _credentials(args)[0]) as client:
        current = await client.read_config()
    changes = cfg.diff(current, desired, exclude=cfg.DEFAULT_VERIFY_EXCLUDE)
    print(cfg.format_diff(changes))
    return 0


async def cmd_push(args) -> int:
    desired = _render(Path(args.template), _parse_vars(args.var))
    async with AirOSClient(args.host, _credentials(args)[0]) as client:
        identity = await client.identify()
        print(
            f"{args.host}: {identity.firmware} ({identity.platform.value})",
            file=sys.stderr,
        )
        current = await client.read_config()
        changes = cfg.diff(current, desired, exclude=cfg.DEFAULT_VERIFY_EXCLUDE)
        if not changes:
            print("already converged, nothing to do", file=sys.stderr)
            return 0
        print(cfg.format_diff(changes), file=sys.stderr)
        if not args.commit:
            print("\n(dry run — pass --commit to write)", file=sys.stderr)
            return 0
        await client.write_config(cfg.merge(current, desired))
        await client.commit()
        if args.reboot:
            await client.reboot()
            print(
                "rebooted; re-run `airos diff` once it is back to verify",
                file=sys.stderr,
            )
        else:
            print(
                "committed but NOT rebooted — config applies on next boot",
                file=sys.stderr,
            )
    return 0


async def cmd_status(args) -> int:
    """Read live telemetry. ``--raw`` dumps every status field the radio reported.

    The raw dump is the point of this command: the field-name aliases in
    telemetry.py are reconstructed, not documented, so they need confirming
    against real hardware.
    """
    tracker = ThroughputTracker() if args.samples > 1 else None
    async with AirOSClient(args.host, _credentials(args)[0]) as client:
        for sample in range(args.samples):
            if sample:
                await asyncio.sleep(args.interval)
            tel = await client.read_telemetry(tracker=tracker)
            print(f"--- sample {sample + 1}/{args.samples} ---")
            for label, value, unit in (
                ("model", tel.model, ""),
                ("firmware", tel.firmware, ""),
                ("hostname", tel.hostname, ""),
                ("mode", tel.wireless_mode, ""),
                ("essid", tel.essid, ""),
                ("frequency", tel.frequency_mhz, "MHz"),
                ("chan width", tel.chanbw_mhz, "MHz"),
                ("signal", tel.signal_dbm, "dBm"),
                ("noise floor", tel.noise_dbm, "dBm"),
                ("snr", tel.snr_db, "dB"),
                ("ccq", tel.ccq_pct, "%"),
                ("quality", tel.quality_pct, "%"),
                ("capacity", tel.capacity_pct, "%"),
                ("tx rate", tel.tx_rate_mbps, "Mbps"),
                ("rx rate", tel.rx_rate_mbps, "Mbps"),
                ("tx thru", tel.tx_throughput_kbps, "kbps"),
                ("rx thru", tel.rx_throughput_kbps, "kbps"),
                ("uptime", tel.uptime_s, "s"),
                ("ap mac", tel.ap_mac, ""),
                ("distance", tel.distance_m, "m"),
                ("stations", tel.station_count, ""),
            ):
                if value is not None:
                    print(f"  {label:12} {value} {unit}".rstrip())
            for station in tel.stations:
                print(f"    station: {station.describe()}")
            if args.raw:
                print("  raw fields the radio reported:")
                for key in sorted(tel.raw):
                    print(f"    {key} = {tel.raw[key]!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airos", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_host_args(p):
        p.add_argument("--host", required=True, help="radio IP address")
        p.add_argument("--user", help="SSH username (default: try ubnt/ubnt)")
        p.add_argument("--password", help="SSH password")

    p = sub.add_parser("discover", help="broadcast a UBNT discovery probe")
    p.add_argument(
        "--iface", help="interface to derive broadcast addresses from (Linux)"
    )
    p.add_argument(
        "--broadcast",
        action="append",
        default=[],
        metavar="ADDR",
        help="broadcast address to probe; repeatable. Overrides --iface.",
    )
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument(
        "--raw", action="store_true", help="dump raw TLVs to confirm field meanings"
    )
    p.set_defaults(func=cmd_discover, is_async=True)

    p = sub.add_parser("dump", help="print a radio's live running.cfg")
    add_host_args(p)
    p.set_defaults(func=cmd_dump, is_async=True)

    p = sub.add_parser("status", help="read live telemetry from a radio")
    add_host_args(p)
    p.add_argument(
        "--raw", action="store_true", help="dump every reported status field"
    )
    p.add_argument(
        "--samples",
        type=int,
        default=1,
        help="take N samples; >1 enables throughput, which needs two readings",
    )
    p.add_argument(
        "--interval", type=float, default=5.0, help="seconds between samples"
    )
    p.set_defaults(func=cmd_status, is_async=True)

    p = sub.add_parser("cfgdiff", help="diff two captured configs to build a template")
    p.add_argument("before")
    p.add_argument("after")
    p.set_defaults(func=cmd_cfgdiff, is_async=False)

    p = sub.add_parser("diff", help="render a template and diff it against a radio")
    add_host_args(p)
    p.add_argument("--template", required=True)
    p.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    p.set_defaults(func=cmd_diff, is_async=True)

    p = sub.add_parser("push", help="render, diff and optionally write a template")
    add_host_args(p)
    p.add_argument("--template", required=True)
    p.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--commit", action="store_true", help="actually write and cfgmtd")
    p.add_argument("--reboot", action="store_true", help="reboot after committing")
    p.set_defaults(func=cmd_push, is_async=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.is_async:
            return asyncio.run(args.func(args))
        return args.func(args)
    except AirOSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
