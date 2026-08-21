"""Shared Ubiquiti device support for the Doover apps in this repo.

Layout:

* :mod:`ubiquiti_common.models`     — platform/firmware parsing, discovered-device type
* :mod:`ubiquiti_common.cfg`        — airOS ``system.cfg`` parse / merge / diff
* :mod:`ubiquiti_common.discovery`  — UBNT discovery over UDP 10001
* :mod:`ubiquiti_common.netif`      — reaching radios outside our subnet
* :mod:`ubiquiti_common.airos`      — SSH driver (read, stage, commit, reboot)
* :mod:`ubiquiti_common.cli`        — ``airos`` bench tool, no Doover harness needed
"""

from .airos import AirOSClient, AirOSError, Credential, DeviceIdentity, probe
from .models import DiscoveredDevice, Platform, normalise_mac, parse_firmware

__all__ = (
    "AirOSClient",
    "AirOSError",
    "Credential",
    "DeviceIdentity",
    "DiscoveredDevice",
    "Platform",
    "normalise_mac",
    "parse_firmware",
    "probe",
)
