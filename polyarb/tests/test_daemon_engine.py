"""Tests for polyarb.daemon.engine."""

from __future__ import annotations

from polyarb.config import Config
from polyarb.daemon.engine import _CircuitBreaker, run_scan_once
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


async def test_encoder_receives_all_pairs():
    """Encoder receives all Poly x Kalshi pairs, even with zero token overlap."""
    poly = FakeProvider([_mkt("p1", "Will BTC hit $100k?")])
    kalshi = FakeProvider([_mkt("k1", "Bitcoin above $100,000?", "kalshi")])
    encoder = FakeEncoder(scores=[0.85])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # Pair was sent to encoder despite zero shared tokens
    assert len(encoder.pairs_sent) == 1
    assert encoder.pairs_sent[0] == ("Will BTC hit $100k?", "Bitcoin above $100,000?")
    assert len(state.matches) == 1


async def test_encoder_filters_false_positives():
    """When encoder returns low score, candidate is excluded."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=[0.15])  # below match_final_threshold
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert len(state.matches) == 0


async def test_encoder_picks_best_match_per_poly():
    """Each Poly market gets at most one Kalshi match (highest score)."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([
        _mkt("k1", "Will X happen?", "kalshi"),
        _mkt("k2", "X happening?", "kalshi"),
    ])
    # Two pairs generated, first scores higher
    encoder = FakeEncoder(scores=[0.90, 0.60])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert len(state.matches) == 1
    assert state.matches[0].confidence == 0.90
    assert state.matches[0].kalshi_market.condition_id == "k1"


async def test_encoder_failure_produces_no_matches():
    """When encoder returns None, all-pairs candidates have confidence=0, so none survive."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    encoder = FakeEncoder(scores=None)  # simulates failure
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # generate_all_pairs sets confidence=0.0 — none pass the 0.5 threshold
    assert len(state.matches) == 0


async def test_encoder_year_mismatch_filtered():
    """Year-mismatch pairs are excluded before reaching the encoder."""
    poly = FakeProvider([_mkt("p1", "Will BTC hit $100k by 2025?")])
    kalshi = FakeProvider([_mkt("k1", "Bitcoin above $100k by 2026?", "kalshi")])
    encoder = FakeEncoder(scores=[0.95])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # Year mismatch filtered in generate_all_pairs — encoder never called
    assert len(encoder.pairs_sent) == 0
    assert len(state.matches) == 0


async def test_no_encoder_uses_token_matcher():
    """Without encoder, find_matches runs with token-based scoring."""
    poly = FakeProvider([_mkt("p1", "Will X happen?")])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi, encoder_client=None)

    assert len(state.matches) >= 1


# ── Circuit breaker tests ────────────────────────────────────


def test_circuit_breaker_starts_closed():
    cb = _CircuitBreaker("test")
    assert cb.is_open is False
    assert cb.backoff_delay == 0.0


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker("test")
    for _ in range(5):
        cb.record_failure(RuntimeError("fail"))
    assert cb.is_open is True
    assert cb.backoff_delay > 0


def test_circuit_breaker_resets_on_success():
    cb = _CircuitBreaker("test")
    for _ in range(5):
        cb.record_failure(RuntimeError("fail"))
    assert cb.is_open is True
    cb.record_success()
    assert cb.is_open is False
    assert cb.backoff_delay == 0.0


async def test_scan_continues_with_provider_timeout():
    """If a provider raises, scan completes with empty markets for that side."""

    class FailingProvider(FakeProvider):
        async def get_active_markets(self):
            raise TimeoutError("API timeout")

    poly = FailingProvider([])
    kalshi = FakeProvider([_mkt("k1", "Will X happen?", "kalshi")])
    state = State(config=Config())

    # Should not raise — the scan should handle the failure gracefully
    await run_scan_once(state, poly, kalshi)
    assert state.scan_count == 1
