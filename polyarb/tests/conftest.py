"""Shared fixtures for polyarb tests."""

from __future__ import annotations

import pytest

from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token

# ── Factories ────────────────────────────────────────────────


def make_token(token_id: str, side: Side, mid: float, spread: float = 0.02) -> Token:
    """Create a Token with symmetric bid/ask around midpoint."""
    half = spread / 2
    return Token(
        token_id=token_id,
        side=side,
        midpoint=mid,
        best_bid=round(mid - half, 4),
        best_ask=round(mid + half, 4),
    )


def make_market(
    cid: str = "test-1",
    question: str = "Will X happen?",
    yes_mid: float = 0.50,
    no_mid: float | None = None,
    platform: str = "polymarket",
    spread: float = 0.02,
) -> Market:
    """Create a Market with sensible defaults."""
    if no_mid is None:
        no_mid = round(1.0 - yes_mid, 4)
    return Market(
        condition_id=cid,
        question=question,
        yes_token=make_token(f"y-{cid}", Side.YES, yes_mid, spread),
        no_token=make_token(f"n-{cid}", Side.NO, no_mid, spread),
        platform=platform,
    )


def make_matched_pair(
    poly_yes_ask: float = 0.65,
    kalshi_yes_ask: float = 0.40,
    confidence: float = 0.9,
) -> MatchedPair:
    """Create a MatchedPair with configurable ask prices."""
    poly = make_market(
        cid="poly-1",
        question="Will X happen?",
        yes_mid=poly_yes_ask,
        no_mid=round(1.0 - poly_yes_ask, 4),
        platform="polymarket",
    )
    kalshi = make_market(
        cid="kalshi-1",
        question="Will X happen?",
        yes_mid=kalshi_yes_ask,
        no_mid=round(1.0 - kalshi_yes_ask, 4),
        platform="kalshi",
    )
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=confidence)


# ── Fake async clients ───────────────────────────────────────


class FakeAsyncProvider:
    """Async data provider returning preconfigured markets."""

    def __init__(self, markets: list[Market] | None = None):
        self.markets = markets or []
        self.call_count = 0

    async def get_active_markets(self) -> list[Market]:
        self.call_count += 1
        return self.markets

    async def get_events(self):
        return []

    async def close(self):
        pass


class FakeKalshiClient:
    """Fake Kalshi execution client for testing."""

    def __init__(self, fail: bool = False, cancel_fail: bool = False):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self._fail = fail
        self._cancel_fail = cancel_fail

    async def create_order(self, **kwargs) -> dict:
        if self._fail:
            raise RuntimeError("Kalshi API error")
        self.orders.append(kwargs)
        return {"order_id": f"k-{len(self.orders)}", "status": "executed"}

    async def cancel_order(self, order_id: str) -> dict:
        if self._cancel_fail:
            raise RuntimeError("Cancel failed")
        self.cancelled.append(order_id)
        return {"order": {"status": "canceled"}}


class FakePolyClient:
    """Fake Polymarket execution client for testing."""

    def __init__(self, fail: bool = False, cancel_fail: bool = False):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self._fail = fail
        self._cancel_fail = cancel_fail

    async def create_order(self, **kwargs) -> dict:
        if self._fail:
            raise RuntimeError("Poly API error")
        self.orders.append(kwargs)
        return {"orderID": f"p-{len(self.orders)}", "status": "MATCHED"}

    async def cancel_order(self, order_id: str) -> dict:
        if self._cancel_fail:
            raise RuntimeError("Cancel failed")
        self.cancelled.append(order_id)
        return {"orderID": order_id, "status": "CANCELED"}


class FakeEncoderClient:
    """Fake cross-encoder that returns configurable scores."""

    def __init__(self, scores: list[float] | None = None):
        self._scores = scores
        self.pairs_sent: list[tuple[str, str]] = []

    async def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float] | None:
        self.pairs_sent = pairs
        if self._scores is None:
            return None
        return self._scores[: len(pairs)]

    async def health(self) -> bool:
        return self._scores is not None

    async def close(self) -> None:
        pass


# ── Pytest fixtures ──────────────────────────────────────────


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def profitable_match():
    """Kalshi YES ask ~0.41, Poly NO ask ~0.36 → strong profit."""
    return make_matched_pair(poly_yes_ask=0.65, kalshi_yes_ask=0.40)
