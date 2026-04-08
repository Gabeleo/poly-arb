"""Tests for orphan detection and resolution (execution/recovery.py)."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.execution.journal import ExecutionJournal
from polyarb.execution.recovery import check_orphans, resolve_orphan


@pytest.fixture
def journal():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    j = ExecutionJournal(db_path=path)
    yield j
    j.close()
    os.unlink(path)


def _create_orphan(journal: ExecutionJournal, platform: str = "kalshi", ticker: str = "T-1"):
    """Helper: create a leg stuck in 'sent' status."""
    journal.record_execution("exec-orph", "mk-o", 1)
    row_id = journal.record_attempt("exec-orph", 0, platform, ticker, "yes", "buy", 0.40, 10.0)
    journal.mark_sent(row_id)
    return row_id


# ── check_orphans ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_orphans(journal: ExecutionJournal):
    orphans = await check_orphans(journal)
    assert orphans == []


@pytest.mark.asyncio
async def test_orphans_detected(journal: ExecutionJournal):
    _create_orphan(journal)
    orphans = await check_orphans(journal)
    assert len(orphans) == 1
    assert orphans[0]["ticker"] == "T-1"


# ── resolve_orphan ────────────────────────────────────────────


class FakeKalshiWithPosition:
    async def get_positions(self, ticker: str = "") -> list[dict]:
        return [{"ticker": ticker, "quantity": 10}]


class FakeKalshiNoPosition:
    async def get_positions(self, ticker: str = "") -> list[dict]:
        return []


class FakeKalshiError:
    async def get_positions(self, ticker: str = "") -> list[dict]:
        raise RuntimeError("Exchange unreachable")


@pytest.mark.asyncio
async def test_resolve_kalshi_with_position(journal: ExecutionJournal):
    """Orphan resolved as filled when Kalshi shows a position."""
    row_id = _create_orphan(journal, "kalshi", "TICKER-A")
    orphan = journal.get_orphans()[0]

    result = await resolve_orphan(journal, orphan, kalshi_client=FakeKalshiWithPosition())

    assert result == "confirmed_fill"
    leg = journal._conn.execute(
        "SELECT status FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "filled"


@pytest.mark.asyncio
async def test_resolve_kalshi_no_position(journal: ExecutionJournal):
    """Orphan resolved as no-fill when Kalshi shows no position."""
    row_id = _create_orphan(journal, "kalshi", "TICKER-B")
    orphan = journal.get_orphans()[0]

    result = await resolve_orphan(journal, orphan, kalshi_client=FakeKalshiNoPosition())

    assert result == "confirmed_no_fill"
    leg = journal._conn.execute(
        "SELECT status FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "failed"


@pytest.mark.asyncio
async def test_resolve_kalshi_exchange_error(journal: ExecutionJournal):
    """Exchange error → manual_review, leg marked orphaned."""
    row_id = _create_orphan(journal, "kalshi", "TICKER-C")
    orphan = journal.get_orphans()[0]

    result = await resolve_orphan(journal, orphan, kalshi_client=FakeKalshiError())

    assert result == "manual_review"
    leg = journal._conn.execute(
        "SELECT status FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "orphaned"


@pytest.mark.asyncio
async def test_resolve_no_client(journal: ExecutionJournal):
    """No client available → manual_review."""
    _create_orphan(journal, "kalshi", "TICKER-D")
    orphan = journal.get_orphans()[0]

    result = await resolve_orphan(journal, orphan)

    assert result == "manual_review"


@pytest.mark.asyncio
async def test_resolve_polymarket_always_manual(journal: ExecutionJournal):
    """Polymarket orphans always go to manual review (no positions API)."""
    row_id = _create_orphan(journal, "polymarket", "TOKEN-1")
    orphan = journal.get_orphans()[0]

    result = await resolve_orphan(journal, orphan, poly_client=object())

    assert result == "manual_review"
    leg = journal._conn.execute(
        "SELECT status FROM execution_legs WHERE id=?", (row_id,)
    ).fetchone()
    assert leg["status"] == "orphaned"
