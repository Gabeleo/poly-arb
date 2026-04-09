"""Tests for idempotency key generation and execution dedup."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.config import Config
from polyarb.execution.cross import CrossExecutor
from polyarb.execution.idempotency import (
    BUCKET_SECONDS,
    generate_idempotency_key,
)
from polyarb.execution.journal import ExecutionJournal
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token

# ── Helpers ───────────────────────────────────────────────────


def _mkt(
    cid: str,
    platform: str,
    yes_ask: float,
    no_ask: float | None = None,
) -> Market:
    if no_ask is None:
        no_ask = round(1.0 - yes_ask, 4)
    return Market(
        condition_id=cid,
        question="Will X?",
        yes_token=Token("y-" + cid, Side.YES, yes_ask, yes_ask - 0.01, yes_ask),
        no_token=Token("n-" + cid, Side.NO, no_ask, no_ask - 0.01, no_ask),
        platform=platform,
    )


def _profitable_match() -> MatchedPair:
    poly = _mkt("poly-1", "polymarket", 0.65, no_ask=0.35)
    kalshi = _mkt("kalshi-1", "kalshi", 0.40)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.9)


class FakeKalshiClient:
    def __init__(self, fail: bool = False):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self._fail = fail

    async def create_order(self, **kwargs) -> dict:
        if self._fail:
            raise RuntimeError("Kalshi API error")
        self.orders.append(kwargs)
        return {"order_id": f"k-{len(self.orders)}", "status": "executed"}

    async def cancel_order(self, order_id: str) -> dict:
        self.cancelled.append(order_id)
        return {"order": {"status": "canceled"}}


class FakePolyClient:
    def __init__(self, fail: bool = False):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self._fail = fail

    async def create_order(self, **kwargs) -> dict:
        if self._fail:
            raise RuntimeError("Poly API error")
        self.orders.append(kwargs)
        return {"orderID": f"p-{len(self.orders)}", "status": "matched"}

    async def cancel_order(self, order_id: str) -> dict:
        self.cancelled.append(order_id)
        return {"status": "cancelled"}


@pytest.fixture
def journal():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    j = ExecutionJournal(db_path=path)
    yield j
    j.close()
    os.unlink(path)


# ── Key generation tests ─────────────────────────────────────


def test_same_inputs_same_bucket_same_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    k2 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts + 30)
    assert k1 == k2


def test_different_bucket_different_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    k2 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts + BUCKET_SECONDS)
    assert k1 != k2


def test_different_match_key_different_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    k2 = generate_idempotency_key("m2", "kalshi_yes_poly_no", 10.0, ts=ts)
    assert k1 != k2


def test_different_direction_different_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    k2 = generate_idempotency_key("m1", "kalshi_no_poly_yes", 10.0, ts=ts)
    assert k1 != k2


def test_different_size_different_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    k2 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 20.0, ts=ts)
    assert k1 != k2


def test_key_is_16_hex_chars():
    key = generate_idempotency_key("m1", "dir", 5.0, ts=1_700_000_000.0)
    assert len(key) == 16
    int(key, 16)  # should not raise


def test_int_and_float_size_produce_same_key():
    ts = 1_700_000_000.0
    k1 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10, ts=ts)
    k2 = generate_idempotency_key("m1", "kalshi_yes_poly_no", 10.0, ts=ts)
    assert k1 == k2


# ── Journal dedup tests ──────────────────────────────────────


def test_find_by_idempotency_key_returns_none_when_empty(journal: ExecutionJournal):
    assert journal.find_by_idempotency_key("nonexistent") is None


def test_find_by_idempotency_key_returns_pending(journal: ExecutionJournal):
    journal.record_execution("exec-1", "mk", 2, idempotency_key="key-abc")
    result = journal.find_by_idempotency_key("key-abc")
    assert result is not None
    assert result["execution_id"] == "exec-1"
    assert result["status"] == "pending"


def test_find_by_idempotency_key_skips_failed(journal: ExecutionJournal):
    journal.record_execution("exec-1", "mk", 2, idempotency_key="key-abc")
    journal.record_completion("exec-1", success=False)
    result = journal.find_by_idempotency_key("key-abc")
    assert result is None


def test_find_by_idempotency_key_returns_completed(journal: ExecutionJournal):
    journal.record_execution("exec-1", "mk", 2, idempotency_key="key-abc")
    journal.record_completion("exec-1", success=True, profit=0.05)
    result = journal.find_by_idempotency_key("key-abc")
    assert result is not None
    assert result["status"] == "completed"


# ── CrossExecutor integration ────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_execution_skipped(journal: ExecutionJournal):
    """Same match executed twice in same 60s window -> second is skipped."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)
    config = Config()

    result1 = await executor.execute(_profitable_match(), config)
    assert result1.success is True
    assert len(kalshi.orders) == 1
    assert len(poly.orders) == 1

    # Second execution of the same match — should be skipped
    result2 = await executor.execute(_profitable_match(), config)
    assert "Duplicate execution skipped" in result2.error
    # No additional orders placed
    assert len(kalshi.orders) == 1
    assert len(poly.orders) == 1


@pytest.mark.asyncio
async def test_retry_after_failure_allowed(journal: ExecutionJournal):
    """If first execution fails, retry in the same window should proceed."""
    kalshi_fail = FakeKalshiClient(fail=True)
    poly_fail = FakePolyClient(fail=True)
    executor_fail = CrossExecutor(kalshi=kalshi_fail, poly=poly_fail, journal=journal)
    config = Config()

    result1 = await executor_fail.execute(_profitable_match(), config)
    assert result1.success is False

    # Retry with working clients — should proceed because previous was failed
    kalshi_ok = FakeKalshiClient()
    poly_ok = FakePolyClient()
    executor_ok = CrossExecutor(kalshi=kalshi_ok, poly=poly_ok, journal=journal)

    result2 = await executor_ok.execute(_profitable_match(), config)
    assert result2.success is True
    assert len(kalshi_ok.orders) == 1


@pytest.mark.asyncio
async def test_no_journal_no_idempotency():
    """Without a journal, idempotency is not enforced (no crash)."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=None)
    config = Config()

    result1 = await executor.execute(_profitable_match(), config)
    result2 = await executor.execute(_profitable_match(), config)

    assert result1.success is True
    assert result2.success is True
    # Both executed — no dedup without journal
    assert len(kalshi.orders) == 2


@pytest.mark.asyncio
async def test_unique_constraint_prevents_race(journal: ExecutionJournal):
    """Simulates the TOCTOU race: soft check misses, but insert hits unique constraint."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()

    # First execution succeeds normally
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)
    result1 = await executor.execute(_profitable_match(), Config())
    assert result1.success is True

    first_exec = journal.get_history(limit=1)[0]
    idem_key = first_exec["idempotency_key"]
    assert idem_key is not None

    # Patch find_by_idempotency_key to return None (simulates the race:
    # the other coroutine hasn't inserted yet when we check)
    real_find = journal.find_by_idempotency_key
    journal.find_by_idempotency_key = lambda key: None  # type: ignore[assignment]

    # Second execution: soft check sees nothing, but insert hits unique constraint.
    # IntegrityError handler falls back to lookup (using the real method).
    # Restore real method so the fallback lookup inside the except block works.
    original_record = journal.record_execution

    def patched_record(*args, **kwargs):
        # Restore find so the IntegrityError handler's fallback lookup works
        journal.find_by_idempotency_key = real_find  # type: ignore[assignment]
        return original_record(*args, **kwargs)

    journal.record_execution = patched_record  # type: ignore[assignment]

    result2 = await executor.execute(_profitable_match(), Config())
    assert "Duplicate execution skipped" in result2.error
    # Only the first execution placed orders
    assert len(kalshi.orders) == 1
