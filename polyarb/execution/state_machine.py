"""Order lifecycle with explicit state transitions.

Validates that execution legs move through legal states, preventing
impossible transitions (e.g., rejected -> filled) and making partial
fills an explicit, queryable state.

State diagram (from design doc 3.4.1)::

                      +---------+
                      | created |
                      +----+----+
                           | submit()
                      +----v----+
              +-------+  sent   +-------+
              |       +----+----+       |
              |            |            |
         timeout()    partial_fill() reject()
              |            |            |
         +----v----+  +---v------+  +--v-------+
         |timed_out|  | partial  |  | rejected |
         +----+----+  +---+--+---+  +----------+
              |           |  |
         cancel()    fill()  timeout()
              |           |  |
         +----v-----+ +--v--v---+
         |cancelled | | filled  |
         +----------+ +---------+

Filled legs may also transition to cancelled during partial-failure
unwind (one leg filled, other failed, operator cancels the filled leg).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class LegStatus(StrEnum):
    """Execution leg states.

    String values match the database ``status`` column for backward
    compatibility with existing data and queries.
    """

    CREATED = "pending"
    SENT = "sent"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"


class ExecutionStatus(StrEnum):
    """Top-level execution states."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, current: LegStatus, target: LegStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.name} -> {target.name}")


# Valid transitions: current -> set of allowed next states.
VALID_LEG_TRANSITIONS: dict[LegStatus, frozenset[LegStatus]] = {
    LegStatus.CREATED: frozenset({LegStatus.SENT}),
    LegStatus.SENT: frozenset(
        {
            LegStatus.PARTIAL,
            LegStatus.FILLED,
            LegStatus.REJECTED,
            LegStatus.TIMED_OUT,
            LegStatus.ORPHANED,
        }
    ),
    LegStatus.PARTIAL: frozenset({LegStatus.FILLED}),
    LegStatus.TIMED_OUT: frozenset({LegStatus.CANCELLED}),
    LegStatus.FILLED: frozenset({LegStatus.CANCELLED}),  # unwind path
    # Terminal states -- no transitions out.
    LegStatus.REJECTED: frozenset(),
    LegStatus.CANCELLED: frozenset(),
    LegStatus.ORPHANED: frozenset(),
}

TERMINAL_STATES: frozenset[LegStatus] = frozenset(
    {
        LegStatus.REJECTED,
        LegStatus.CANCELLED,
        LegStatus.ORPHANED,
    }
)


@dataclass(frozen=True)
class StateTransition:
    """Record of a single state change."""

    from_status: LegStatus
    to_status: LegStatus
    timestamp: str
    reason: str | None = None


@dataclass
class OrderStateMachine:
    """Tracks and validates state transitions for a single execution leg.

    Every state change is validated against the transition table and
    recorded with a timestamp.  Invalid transitions raise
    ``InvalidTransitionError``.
    """

    leg_id: int
    execution_id: str
    status: LegStatus = LegStatus.CREATED
    transitions: list[StateTransition] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        """True if the leg is in a final state (no further transitions)."""
        return self.status in TERMINAL_STATES

    def can_transition_to(self, target: LegStatus) -> bool:
        """Check whether *target* is a legal next state."""
        return target in VALID_LEG_TRANSITIONS.get(self.status, frozenset())

    def transition(self, target: LegStatus, *, reason: str | None = None) -> StateTransition:
        """Move to *target*, raising on illegal transition."""
        if not self.can_transition_to(target):
            raise InvalidTransitionError(self.status, target)

        event = StateTransition(
            from_status=self.status,
            to_status=target,
            timestamp=datetime.now(UTC).isoformat(),
            reason=reason,
        )
        self.transitions.append(event)
        old = self.status
        self.status = target

        logger.info(
            "Leg %d (%s): %s -> %s%s",
            self.leg_id,
            self.execution_id,
            old.name,
            target.name,
            f" ({reason})" if reason else "",
        )
        return event

    # -- Convenience methods matching design-doc transitions ------

    def submit(self) -> StateTransition:
        """CREATED -> SENT."""
        return self.transition(LegStatus.SENT)

    def partial_fill(self, *, fill_qty: float | None = None) -> StateTransition:
        """SENT -> PARTIAL."""
        reason = f"partial fill: {fill_qty}" if fill_qty is not None else None
        return self.transition(LegStatus.PARTIAL, reason=reason)

    def fill(self, *, fill_qty: float | None = None) -> StateTransition:
        """SENT -> FILLED  or  PARTIAL -> FILLED."""
        reason = f"filled: {fill_qty}" if fill_qty is not None else None
        return self.transition(LegStatus.FILLED, reason=reason)

    def reject(self, *, error: str | None = None) -> StateTransition:
        """SENT -> REJECTED."""
        return self.transition(LegStatus.REJECTED, reason=error)

    def timeout(self) -> StateTransition:
        """SENT -> TIMED_OUT, or PARTIAL -> FILLED (remainder expires)."""
        if self.status == LegStatus.PARTIAL:
            return self.transition(LegStatus.FILLED, reason="remainder expired")
        return self.transition(LegStatus.TIMED_OUT)

    def cancel(self) -> StateTransition:
        """TIMED_OUT -> CANCELLED  or  FILLED -> CANCELLED (unwind)."""
        return self.transition(LegStatus.CANCELLED)

    def mark_orphaned(self) -> StateTransition:
        """SENT -> ORPHANED (recovery)."""
        return self.transition(LegStatus.ORPHANED, reason="recovery: unresolved")


def validate_transition(current: str, target: str) -> None:
    """Validate a raw status-string transition.

    Raises ``InvalidTransitionError`` if illegal.  This is the
    integration point for repository code that works with plain strings.
    """
    current_status = LegStatus(current)
    target_status = LegStatus(target)
    if target_status not in VALID_LEG_TRANSITIONS.get(current_status, frozenset()):
        raise InvalidTransitionError(current_status, target_status)
