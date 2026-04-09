"""Tests for CrossExecutor dual-leg execution and failure recovery."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.config import Config
from polyarb.execution.cross import CrossExecutor, ExecutionResult
from polyarb.execution.journal import ExecutionJournal
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


# ── Helpers ───────────────────────────────────────────────────


def _mkt(
    cid: str, platform: str, yes_ask: float, no_ask: float | None = None,
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
    """Kalshi YES ask=0.40, Poly NO ask=0.35 -> cost=0.75, strong profit."""
    poly = _mkt("poly-1", "polymarket", 0.65, no_ask=0.35)
    kalshi = _mkt("kalshi-1", "kalshi", 0.40)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.9)


# ── Fake clients ──────────────────────────────────────────────


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


# ── Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_legs_succeed():
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is True
    assert result.kalshi_order is not None
    assert result.poly_order is not None
    assert len(kalshi.orders) == 1
    assert len(poly.orders) == 1
    assert result.unwound is False


@pytest.mark.asyncio
async def test_both_legs_fail():
    kalshi = FakeKalshiClient(fail=True)
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is False
    assert "Both legs failed" in result.error
    assert result.unwound is False


@pytest.mark.asyncio
async def test_kalshi_fails_poly_succeeds_cancels_poly():
    kalshi = FakeKalshiClient(fail=True)
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is False
    assert "Kalshi leg failed" in result.error
    # Poly order should have been placed then cancelled
    assert len(poly.orders) == 1
    assert len(poly.cancelled) == 1
    assert poly.cancelled[0] == "p-1"
    assert result.unwound is True


@pytest.mark.asyncio
async def test_kalshi_succeeds_poly_fails_cancels_kalshi():
    kalshi = FakeKalshiClient()
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is False
    assert "Poly leg failed" in result.error
    # Kalshi order should have been placed then cancelled
    assert len(kalshi.orders) == 1
    assert len(kalshi.cancelled) == 1
    assert kalshi.cancelled[0] == "k-1"
    assert result.unwound is True


@pytest.mark.asyncio
async def test_execution_params_passed_correctly():
    """Verify Kalshi gets cents and Poly gets float price."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    match = _profitable_match()
    await executor.execute(match, Config(order_size=5.0))

    params = match.execution_params

    # Kalshi order
    k_order = kalshi.orders[0]
    assert k_order["price_cents"] == max(1, min(99, round(params["kalshi"]["price"] * 100)))
    assert k_order["count"] == 5
    assert k_order["side"] == params["kalshi"]["side"]
    assert k_order["ticker"] == params["kalshi"]["ticker"]

    # Poly order
    p_order = poly.orders[0]
    assert p_order["token_id"] == params["poly"]["token_id"]
    assert p_order["price"] == params["poly"]["price"]
    assert p_order["size"] == 5.0
    assert p_order["order_type"] == "FOK"


@pytest.mark.asyncio
async def test_sides_are_complementary_in_orders():
    """Kalshi and Poly must take opposite sides."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    match = _profitable_match()
    await executor.execute(match, Config())

    params = match.execution_params
    assert params["kalshi"]["side"] != params["poly"]["side"]


@pytest.mark.asyncio
async def test_unwind_failure_reported():
    """If cancellation also fails, unwound should be False."""

    class FailCancelKalshi(FakeKalshiClient):
        async def cancel_order(self, order_id: str) -> dict:
            raise RuntimeError("Cancel failed")

    kalshi = FailCancelKalshi()
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is False
    assert result.unwound is False
    assert "UNWIND FAILED" in result.error


@pytest.mark.asyncio
async def test_execution_result_describe():
    """ExecutionResult.describe() returns human-readable strings."""
    success = ExecutionResult(
        success=True,
        kalshi_order={"order_id": "k-1"},
        poly_order={"orderID": "p-1"},
    )
    assert "Both legs filled" in success.describe()

    failure = ExecutionResult(success=False, error="test error")
    assert "failed" in failure.describe().lower()

    unwound = ExecutionResult(success=False, error="partial", unwound=True)
    assert "unwound" in unwound.describe().lower()


# ── Journal integration ──────────────────────────────────────


@pytest.fixture
def journal():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    j = ExecutionJournal(db_path=path)
    yield j
    j.close()
    os.unlink(path)


# ── Tests: Kelly sizing integration ──────────────────────────


@pytest.mark.asyncio
async def test_kelly_sizing_active():
    """With bankroll and kelly_fraction set, order size should differ from static."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    config = Config(bankroll=1000.0, kelly_fraction=0.5, order_size=10.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is True
    # Kelly should produce a size != static 10
    k_count = kalshi.orders[0]["count"]
    assert k_count != 10


@pytest.mark.asyncio
async def test_kelly_disabled_bankroll_zero():
    """bankroll=0 falls back to static order_size."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    config = Config(bankroll=0.0, kelly_fraction=0.5, order_size=7.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is True
    assert kalshi.orders[0]["count"] == 7


@pytest.mark.asyncio
async def test_kelly_disabled_fraction_zero():
    """kelly_fraction=0 falls back to static order_size."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    config = Config(bankroll=1000.0, kelly_fraction=0.0, order_size=7.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is True
    assert kalshi.orders[0]["count"] == 7


@pytest.mark.asyncio
async def test_kelly_below_minimum_returns_failure():
    """If Kelly size < 1.0, execution should fail without placing orders."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    # Very small bankroll -> Kelly size < 1.0
    config = Config(bankroll=1.0, kelly_fraction=0.1, order_size=10.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is False
    assert "below minimum" in result.error.lower()
    assert len(kalshi.orders) == 0
    assert len(poly.orders) == 0


@pytest.mark.asyncio
async def test_kelly_max_position_cap():
    """Order count should not exceed max_position."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly)

    config = Config(bankroll=10000.0, kelly_fraction=0.5, max_position=5.0)
    result = await executor.execute(_profitable_match(), config)

    assert result.success is True
    assert kalshi.orders[0]["count"] <= 5


# ── Journal integration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_executor_journals_success(journal: ExecutionJournal):
    """Both legs succeed → journal records completed execution with 2 filled legs."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient()
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)

    result = await executor.execute(_profitable_match(), Config())

    assert result.success is True
    history = journal.get_history(limit=1)
    assert len(history) == 1
    assert history[0]["status"] == "completed"
    assert len(history[0]["legs"]) == 2
    for leg in history[0]["legs"]:
        assert leg["status"] == "filled"


@pytest.mark.asyncio
async def test_cross_executor_journals_both_fail(journal: ExecutionJournal):
    """Both legs fail → journal records failed execution."""
    kalshi = FakeKalshiClient(fail=True)
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)

    await executor.execute(_profitable_match(), Config())

    history = journal.get_history(limit=1)
    assert history[0]["status"] == "failed"
    for leg in history[0]["legs"]:
        assert leg["status"] == "failed"


@pytest.mark.asyncio
async def test_cross_executor_journals_partial_cancel(journal: ExecutionJournal):
    """Kalshi ok, Poly fails → journal shows Kalshi cancelled."""
    kalshi = FakeKalshiClient()
    poly = FakePolyClient(fail=True)
    executor = CrossExecutor(kalshi=kalshi, poly=poly, journal=journal)

    await executor.execute(_profitable_match(), Config())

    history = journal.get_history(limit=1)
    assert history[0]["status"] == "failed"
    legs = sorted(history[0]["legs"], key=lambda l: l["leg_index"])
    # Kalshi leg was filled then cancelled
    assert legs[0]["status"] == "cancelled"
    # Poly leg failed
    assert legs[1]["status"] == "failed"
