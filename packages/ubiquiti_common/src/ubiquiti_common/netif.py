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

    If the address is already reachable this is a no-op — the common case on a
    Doovit whose LAN bridge is already in the radio's subnet. An address is only
    added for a radio that is off-subnet, which in practice means a factory-default
    unit on 192.168.1.20, and it is removed again on exit.

    There is no default-route check. Adding a secondary address does not disturb an
    existing default route: it only adds a connected route for the new subnet. Set
    ``manage_addresses`` false to disable this entirely.
    """
    if await is_reachable(interface, target_ip):
        yield None
        return

    cidr = helper_address_for(target_ip, prefix_len)
    await add_address(interface, cidr)
    try:
        yield cidr
    finally:
        with contextlib.suppress(NetifError):
            await remove_address(interface, cidr)
