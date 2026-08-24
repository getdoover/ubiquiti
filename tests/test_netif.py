"""Interface helpers.

There is deliberately **no default-route guard**. Adding a secondary address only
adds a connected route for that subnet — it does not disturb an existing default
route. An earlier guard here refused to provision over any interface carrying the
default route, which crash-looped a perfectly healthy install on station-1, whose
`br0` carries both the uplink and the target radio's subnet. `manage_addresses` is
the off-switch.
"""

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


# --------------------------------------------------------------- reachable()


async def test_reachable_is_a_noop_when_already_in_subnet(monkeypatch):
    """The common case on a Doovit whose LAN bridge is already in the radio's
    subnet — nothing is added, so nothing has to be cleaned up."""
    added = []

    async def already(interface, ip):
        return True

    monkeypatch.setattr(netif, "is_reachable", already)
    monkeypatch.setattr(netif, "add_address", lambda *a, **k: added.append(a))

    async with netif.reachable("br0", "192.168.1.12") as cidr:
        assert cidr is None
    assert added == [], "must not touch an interface it did not need to touch"


async def test_reachable_adds_and_always_removes_the_address(monkeypatch):
    added, removed = [], []

    async def not_reachable(interface, ip):
        return False

    async def add(interface, cidr, label="doover-prov"):
        added.append(cidr)

    async def remove(interface, cidr):
        removed.append(cidr)

    monkeypatch.setattr(netif, "is_reachable", not_reachable)
    monkeypatch.setattr(netif, "add_address", add)
    monkeypatch.setattr(netif, "remove_address", remove)

    with pytest.raises(RuntimeError):
        async with netif.reachable("eth0", "192.168.1.20"):
            raise RuntimeError("provisioning blew up")

    # The temporary address must not survive a failure mid-provision.
    assert added == ["192.168.1.254/24"]
    assert removed == added


async def test_off_subnet_radio_on_the_uplink_interface_is_still_provisioned(
    monkeypatch,
):
    """station-1's shape: br0 carries the default route. A factory radio on
    192.168.1.20 there must get its helper address, not be refused."""
    added, removed = [], []

    async def not_reachable(interface, ip):
        return False

    async def add(interface, cidr, label="doover-prov"):
        added.append((interface, cidr))

    async def remove(interface, cidr):
        removed.append((interface, cidr))

    monkeypatch.setattr(netif, "is_reachable", not_reachable)
    monkeypatch.setattr(netif, "add_address", add)
    monkeypatch.setattr(netif, "remove_address", remove)

    async with netif.reachable("br0", "192.168.1.20") as cidr:
        assert cidr == "192.168.1.254/24"
    assert added == [("br0", "192.168.1.254/24")]
    assert removed == added


def test_no_default_route_guard_remains():
    """Pinned: this guard was wrong twice. Do not reintroduce it."""
    import inspect

    for gone in (
        "assert_safe_interface",
        "carries_default_route",
        "default_route_interface",
    ):
        assert not hasattr(netif, gone), f"{gone} should not exist"
    assert "default_route" not in inspect.getsource(netif.reachable)
