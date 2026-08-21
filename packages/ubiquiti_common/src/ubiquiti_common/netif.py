"""Interface helpers for reaching radios that are not on our subnet.

A factory-default airMAX radio sits on ``192.168.1.20/24``. On a 10.x LAN it will
answer a discovery broadcast (that is L2) but is not *routable* until we put an
address in its subnet on the provisioning interface. That needs ``NET_ADMIN``,
which ``deployment/docker-compose.yml`` grants.

Everything here shells out to ``iproute2`` rather than using a netlink library,
so the same calls can be pasted into an SSH session on the Doovit when
debugging.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


class NetifError(RuntimeError):
    """An ``ip`` invocation failed."""


@dataclass(frozen=True)
class InterfaceAddress:
    address: str
    prefix_len: int
    broadcast: str | None

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.address}/{self.prefix_len}", strict=False)


async def _ip(*args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # iproute2 is Linux-only; on a dev Mac this is the expected path, and
        # discovery still works by falling back to 255.255.255.255.
        raise NetifError("iproute2 (`ip`) is not available on this host") from exc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise NetifError(
            f"ip {' '.join(args)} failed ({proc.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace")


async def interface_addresses(interface: str) -> list[InterfaceAddress]:
    """IPv4 addresses currently configured on ``interface``."""
    raw = json.loads(await _ip("-j", "addr", "show", "dev", interface) or "[]")
    result: list[InterfaceAddress] = []
    for link in raw:
        for info in link.get("addr_info", []):
            if info.get("family") != "inet":
                continue
            result.append(
                InterfaceAddress(
                    address=info["local"],
                    prefix_len=int(info["prefixlen"]),
                    broadcast=info.get("broadcast"),
                )
            )
    return result


async def broadcast_addresses(interface: str) -> list[str]:
    """Per-subnet broadcast addresses to aim discovery probes at."""
    addrs = await interface_addresses(interface)
    out: list[str] = []
    for addr in addrs:
        candidate = addr.broadcast or str(addr.network.broadcast_address)
        if candidate and candidate not in out:
            out.append(candidate)
    return out


async def default_route_interface() -> str | None:
    """The interface carrying the default route, if any."""
    raw = json.loads(await _ip("-j", "route", "show", "default") or "[]")
    for route in raw:
        if route.get("dev"):
            return route["dev"]
    return None


async def carries_default_route(interface: str) -> bool:
    """Whether ``interface`` is the one carrying this device's default route."""
    try:
        return (await default_route_interface()) == interface
    except NetifError:
        return False


async def assert_safe_interface(interface: str) -> None:
    """Refuse to *add an address to* the interface carrying our own uplink.

    Adding ``192.168.1.x/24`` to the interface that carries the Doovit's default
    route can black-hole the Doover connection — and the thing we would lose
    remote access to is the thing doing the provisioning. Fail loudly instead.

    This is deliberately not a startup check. A Doovit commonly bridges its LAN
    and its uplink onto one interface (``br0`` with the default route *and* the
    radio's subnet), where no address needs adding and nothing is at risk.
    """
    default_iface = await default_route_interface()
    if default_iface and default_iface == interface:
        raise NetifError(
            f"refusing to use {interface!r} for provisioning: it carries the default "
            "route, so adding a provisioning address risks cutting this device's own "
            "uplink. Pick a dedicated LAN interface."
        )


async def is_reachable(interface: str, ip: str) -> bool:
    """True if ``ip`` falls inside a subnet already configured on ``interface``."""
    target = ipaddress.ip_address(ip)
    for addr in await interface_addresses(interface):
        if target in addr.network:
            return True
    return False


async def add_address(interface: str, cidr: str, label: str = "doover-prov") -> None:
    await _ip(
        "addr", "add", cidr, "dev", interface, "label", f"{interface}:{label}"[:15]
    )
    log.info("added provisioning address %s to %s", cidr, interface)


async def remove_address(interface: str, cidr: str) -> None:
    await _ip("addr", "del", cidr, "dev", interface)
    log.info("removed provisioning address %s from %s", cidr, interface)


def helper_address_for(target_ip: str, prefix_len: int = 24, host: int = 254) -> str:
    """Pick an address in ``target_ip``'s subnet for us to borrow.

    Defaults to ``.254`` because airOS devices ship on ``.20`` and Ubiquiti's own
    tooling tends to sit low in the range; ``.254`` keeps us out of the way.
    """
    network = ipaddress.ip_network(f"{target_ip}/{prefix_len}", strict=False)
    candidate = network.network_address + host
    if candidate not in network or candidate == ipaddress.ip_address(target_ip):
        candidate = network.network_address + (host - 1)
    return f"{candidate}/{prefix_len}"


@contextlib.asynccontextmanager
async def reachable(interface: str, target_ip: str, prefix_len: int = 24):
    """Ensure ``target_ip`` is routable for the duration of the block.

    If the address is already reachable this is a no-op — which is the common case
    on a Doovit whose LAN bridge is already in the radio's subnet, and is why the
    default-route check below lives here rather than at startup. Only when we
    genuinely have to reshape the interface is it dangerous, so only then do we
    refuse.
    """
    if await is_reachable(interface, target_ip):
        yield None
        return

    # We are about to add an address. THIS is the dangerous case, not merely
    # sharing an interface with the default route.
    await assert_safe_interface(interface)

    cidr = helper_address_for(target_ip, prefix_len)
    await add_address(interface, cidr)
    try:
        yield cidr
    finally:
        with contextlib.suppress(NetifError):
            await remove_address(interface, cidr)
