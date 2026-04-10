"""Integration tests for the cross-platform execution flow."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.config import Config
from polyarb.execution.cross import CrossExecutor
from polyarb.execution.journal import ExecutionJournal
from polyarb.tests.conftest import (
    FakeKalshiClient,
    FakePolyClient,
    make_matched_pair,
)


def _profitable_match():
    """Kalshi YES ask=0.41, Poly NO ask=0.36 → cost ~0.77, profit ~0.23."""
    return make_matched_pair(poly_yes_ask=0.65, kalshi_yes_ask=0.40)


def _make_journal():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    journal = ExecutionJournal(db_path=path)
    return journal, path


# ── Both legs succeed ────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_legs_fill():
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    result = await executor.execute(_profitable_match(), Config(order_size=10.0))

    assert result.success is True
    assert result.kalshi_order is not None
    assert result.poly_order is not None
    assert len(kalshi.orders) == 1
    assert len(poly.orders) == 1


@pytest.mark.asyncio
async def test_both_legs_fill_with_journal():
    journal, path = _make_journal()
    try:
        kalshi = FakeKalshiClient()
        poly = FakePolyClient()
        executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)  # type: ignore[arg-type]

        result = await executor.execute(_profitable_match(), Config(order_size=10.0))

        assert result.success is True
        # Journal should have recorded the execution
        history = journal.get_history(limit=1)
        assert len(history) == 1
        assert history[0]["status"] == "completed"
    finally:
        journal.close()
        os.unlink(path)


# ── Both legs fail ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_legs_fail():
    kalshi = FakeKalshiClient(fail=True)
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    result = await executor.execute(_profitable_match(), Config(order_size=10.0))

    assert result.success is False
    assert "Both legs failed" in result.error
    assert result.unwound is False


# ── Partial failure: Kalshi fills, Poly fails ────────────────


@pytest.mark.asyncio
async def test_kalshi_fills_poly_fails_unwinds():
    kalshi = FakeKalshiClient()
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    result = await executor.execute(_profitable_match(), Config(order_size=10.0))

    assert result.success is False
    assert result.unwound is True
    assert len(kalshi.cancelled) == 1  # Kalshi order was unwound


@pytest.mark.asyncio
async def test_kalshi_fills_poly_fails_unwind_fails():
    kalshi = FakeKalshiClient(cancel_fail=True)
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    result = await executor.execute(_profitable_match(), Config(order_size=10.0))

    assert result.success is False
    assert result.unwound is False
    assert "UNWIND FAILED" in result.error


# ── Partial failure: Poly fills, Kalshi fails ────────────────


@pytest.mark.asyncio
async def test_poly_fills_kalshi_fails_unwinds():
    kalshi = FakeKalshiClient(fail=True)
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    result = await executor.execute(_profitable_match(), Config(order_size=10.0))

    assert result.success is False
    assert result.unwound is True
    assert len(poly.cancelled) == 1  # Poly order was unwound


# ── Kelly sizing ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kelly_sizing_with_bankroll():
    """When bankroll is set, Kelly sizing should be used."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    config = Config(bankroll=1000.0, kelly_fraction=0.5, max_position=100.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is True
    # Kelly should have computed a size > 0
    assert len(kalshi.orders) == 1
    assert kalshi.orders[0]["count"] > 0


@pytest.mark.asyncio
async def test_kelly_too_small_rejects():
    """When edge is too small for Kelly, execution is rejected."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)  # type: ignore[arg-type]

    # Very small bankroll → Kelly size < 1 contract
    config = Config(bankroll=1.0, kelly_fraction=0.01, max_position=100.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is False
    assert "below minimum" in result.error.lower()


# ── Idempotency ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate():
    journal, path = _make_journal()
    try:
        kalshi = FakeKalshiClient()
        poly = FakePolyClient()
        executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)  # type: ignore[arg-type]
        match = _profitable_match()
        config = Config(order_size=10.0)

        result1 = await executor.execute(match, config)
        assert result1.success is True

        # Second execution with same match should be deduplicated
        result2 = await executor.execute(match, config)
        assert "Duplicate" in result2.error or "existing" in result2.error.lower()
        # Only one actual execution happened
        assert len(kalshi.orders) == 1
    finally:
        journal.close()
        os.unlink(path)


# ── Journal recording ────────────────────────────────────────


@pytest.mark.asyncio
async def test_journal_records_failure():
    journal, path = _make_journal()
    try:
        kalshi = FakeKalshiClient(fail=True)
        poly = FakePolyClient(fail=True)
        executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)  # type: ignore[arg-type]

        result = await executor.execute(_profitable_match(), Config(order_size=10.0))

        assert result.success is False
        history = journal.get_history(limit=1)
        assert len(history) == 1
        assert history[0]["status"] == "failed"
    finally:
        journal.close()
        os.unlink(path)
