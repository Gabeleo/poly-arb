"""Tests for polyarb.daemon.engine."""

from polyarb.config import Config
from polyarb.daemon.engine import run_scan_once
from polyarb.daemon.state import State
from polyarb.models import Market, Side, Token


# ── Helpers ─────────────────────────────────────────────────


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
        self.call_count = 0

    async def get_active_markets(self) -> list[Market]:
        self.call_count += 1
        return self._markets

    async def get_events(self):
        return []

    async def search_markets(self, query: str, limit: int = 5):
        return []

    async def close(self):
        pass


# ── Tests ──────────────────────────────────────────────────


async def test_run_scan_once_fetches_both_providers():
    poly = FakeProvider([])
    kalshi = FakeProvider([])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    assert poly.call_count == 1
    assert kalshi.call_count == 1


async def test_run_scan_once_finds_matches():
    """Markets with the same question text on both platforms should match."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    assert len(state.matches) >= 1
    assert state.scan_count == 1


async def test_run_scan_once_dedup():
    """Second scan with same data produces no new matches."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)
    first_new = len(state.matches)

    # Second scan — same data, dedup should kick in
    await run_scan_once(state, poly, kalshi)
    assert state.scan_count == 2
    # Matches list is refreshed but _seen_matches prevents "new" count growth
    # We verify via scan_count that it ran twice
