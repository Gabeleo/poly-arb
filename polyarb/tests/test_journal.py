"""Tests for ExecutionJournal — SQLite-backed durable order tracking."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.execution.journal import ExecutionJournal


@pytest.fixture
def journal():
    """Create a journal backed by a temp file, cleaned up after test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    j = ExecutionJournal(db_path=path)
    yield j
    j.close()
    os.unlink(path)


# ── Schema ─────────────────────────────────────────────────────


def test_tables_created(journal: ExecutionJournal):
    """Both tables and indexes exist after init."""
    tables = journal._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in tables}
    assert "executions" in names
    assert "execution_legs" in names


def test_wal_mode(journal: ExecutionJournal):
    row = journal._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


# ── Record lifecycle ───────────────────────────────────────────


def test_full_success_lifecycle(journal: ExecutionJournal):
    """Record execution → attempt → sent → result → completion."""
    journal.record_execution("exec-1", "match-key-1", 1)
    row_id = journal.record_attempt(
        "exec-1", 0, "kalshi", "TICKER-1", "yes", "buy", 0.42, 10.0,
    )
    journal.mark_sent(row_id)
    journal.record_result(row_id, "order-abc", "filled", fill_qty=10.0)
    journal.record_completion("exec-1", True, 0.05)

    # Verify execution row
    exec_row = journal._conn.execute(
        "SELECT * FROM executions WHERE execution_id='exec-1'"
    ).fetchone()
    assert exec_row["status"] == "completed"
    assert exec_row["profit"] == 0.05
    assert exec_row["completed_at"] is not None

    # Verify leg row
    leg = journal._conn.execute(
        "SELECT * FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "filled"
    assert leg["order_id"] == "order-abc"
    assert leg["fill_qty"] == 10.0


def test_failure_lifecycle(journal: ExecutionJournal):
    """Leg failure records error and marks execution failed."""
    journal.record_execution("exec-2", "match-key-2", 1)
    row_id = journal.record_attempt(
        "exec-2", 0, "polymarket", "TOKEN-1", "no", "buy", 0.55, 5.0,
    )
    journal.mark_sent(row_id)
    journal.record_result(row_id, None, "failed", error="API timeout")
    journal.record_completion("exec-2", False)

    leg = journal._conn.execute(
        "SELECT * FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "failed"
    assert leg["order_id"] is None
    assert leg["error"] == "API timeout"

    exec_row = journal._conn.execute(
        "SELECT * FROM executions WHERE execution_id='exec-2'"
    ).fetchone()
    assert exec_row["status"] == "failed"


def test_cancel_lifecycle(journal: ExecutionJournal):
    """Cancellation updates leg status."""
    journal.record_execution("exec-3", "mk-3", 2)
    row_id = journal.record_attempt(
        "exec-3", 0, "kalshi", "T-1", "yes", "buy", 0.30, 10.0,
    )
    journal.mark_sent(row_id)
    journal.record_result(row_id, "k-ord-1", "filled")
    journal.record_cancel(row_id, "cancelled")

    leg = journal._conn.execute(
        "SELECT * FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "cancelled"


# ── Orphan detection ──────────────────────────────────────────


def test_orphan_detection(journal: ExecutionJournal):
    """Legs left in 'sent' status are returned as orphans."""
    journal.record_execution("exec-4", "mk-4", 2)
    r1 = journal.record_attempt("exec-4", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    journal.mark_sent(r1)
    r2 = journal.record_attempt("exec-4", 1, "polymarket", "T-2", "no", "buy", 0.35, 10.0)
    journal.mark_sent(r2)

    # Complete only the first leg
    journal.record_result(r1, "k-1", "filled")

    orphans = journal.get_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == r2
    assert orphans[0]["platform"] == "polymarket"


def test_no_orphans_when_all_completed(journal: ExecutionJournal):
    journal.record_execution("exec-5", "mk-5", 1)
    r = journal.record_attempt("exec-5", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    journal.mark_sent(r)
    journal.record_result(r, "k-1", "filled")

    assert journal.get_orphans() == []


def test_mark_orphaned(journal: ExecutionJournal):
    """mark_orphaned transitions sent → orphaned and removes from get_orphans."""
    journal.record_execution("exec-6", "mk-6", 1)
    r = journal.record_attempt("exec-6", 0, "polymarket", "T-1", "no", "buy", 0.50, 5.0)
    journal.mark_sent(r)

    assert len(journal.get_orphans()) == 1

    journal.mark_orphaned(r)
    assert journal.get_orphans() == []

    leg = journal._conn.execute(
        "SELECT status FROM execution_legs WHERE id=?", (r,)
    ).fetchone()
    assert leg["status"] == "orphaned"


# ── count_by_status ───────────────────────────────────────────


def test_count_by_status(journal: ExecutionJournal):
    journal.record_execution("exec-7", "mk-7", 2)
    r1 = journal.record_attempt("exec-7", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    r2 = journal.record_attempt("exec-7", 1, "polymarket", "T-2", "no", "buy", 0.35, 10.0)
    journal.mark_sent(r1)
    journal.mark_sent(r2)
    journal.record_result(r1, "k-1", "filled")

    assert journal.count_by_status("sent") == 1
    assert journal.count_by_status("filled") == 1
    assert journal.count_by_status("pending") == 0


# ── get_history ───────────────────────────────────────────────


def test_get_history(journal: ExecutionJournal):
    """History returns executions with nested legs."""
    journal.record_execution("exec-h1", "mk-h1", 2)
    journal.record_attempt("exec-h1", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    journal.record_attempt("exec-h1", 1, "polymarket", "T-2", "no", "buy", 0.35, 10.0)
    journal.record_completion("exec-h1", True, 0.05)

    journal.record_execution("exec-h2", "mk-h2", 1)
    journal.record_attempt("exec-h2", 0, "kalshi", "T-3", "no", "buy", 0.60, 5.0)
    journal.record_completion("exec-h2", False)

    history = journal.get_history(limit=10)
    assert len(history) == 2
    # Most recent first
    assert history[0]["execution_id"] == "exec-h2"
    assert len(history[0]["legs"]) == 1
    assert history[1]["execution_id"] == "exec-h1"
    assert len(history[1]["legs"]) == 2


def test_get_history_limit(journal: ExecutionJournal):
    """Limit caps the number of executions returned."""
    for i in range(5):
        journal.record_execution(f"exec-lim-{i}", f"mk-{i}", 1)
        journal.record_attempt(f"exec-lim-{i}", 0, "kalshi", "T", "yes", "buy", 0.40, 10.0)

    history = journal.get_history(limit=3)
    assert len(history) == 3


# ── Idempotency / uniqueness ─────────────────────────────────


def test_duplicate_execution_id_rejected(journal: ExecutionJournal):
    journal.record_execution("exec-dup", "mk-dup", 1)
    with pytest.raises(Exception):
        journal.record_execution("exec-dup", "mk-dup", 1)


def test_duplicate_leg_index_rejected(journal: ExecutionJournal):
    journal.record_execution("exec-legdup", "mk-ld", 2)
    journal.record_attempt("exec-legdup", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    with pytest.raises(Exception):
        journal.record_attempt("exec-legdup", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
