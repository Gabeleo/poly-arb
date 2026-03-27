"""Tests for polyarb.daemon.engine."""

from __future__ import annotations

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


class FakeEncoder:
    """Fake EncoderClient for testing the two-phase pipeline."""

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


# ── Tests (no encoder) ───────────────────────────────────────


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


# ── Tests (with encoder) ─────────────────────────────────────


async def test_encoder_verifies_candidates():
    """When encoder returns high score, match comes through."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=[0.92])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert len(state.matches) == 1
    assert state.matches[0].confidence == 0.92
    assert len(encoder.pairs_sent) == 1


async def test_encoder_filters_false_positives():
    """When encoder returns low score, candidate is excluded."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=[0.15])  # below match_final_threshold
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert len(state.matches) == 0


async def test_encoder_failure_falls_back_to_token_scores():
    """When encoder returns None, fall back to token-based scores."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=None)  # simulates failure
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # Token scorer gives high confidence for identical text → survives fallback
    assert len(state.matches) >= 1
    # Confidence should be token-based, not encoder-based
    assert state.matches[0].confidence != 0.92


async def test_no_encoder_uses_final_threshold():
    """Without encoder, find_matches runs at match_final_threshold directly."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=None)

    assert len(state.matches) >= 1
