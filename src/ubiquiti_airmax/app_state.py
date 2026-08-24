"""Per-target provisioning state.

Deliberately *not* a :class:`pydoover.state.StateMachine`. That models one machine
bound to the application instance, whereas this app tracks N independent targets
that advance through the same lifecycle at different times. A plain record per
target keeps the reconcile loop readable and makes the backoff/attempt accounting
— the part that actually protects the radios — obvious.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class TargetState(str, Enum):
    #: Configured but not yet seen on the wire.
    PENDING = "pending"
    #: Seen, but not reachable over SSH with any configured credential.
    UNREACHABLE = "unreachable"
    #: Reachable, and its live config already satisfies the template.
    CONVERGED = "converged"
    #: Reachable and differing — a push is warranted.
    DRIFTED = "drifted"
    #: Config staged, committed and rebooted; waiting to verify.
    APPLYING = "applying"
    #: Attempts exhausted, or refused on a safety check. Needs operator action.
    FAILED = "failed"
    #: Would have been pushed, but dry run is on.
    WOULD_APPLY = "would_apply"

    @property
    def is_terminal(self) -> bool:
        return self in (TargetState.CONVERGED, TargetState.FAILED)


@dataclass
class TargetRecord:
    """Everything the reconcile loop remembers about one MAC."""

    mac: str
    state: TargetState = TargetState.PENDING
    #: Fingerprint of the desired config. When this changes the operator has
    #: expressed new intent, so the attempt count is cleared and a parked target
    #: gets to try again — this is what replaces the old Reset Failed button.
    intent: str = ""
    #: When the current intent was first seen — a fresh record (container start)
    #: or a changed fingerprint (operator edited the config). The deployment delay
    #: is measured from here, so a fleet-wide deploy holds every radio for the same
    #: window whether the new config arrived by restart or by live update.
    intent_since: float | None = None
    ip: str | None = None
    model: str | None = None
    platform: str | None = None
    firmware: str | None = None
    attempts: int = 0
    last_attempt: float | None = None
    last_seen: float | None = None
    last_diff: str = ""
    message: str = ""
    history: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        """Record a human-readable event, keeping only the recent tail."""
        self.message = message
        self.history.append(f"{time.strftime('%H:%M:%S')} {message}")
        del self.history[:-20]

    def transition(self, state: TargetState, message: str | None = None) -> None:
        if message:
            self.note(message)
        if state is not self.state:
            self.state = state

    def record_attempt(self) -> None:
        self.attempts += 1
        self.last_attempt = time.time()

    def hold_remaining(self, delay_seconds: float) -> float:
        """Seconds left of the deployment delay for the current intent.

        ``0`` means clear to write. Measured from :attr:`intent_since` rather than
        process start, so it covers both ways a new config arrives: a redeploy
        (fresh record) and a live config edit (changed fingerprint).
        """
        if delay_seconds <= 0 or self.intent_since is None:
            return 0.0
        return max(0.0, delay_seconds - (time.time() - self.intent_since))

    def backoff_remaining(self, backoff_seconds: float) -> float:
        """Seconds until this target may be retried. ``0`` means now."""
        if self.last_attempt is None:
            return 0.0
        elapsed = time.time() - self.last_attempt
        return max(0.0, backoff_seconds - elapsed)

    def reset(self, reason: str = "reset") -> None:
        """Clear the failure accounting so the target is retried from scratch."""
        self.attempts = 0
        self.last_attempt = None
        self.state = TargetState.PENDING
        self.note(reason)

    def to_dict(self) -> dict:
        return {
            "mac": self.mac,
            "state": self.state.value,
            "ip": self.ip,
            "model": self.model,
            "platform": self.platform,
            "firmware": self.firmware,
            "attempts": self.attempts,
            "last_seen": self.last_seen,
            "message": self.message,
        }

    @property
    def needs_attention(self) -> bool:
        """True when an operator should look at this target."""
        return self.state is TargetState.FAILED
