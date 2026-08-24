"""ICMP round-trip measurement across a wireless link.

Why this exists rather than airOS's own latency field: `wlanTxLatency` (and the
per-station `tx_latency` in `wstalist`) is airMAX *TX queue* latency — how long a
frame waits for its TDMA slot. On an uncongested link it is legitimately 0, and it
is reported at 1 ms resolution, so it cannot express link length either: 10 km of
air is 0.067 ms round trip. It is a congestion signal, not a link-health one.

An ICMP round trip to the radio on the *far* side of the link measures something
an operator can act on — a couple of ms when healthy, climbing or dropping packets
when not. Packet loss is arguably the more valuable half of the result.

This shells out to ``ping`` rather than opening a raw socket: a raw socket needs
``CAP_NET_RAW`` in the container, whereas BusyBox ``ping`` is already present in
the base image and already holds whatever privilege it needs. Output parsing
covers both BusyBox (``round-trip min/avg/max =``) and iputils
(``rtt min/avg/max/mdev =``), since only the former is in the current base image
and that is not a promise about future ones.
"""

import asyncio
import contextlib
import ipaddress
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


class PingError(Exception):
    """``ping`` could not be run at all — a deployment fault, not a link fault.

    Kept distinct from "the host did not answer", which is a
    :class:`PingResult` with loss, because that is a real measurement and this
    is the absence of one.
    """


#: BusyBox: "3 packets transmitted, 3 packets received, 0% packet loss"
#: iputils: "3 packets transmitted, 3 received, 0% packet loss"
_COUNTS = re.compile(
    r"(\d+)\s+packets\s+transmitted,\s*(\d+)\s+(?:packets\s+)?received"
)
#: BusyBox:  "round-trip min/avg/max = 0.097/0.270/0.461 ms"
#: iputils:  "rtt min/avg/max/mdev = 0.097/0.270/0.461/0.100 ms"
#: macOS:    "round-trip min/avg/max/stddev = 0.052/0.075/0.098/0.019 ms"
#:
#: The fourth column's *name* varies by implementation (mdev/stddev), so it is
#: matched generically — pinning it to `mdev` silently returned no RTT at all on
#: a dev Mac. Only min/avg/max are captured; the spread is not published.
#:
#: Absent entirely when every packet is lost, which is why rtt is optional below.
_RTT = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/\w+)?\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)"
)

#: Conservative hostname check. Nothing is passed through a shell — we exec
#: directly — so this is not an injection guard; it is here to fail a typo'd
#: config value fast instead of waiting out a DNS timeout every pass.
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


@dataclass(frozen=True)
class PingResult:
    """The outcome of one ``ping`` run.

    ``rtt_*`` are None when nothing came back; that is not the same as 0.
    """

    target: str
    transmitted: int = 0
    received: int = 0
    rtt_min_ms: float | None = None
    rtt_avg_ms: float | None = None
    rtt_max_ms: float | None = None

    @property
    def loss_pct(self) -> float | None:
        """Derived from the counts rather than read from the output.

        BusyBox and iputils format the loss percentage differently (and iputils
        can print `100%` alongside a nonzero received count when duplicates are
        involved), so the counts are the more trustworthy source.
        """
        if not self.transmitted:
            return None
        return 100.0 * (self.transmitted - self.received) / self.transmitted

    @property
    def ok(self) -> bool:
        return self.received > 0


def parse(output: str, target: str) -> PingResult:
    counts = _COUNTS.search(output or "")
    rtt = _RTT.search(output or "")
    transmitted, received = (
        (int(counts.group(1)), int(counts.group(2))) if counts else (0, 0)
    )
    return PingResult(
        target=target,
        transmitted=transmitted,
        received=received,
        rtt_min_ms=float(rtt.group(1)) if rtt else None,
        rtt_avg_ms=float(rtt.group(2)) if rtt else None,
        rtt_max_ms=float(rtt.group(3)) if rtt else None,
    )


def _valid_target(target: str) -> bool:
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return bool(_HOSTNAME.match(target))


async def ping(
    target: str,
    *,
    count: int = 5,
    interval: float = 0.2,
    wait: float = 1.0,
) -> PingResult:
    """Measure the round trip to ``target``.

    Total wall time is bounded: ``count * interval`` of sending plus a grace
    period for the last reply, enforced with a hard timeout in case ``ping``
    itself hangs. A timeout is reported as total loss rather than raised — a link
    that swallows every packet *is* the measurement.
    """
    target = (target or "").strip()
    if not target or not _valid_target(target):
        raise PingError(f"not a usable ping target: {target!r}")

    args = [
        "-c",
        str(int(count)),
        "-i",
        f"{interval:g}",
        "-W",
        f"{max(1, int(wait))}",
        "-q",
        target,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:  # no ping in the image
        raise PingError("ping is not available in this image") from exc
    except OSError as exc:
        raise PingError(f"could not run ping: {exc}") from exc

    budget = count * interval + wait * 2 + 3.0
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=budget)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        log.warning("ping %s exceeded %.1fs; treating as total loss", target, budget)
        return PingResult(target=target, transmitted=count, received=0)

    text = (out or b"").decode(errors="replace")
    result = parse(text, target)
    if not result.transmitted:
        # No stats block at all: ping ran but could not even start (no route,
        # bad interface). Distinguish it from a clean 100% loss.
        raise PingError(
            f"ping produced no statistics for {target}: {text.strip()[:200]}"
        )
    return result
