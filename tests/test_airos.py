"""Telemetry read path against a scripted radio.

There is no radio simulator in this repo — the push path is validated by hand —
but ``read_telemetry`` is pure command-in, value-out once the SSH session is
stubbed, and the topology identity it produces is what the network overview
joins every edge on. That is worth pinning.
"""

import pytest

from ubiquiti_common.airos import (
    STATIONS_CMD,
    AirOSError,
    STATUS_FLAT_CMD,
    STATUS_JSON_CMD,
    WLAN_MAC_CMD,
    AirOSClient,
    Credential,
)

# Captured from a Bullet AC IP67 on 2WA.v8.7.11: `mca-dump` returns nothing at
# all on this firmware, so every field comes from the flat `mca-status`.
MCA_STATUS = (
    "deviceName=PMP02-STA,deviceId=28:70:4E:E2:96:B9,"
    "firmwareVersion=2WA.ar934x.v8.7.11.46972.220614.0419\n"
    "apMac=00:00:00:00:00:00\n"
    "wlanOpmode=sta-ptp-ac\n"
    "signal=-61\n"
)
ATH0_ADDRESS = "28:70:4e:e2:96:b9\n"


class ScriptedClient(AirOSClient):
    """An AirOSClient whose only real behaviour is answering commands."""

    def __init__(self, responses):
        super().__init__("192.0.2.1", Credential("ubnt", "ubnt"))
        self.responses = responses
        self.commands = []

    async def run(self, command, check=True):
        self.commands.append(command)
        return self.responses.get(command, "")


@pytest.fixture
def scripted():
    # Overrides are passed as a dict, not as kwargs: the command names are
    # module constants, and `build(STATUS_FLAT_CMD=...)` would silently add a
    # key literally named "STATUS_FLAT_CMD" that nothing ever asks for, leaving
    # the default response in place and the test quietly asserting nothing.
    def build(overrides=None):
        responses = {
            STATUS_JSON_CMD: "",  # mca-dump is empty on this firmware
            STATUS_FLAT_CMD: MCA_STATUS,
            STATIONS_CMD: "[]",
            WLAN_MAC_CMD: ATH0_ADDRESS,
        }
        responses.update(overrides or {})
        return ScriptedClient(responses)

    return build


async def test_device_mac_comes_from_the_status_document(scripted):
    client = scripted()
    telemetry = await client.read_telemetry()
    assert telemetry.device_mac == "28:70:4e:e2:96:b9"


async def test_the_fallback_costs_nothing_on_the_common_path(scripted):
    """airOS 8.7.11 always reports deviceId, so the extra round trip is insurance.

    A radio is polled every 30 s forever; a command issued on every pass for a
    value already in hand is a cost paid thousands of times a day.
    """
    client = scripted()
    await client.read_telemetry()
    assert WLAN_MAC_CMD not in client.commands


async def test_falls_back_to_ath0_when_the_status_document_omits_it(scripted):
    client = scripted({STATUS_FLAT_CMD: "wlanOpmode=sta-ptp-ac\nsignal=-61\n"})
    telemetry = await client.read_telemetry()
    assert telemetry.device_mac == "28:70:4e:e2:96:b9"
    assert WLAN_MAC_CMD in client.commands


async def test_ath0_is_the_interface_read_not_eth0_or_ath1():
    """The identity MAC is ath0's.

    On a Bullet AC IP67: ath0 28:70:4e:e2:96:b9 (== deviceId == discovery MAC),
    eth0 28:70:4e:e3:96:b9 (fourth octet differs), ath1 2a:70:4e:e2:96:b9 (the
    2.4 GHz management AP, locally-administered bit set). Reading either of the
    others would produce an identity that no station's `ap_mac` ever matches,
    and every edge in the network graph would silently vanish.
    """
    assert WLAN_MAC_CMD == "cat /sys/class/net/ath0/address"


async def test_a_radio_with_no_status_at_all_is_an_error(scripted):
    client = scripted({STATUS_JSON_CMD: "", STATUS_FLAT_CMD: ""})
    with pytest.raises(AirOSError):
        await client.read_telemetry()
