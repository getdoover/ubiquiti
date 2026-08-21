"""Interface helpers, including the guard that stops us cutting our own uplink."""

import asyncio

import pytest

from ubiquiti_common import netif


async def test_missing_iproute2_raises_netif_error_not_filenotfound(monkeypatch):
    """On a dev Mac (and any image without iproute2) callers must see NetifError.

    Leaking FileNotFoundError past the handlers took the discovery sweep down
    instead of falling back to 255.255.255.255.
    """

    async def boom(*args, **kwargs):
        raise FileNotFoundError("ip")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    with pytest.raises(netif.NetifError, match="not available"):
        await netif.interface_addresses("eth0")


async def test_assert_safe_interface_refuses_the_default_route_interface(monkeypatch):
    async def default_iface():
        return "eth0"

    monkeypatch.setattr(netif, "default_route_interface", default_iface)
    with pytest.raises(netif.NetifError, match="default route"):
        await netif.assert_safe_interface("eth0")


async def test_assert_safe_interface_allows_a_different_interface(monkeypatch):
    async def default_iface():
        return "wwan0"

    monkeypatch.setattr(netif, "default_route_interface", default_iface)
    await netif.assert_safe_interface("eth0")  # must not raise


async def test_assert_safe_interface_allows_when_there_is_no_default_route(monkeypatch):
    async def no_default():
        return None

    monkeypatch.setattr(netif, "default_route_interface", no_default)
    await netif.assert_safe_interface("eth0")


@pytest.mark.parametrize(
    "target,expected",
    [
        ("192.168.1.20", "192.168.1.254/24"),
        ("10.4.5.6", "10.4.5.254/24"),
        # Never hand back the radio's own address.
        ("192.168.1.254", "192.168.1.253/24"),
    ],
)
def test_helper_address_avoids_collisions(target, expected):
    assert netif.helper_address_for(target) == expected


async def test_reachable_is_a_noop_when_already_in_subnet(monkeypatch):
    calls = []

    async def already(interface, ip):
        return True

    monkeypatch.setattr(netif, "is_reachable", already)
    monkeypatch.setattr(netif, "add_address", lambda *a, **k: calls.append(a))
    async with netif.reachable("eth0", "192.168.1.20") as cidr:
        assert cidr is None
    assert calls == []


async def test_reachable_adds_and_always_removes_the_address(monkeypatch):
    added, removed = [], []

    async def not_reachable(interface, ip):
        return False

    async def add(interface, cidr, label="doover-prov"):
        added.append(cidr)

    async def remove(interface, cidr):
        removed.append(cidr)

    async def safe_iface():
        return "wwan0"  # reachable() now checks this before adding

    monkeypatch.setattr(netif, "is_reachable", not_reachable)
    monkeypatch.setattr(netif, "default_route_interface", safe_iface)
    monkeypatch.setattr(netif, "add_address", add)
    monkeypatch.setattr(netif, "remove_address", remove)

    with pytest.raises(RuntimeError):
        async with netif.reachable("eth0", "192.168.1.20"):
            raise RuntimeError("provisioning blew up")

    # The temporary address must not survive a failure mid-provision.
    assert added == ["192.168.1.254/24"]
    assert removed == added


# -------------------------------------------------------- shared-interface case
#
# Observed on station-1: br0 is 192.168.1.10/24 AND carries the default route,
# with the radio at 192.168.1.12. Nothing needs adding, so nothing is at risk —
# an unconditional startup refusal would have blocked a perfectly safe setup.


async def test_shared_interface_is_fine_when_radio_already_reachable(monkeypatch):
    async def already(interface, ip):
        return True

    async def default_iface():
        return "br0"

    added = []
    monkeypatch.setattr(netif, "is_reachable", already)
    monkeypatch.setattr(netif, "default_route_interface", default_iface)
    monkeypatch.setattr(netif, "add_address", lambda *a, **k: added.append(a))

    async with netif.reachable("br0", "192.168.1.12") as cidr:
        assert cidr is None
    assert added == [], "must not touch an interface it did not need to touch"


async def test_shared_interface_still_refuses_when_an_address_is_needed(monkeypatch):
    async def not_reachable(interface, ip):
        return False

    async def default_iface():
        return "br0"

    monkeypatch.setattr(netif, "is_reachable", not_reachable)
    monkeypatch.setattr(netif, "default_route_interface", default_iface)

    with pytest.raises(netif.NetifError, match="default route"):
        async with netif.reachable("br0", "10.9.9.9"):
            pass


async def test_carries_default_route(monkeypatch):
    async def default_iface():
        return "br0"

    monkeypatch.setattr(netif, "default_route_interface", default_iface)
    assert await netif.carries_default_route("br0") is True
    assert await netif.carries_default_route("eth1") is False
