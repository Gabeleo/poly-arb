"""Tests for the order state machine — transition validation and lifecycle tracking."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.execution.state_machine import (
    TERMINAL_STATES,
    VALID_LEG_TRANSITIONS,
    ExecutionStatus,
    InvalidTransitionError,
    LegStatus,
    OrderStateMachine,
    StateTransition,
    validate_transition,
)

# ── LegStatus enum ──────────────────────────────────────────


class TestLegStatus:
    def test_values_match_db_strings(self):
        """Enum values must match the status column in execution_legs."""
        assert LegStatus.CREATED.value == "pending"
        assert LegStatus.SENT.value == "sent"
        assert LegStatus.PARTIAL.value == "partial"
        assert LegStatus.FILLED.value == "filled"
        assert LegStatus.REJECTED.value == "failed"
        assert LegStatus.TIMED_OUT.value == "timed_out"
        assert LegStatus.CANCELLED.value == "cancelled"
        assert LegStatus.ORPHANED.value == "orphaned"

    def test_is_str(self):
        """LegStatus is a str enum — usable wherever a string is expected."""
        assert isinstance(LegStatus.FILLED, str)
        assert LegStatus.FILLED == "filled"

    def test_all_states_in_transition_table(self):
        """Every status has an entry in the transition table."""
        for status in LegStatus:
            assert status in VALID_LEG_TRANSITIONS


class TestExecutionStatus:
    def test_values_match_db_strings(self):
        assert ExecutionStatus.PENDING.value == "pending"
        assert ExecutionStatus.COMPLETED.value == "completed"
        assert ExecutionStatus.FAILED.value == "failed"


# ── InvalidTransitionError ───────────────────────────────────


class TestInvalidTransitionError:
    def test_message_format(self):
        exc = InvalidTransitionError(LegStatus.REJECTED, LegStatus.FILLED)
        assert "REJECTED" in str(exc)
        assert "FILLED" in str(exc)

    def test_attributes(self):
        exc = InvalidTransitionError(LegStatus.CREATED, LegStatus.FILLED)
        assert exc.current == LegStatus.CREATED
        assert exc.target == LegStatus.FILLED


# ── Valid transitions ────────────────────────────────────────


class TestValidTransitions:
    """Every designed transition must be allowed."""

    def test_created_to_sent(self):
        validate_transition("pending", "sent")

    def test_sent_to_filled(self):
        validate_transition("sent", "filled")

    def test_sent_to_rejected(self):
        validate_transition("sent", "failed")

    def test_sent_to_partial(self):
        validate_transition("sent", "partial")

    def test_sent_to_timed_out(self):
        validate_transition("sent", "timed_out")

    def test_sent_to_orphaned(self):
        validate_transition("sent", "orphaned")

    def test_partial_to_filled(self):
        validate_transition("partial", "filled")

    def test_timed_out_to_cancelled(self):
        validate_transition("timed_out", "cancelled")

    def test_filled_to_cancelled_unwind(self):
        """Filled legs can be cancelled during partial-failure unwind."""
        validate_transition("filled", "cancelled")


# ── Invalid transitions ──────────────────────────────────────


class TestInvalidTransitions:
    """Transitions not in the table must raise."""

    def test_created_to_filled_skips_sent(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("pending", "filled")

    def test_created_to_rejected(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("pending", "failed")

    def test_filled_to_sent_backward(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("filled", "sent")

    def test_rejected_to_sent(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("failed", "sent")

    def test_rejected_to_filled(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("failed", "filled")

    def test_cancelled_to_anything(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("cancelled", "sent")

    def test_orphaned_to_anything(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("orphaned", "filled")

    def test_partial_to_rejected(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("partial", "failed")

    def test_partial_to_sent_backward(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("partial", "sent")

    def test_timed_out_to_filled(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("timed_out", "filled")

    def test_unknown_status_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_transition("bogus", "sent")


# ── Terminal states ──────────────────────────────────────────


class TestTerminalStates:
    def test_terminal_states_have_no_outgoing_transitions(self):
        for status in TERMINAL_STATES:
            assert VALID_LEG_TRANSITIONS[status] == frozenset()

    def test_filled_is_not_strictly_terminal(self):
        """FILLED allows one outgoing transition (unwind to CANCELLED)."""
        assert LegStatus.FILLED not in TERMINAL_STATES

    def test_rejected_is_terminal(self):
        assert LegStatus.REJECTED in TERMINAL_STATES

    def test_cancelled_is_terminal(self):
        assert LegStatus.CANCELLED in TERMINAL_STATES

    def test_orphaned_is_terminal(self):
        assert LegStatus.ORPHANED in TERMINAL_STATES

    def test_sent_is_not_terminal(self):
        assert LegStatus.SENT not in TERMINAL_STATES


# ── OrderStateMachine ────────────────────────────────────────


class TestOrderStateMachine:
    def test_initial_state_is_created(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        assert sm.status == LegStatus.CREATED

    def test_submit(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        event = sm.submit()
        assert sm.status == LegStatus.SENT
        assert event.from_status == LegStatus.CREATED
        assert event.to_status == LegStatus.SENT

    def test_fill_from_sent(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.fill(fill_qty=10.0)
        assert sm.status == LegStatus.FILLED
        # FILLED is not strictly terminal — allows unwind to CANCELLED
        assert not sm.is_terminal

    def test_reject(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        event = sm.reject(error="API timeout")
        assert sm.status == LegStatus.REJECTED
        assert event.reason == "API timeout"
        assert sm.is_terminal  # REJECTED is truly terminal

    def test_partial_fill_then_fill(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.partial_fill(fill_qty=5.0)
        assert sm.status == LegStatus.PARTIAL
        assert not sm.is_terminal
        sm.fill(fill_qty=10.0)
        assert sm.status == LegStatus.FILLED

    def test_timeout_from_sent(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.timeout()
        assert sm.status == LegStatus.TIMED_OUT

    def test_timeout_from_partial_becomes_filled(self):
        """Partial timeout means remainder expired — position becomes final."""
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.partial_fill(fill_qty=3.0)
        event = sm.timeout()
        assert sm.status == LegStatus.FILLED
        assert event.reason == "remainder expired"

    def test_cancel_from_timed_out(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.timeout()
        sm.cancel()
        assert sm.status == LegStatus.CANCELLED

    def test_cancel_from_filled_unwind(self):
        """Filled legs can be cancelled during partial-failure unwind."""
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.fill()
        sm.cancel()
        assert sm.status == LegStatus.CANCELLED

    def test_mark_orphaned(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        event = sm.mark_orphaned()
        assert sm.status == LegStatus.ORPHANED
        assert event.reason == "recovery: unresolved"

    def test_is_terminal_false_for_nonterminal(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        assert not sm.is_terminal
        sm.submit()
        assert not sm.is_terminal

    def test_can_transition_to(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        assert sm.can_transition_to(LegStatus.SENT)
        assert not sm.can_transition_to(LegStatus.FILLED)

    def test_transitions_recorded(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.fill()
        assert len(sm.transitions) == 2
        assert sm.transitions[0].to_status == LegStatus.SENT
        assert sm.transitions[1].to_status == LegStatus.FILLED

    def test_transition_has_timestamp(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        event = sm.submit()
        assert event.timestamp is not None
        assert "T" in event.timestamp  # ISO format

    def test_transition_has_reason(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        event = sm.reject(error="rate limited")
        assert event.reason == "rate limited"

    def test_invalid_transition_raises(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.fill()  # can't go CREATED -> FILLED
        assert exc_info.value.current == LegStatus.CREATED
        assert exc_info.value.target == LegStatus.FILLED

    def test_double_fill_raises(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.fill()
        with pytest.raises(InvalidTransitionError):
            sm.fill()  # FILLED is terminal (except cancel for unwind)

    def test_reject_after_fill_raises(self):
        sm = OrderStateMachine(leg_id=1, execution_id="x")
        sm.submit()
        sm.fill()
        with pytest.raises(InvalidTransitionError):
            sm.reject()


# ── StateTransition dataclass ────────────────────────────────


class TestStateTransition:
    def test_frozen(self):
        t = StateTransition(
            from_status=LegStatus.CREATED,
            to_status=LegStatus.SENT,
            timestamp="2026-04-09T00:00:00",
        )
        with pytest.raises(AttributeError):
            t.reason = "test"  # type: ignore[misc]

    def test_optional_reason(self):
        t = StateTransition(
            from_status=LegStatus.SENT,
            to_status=LegStatus.FILLED,
            timestamp="2026-04-09T00:00:00",
        )
        assert t.reason is None

    def test_with_reason(self):
        t = StateTransition(
            from_status=LegStatus.SENT,
            to_status=LegStatus.REJECTED,
            timestamp="2026-04-09T00:00:00",
            reason="exchange rejected",
        )
        assert t.reason == "exchange rejected"


# ── Full lifecycle scenarios ─────────────────────────────────


class TestLifecycleScenarios:
    """End-to-end state machine scenarios matching real execution flows."""

    def test_happy_path(self):
        """created -> sent -> filled."""
        sm = OrderStateMachine(leg_id=1, execution_id="exec-1")
        sm.submit()
        sm.fill(fill_qty=10.0)
        assert sm.status == LegStatus.FILLED
        assert len(sm.transitions) == 2

    def test_rejection_path(self):
        """created -> sent -> rejected."""
        sm = OrderStateMachine(leg_id=2, execution_id="exec-2")
        sm.submit()
        sm.reject(error="insufficient funds")
        assert sm.status == LegStatus.REJECTED

    def test_partial_fill_path(self):
        """created -> sent -> partial -> filled."""
        sm = OrderStateMachine(leg_id=3, execution_id="exec-3")
        sm.submit()
        sm.partial_fill(fill_qty=5.0)
        sm.fill(fill_qty=10.0)
        assert sm.status == LegStatus.FILLED
        assert len(sm.transitions) == 3

    def test_timeout_cancel_path(self):
        """created -> sent -> timed_out -> cancelled."""
        sm = OrderStateMachine(leg_id=4, execution_id="exec-4")
        sm.submit()
        sm.timeout()
        sm.cancel()
        assert sm.status == LegStatus.CANCELLED

    def test_partial_timeout_path(self):
        """created -> sent -> partial -> filled (remainder expired)."""
        sm = OrderStateMachine(leg_id=5, execution_id="exec-5")
        sm.submit()
        sm.partial_fill(fill_qty=3.0)
        sm.timeout()
        assert sm.status == LegStatus.FILLED

    def test_unwind_path(self):
        """created -> sent -> filled -> cancelled (partial-failure unwind)."""
        sm = OrderStateMachine(leg_id=6, execution_id="exec-6")
        sm.submit()
        sm.fill()
        sm.cancel()
        assert sm.status == LegStatus.CANCELLED
        assert len(sm.transitions) == 3

    def test_orphan_path(self):
        """created -> sent -> orphaned (recovery)."""
        sm = OrderStateMachine(leg_id=7, execution_id="exec-7")
        sm.submit()
        sm.mark_orphaned()
        assert sm.status == LegStatus.ORPHANED


# ── Repository integration ───────────────────────────────────


class TestRepositoryValidation:
    """Verify the repository validates transitions via the state machine."""

    @pytest.fixture
    def repo(self):
        from polyarb.db.engine import create_engine
        from polyarb.db.models import metadata
        from polyarb.db.repositories.executions import SqliteExecutionRepository

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        metadata.create_all(engine)
        repo = SqliteExecutionRepository(engine)
        yield repo
        engine.dispose()
        os.unlink(path)

    def _create_leg(self, repo) -> int:
        """Create an execution with one leg, return leg row_id."""
        repo.record_execution("exec-test", "mk-test", 1)
        return repo.record_attempt("exec-test", 0, "kalshi", "T-1", "yes", "buy", 0.42, 10.0)

    def test_mark_sent_validates_pending_to_sent(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)  # pending -> sent: OK

    def test_mark_sent_rejects_double_sent(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)
        with pytest.raises(InvalidTransitionError):
            repo.mark_sent(row_id)  # sent -> sent: invalid

    def test_record_result_validates_sent_to_filled(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)
        repo.record_result(row_id, "ord-1", "filled")  # sent -> filled: OK

    def test_record_result_rejects_pending_to_filled(self, repo):
        row_id = self._create_leg(repo)
        with pytest.raises(InvalidTransitionError):
            repo.record_result(row_id, "ord-1", "filled")  # pending -> filled: invalid

    def test_record_cancel_validates_filled_to_cancelled(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)
        repo.record_result(row_id, "ord-1", "filled")
        repo.record_cancel(row_id, "cancelled")  # filled -> cancelled: OK

    def test_record_cancel_rejects_sent_to_cancelled(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)
        with pytest.raises(InvalidTransitionError):
            repo.record_cancel(row_id, "cancelled")  # sent -> cancelled: invalid

    def test_mark_orphaned_validates_sent_to_orphaned(self, repo):
        row_id = self._create_leg(repo)
        repo.mark_sent(row_id)
        repo.mark_orphaned(row_id)  # sent -> orphaned: OK

    def test_mark_orphaned_rejects_pending_to_orphaned(self, repo):
        row_id = self._create_leg(repo)
        with pytest.raises(InvalidTransitionError):
            repo.mark_orphaned(row_id)  # pending -> orphaned: invalid
