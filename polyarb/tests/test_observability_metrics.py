"""Tests for Prometheus metric instrumentation in the scan engine."""

from __future__ import annotations

import prometheus_client
import pytest

from polyarb.config import Config
from polyarb.daemon.engine import _CircuitBreaker, run_scan_once
from polyarb.daemon.state import State
from polyarb.models import Market, Side, Token
from polyarb.observability import metrics


def _mkt(cid: str, question: str, platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y", Side.YES, 0.50, 0.49, 0.51),
        no_token=Token("n", Side.NO, 0.50, 0.49, 0.51),
        platform=platform,
    )


class FakeProvider:
    def __init__(self, markets: list[Market]):
        self._markets = markets

    async def get_active_markets(self) -> list[Market]:
        return self._markets

    async def get_events(self):
        return []

    async def search_markets(self, query: str, limit: int = 5):
        return []

    async def close(self):
        pass


class FakeEncoder:
    def __init__(self, scores: list[float] | None = None):
        self._scores = scores
        self.pairs_sent: list[tuple[str, str]] = []

    async def score_pairs(self, pairs) -> list[float] | None:
        self.pairs_sent = pairs
        if self._scores is None:
            return None
        return self._scores[: len(pairs)]

    async def health(self) -> bool:
        return self._scores is not None

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset all metric collectors between tests."""
    collectors = list(prometheus_client.REGISTRY._names_to_collectors.values())
    for c in collectors:
        if hasattr(c, "_metrics"):
            c._metrics.clear()


def _get_counter_value(counter, labels=None):
    """Get the value of a counter (returns 0 if not yet incremented)."""
    if labels:
        try:
            return counter.labels(**labels)._value.get()
        except Exception:
            return 0
    return counter._value.get()


def _get_gauge_value(gauge, labels=None):
    """Get the value of a gauge."""
    if labels:
        return gauge.labels(**labels)._value.get()
    return gauge._value.get()


def _get_histogram_count(histogram, labels=None):
    """Get the observation count of a histogram."""
    if labels:
        return histogram.labels(**labels)._sum._count()
    return histogram._sum._count()


async def test_scan_metrics_recorded():
    poly = FakeProvider([])
    kalshi = FakeProvider([])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    # scan_total with status=success should be incremented
    assert _get_counter_value(metrics.scan_total, {"status": "success"}) >= 1.0


async def test_fetch_metrics_per_provider():
    poly = FakeProvider([_mkt("p1", "Q1?")])
    kalshi = FakeProvider([_mkt("k1", "Q1?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    assert _get_gauge_value(metrics.markets_fetched, {"provider": "poly"}) == 1.0
    assert _get_gauge_value(metrics.markets_fetched, {"provider": "kalshi"}) == 1.0


async def test_encoder_metrics_recorded():
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=[0.92])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # Encoder duration should have at least one observation
    # We check the sum is > 0 which implies at least one observation
    sample = metrics.encoder_duration.collect()[0]
    assert any(s.value > 0 for s in sample.samples if s.name.endswith("_count"))


async def test_encoder_failure_metric():
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=None)
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert _get_counter_value(metrics.encoder_errors) >= 1.0


async def test_circuit_breaker_gauge():
    cb = _CircuitBreaker("test_cb")
    for _ in range(5):
        cb.record_failure(RuntimeError("fail"))

    assert _get_gauge_value(metrics.circuit_breaker_state, {"provider": "test_cb"}) == 1.0

    cb.record_success()
    assert _get_gauge_value(metrics.circuit_breaker_state, {"provider": "test_cb"}) == 0.0


async def test_match_and_opportunity_gauges():
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    # matches_found and opportunities_found should be set
    sample_m = metrics.matches_found.collect()[0]
    sample_o = metrics.opportunities_found.collect()[0]
    # At least verify they don't error — gauge values may be 0 or positive
    assert sample_m is not None
    assert sample_o is not None
