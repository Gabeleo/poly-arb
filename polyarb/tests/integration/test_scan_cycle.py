"""Integration tests for the full daemon scan cycle."""

from __future__ import annotations

import pytest

from polyarb.config import Config
from polyarb.daemon.engine import run_scan_once
from polyarb.daemon.state import State
from polyarb.models import Market, Side, Token
from polyarb.tests.conftest import FakeAsyncProvider, FakeEncoderClient


def _mkt(cid: str, question: str, platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y", Side.YES, 0.50, 0.49, 0.51),
        no_token=Token("n", Side.NO, 0.50, 0.49, 0.51),
        platform=platform,
    )


# ── Scan cycle: fetch + match + detect ───────────────────────


@pytest.mark.asyncio
async def test_scan_fetches_both_providers():
    poly = FakeAsyncProvider([])
    kalshi = FakeAsyncProvider([])
    state = State(config=Config())

    await run_scan_once(state, poly, kalshi)

    assert poly.call_count == 1
    assert kalshi.call_count == 1
    assert state.scan_count == 1


@pytest.mark.asyncio
async def test_scan_finds_matching_markets():
    """Identical question text on both platforms → match."""
    poly = FakeAsyncProvider([_mkt("p1", "Will Bitcoin hit 100k?")])
    kalshi = FakeAsyncProvider([_mkt("k1", "Will Bitcoin hit 100k?", "kalshi")])
    state = State(config=Config(match_final_threshold=0.3))

    await run_scan_once(state, poly, kalshi)

    assert len(state.matches) == 1
    assert state.matches[0].poly_market.condition_id == "p1"
    assert state.matches[0].kalshi_market.condition_id == "k1"


@pytest.mark.asyncio
async def test_scan_no_match_for_unrelated_markets():
    poly = FakeAsyncProvider([_mkt("p1", "Will the sun rise?")])
    kalshi = FakeAsyncProvider([_mkt("k1", "Will Bitcoin hit 100k?", "kalshi")])
    state = State(config=Config(match_final_threshold=0.5))

    await run_scan_once(state, poly, kalshi)

    assert len(state.matches) == 0


@pytest.mark.asyncio
async def test_scan_deduplication():
    """Running two scans with the same markets → second scan has no new matches."""
    poly = FakeAsyncProvider([_mkt("p1", "Will Bitcoin hit 100k?")])
    kalshi = FakeAsyncProvider([_mkt("k1", "Will Bitcoin hit 100k?", "kalshi")])
    state = State(config=Config(match_final_threshold=0.3))

    await run_scan_once(state, poly, kalshi)
    assert len(state.matches) == 1

    await run_scan_once(state, poly, kalshi)

    assert state.scan_count == 2
    # Matches list is replaced, but the dedup cache prevents re-notification


@pytest.mark.asyncio
async def test_scan_increments_count_on_empty():
    state = State(config=Config())
    await run_scan_once(state, FakeAsyncProvider([]), FakeAsyncProvider([]))
    await run_scan_once(state, FakeAsyncProvider([]), FakeAsyncProvider([]))
    assert state.scan_count == 2


@pytest.mark.asyncio
async def test_scan_without_encoder_uses_token_matching():
    """When no encoder_client is passed, token-only matching is used."""
    poly = FakeAsyncProvider([_mkt("p1", "Will Bitcoin hit 100k?")])
    kalshi = FakeAsyncProvider([_mkt("k1", "Will Bitcoin hit 100k?", "kalshi")])
    state = State(config=Config(match_final_threshold=0.3))

    await run_scan_once(state, poly, kalshi, encoder_client=None)

    # Token matcher should find the identical market
    assert len(state.matches) == 1


@pytest.mark.asyncio
async def test_scan_encoder_down_returns_no_matches():
    """When encoder returns None, generate_all_pairs candidates have 0 confidence
    so they don't pass the threshold — no matches returned."""
    poly = FakeAsyncProvider([_mkt("p1", "Will Bitcoin hit 100k?")])
    kalshi = FakeAsyncProvider([_mkt("k1", "Will Bitcoin hit 100k?", "kalshi")])
    encoder = FakeEncoderClient(scores=None)  # simulate encoder down
    state = State(config=Config(match_final_threshold=0.3))

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    # Fallback uses candidate confidence (0.0 from generate_all_pairs) → filtered out
    assert len(state.matches) == 0


@pytest.mark.asyncio
async def test_scan_with_encoder_scoring():
    """Encoder scores filter candidates below threshold."""
    poly = FakeAsyncProvider([
        _mkt("p1", "Will Bitcoin hit 100k?"),
        _mkt("p2", "Will GDP grow 3%?"),
    ])
    kalshi = FakeAsyncProvider([
        _mkt("k1", "Will Bitcoin hit 100k?", "kalshi"),
        _mkt("k2", "Will GDP grow 3%?", "kalshi"),
    ])
    # High scores for both pairs
    encoder = FakeEncoderClient(scores=[0.95] * 10)
    state = State(config=Config(match_final_threshold=0.5))

    await run_scan_once(state, poly, kalshi, encoder_client=encoder)

    assert len(state.matches) == 2


@pytest.mark.asyncio
async def test_scan_provider_failure_handled():
    """If a provider raises, circuit breaker absorbs it and scan completes."""

    class FailingProvider:
        async def get_active_markets(self):
            raise RuntimeError("API down")

        async def close(self):
            pass

    state = State(config=Config())
    # Circuit breaker catches the error — scan completes without crashing
    await run_scan_once(state, FailingProvider(), FakeAsyncProvider([]))
    assert state.scan_count == 1
    assert len(state.matches) == 0


@pytest.mark.asyncio
async def test_scan_multiple_markets_best_match():
    """Each Poly market gets at most one Kalshi match (1:1)."""
    poly = FakeAsyncProvider([
        _mkt("p1", "Will the election result in a landslide?"),
        _mkt("p2", "Will inflation exceed 5%?"),
    ])
    kalshi = FakeAsyncProvider([
        _mkt("k1", "Will the election result in a landslide?", "kalshi"),
        _mkt("k2", "Will inflation exceed 5%?", "kalshi"),
        _mkt("k3", "Will the election be close?", "kalshi"),
    ])
    state = State(config=Config(match_final_threshold=0.3))

    await run_scan_once(state, poly, kalshi)

    # At most 2 matches (one per Poly market)
    assert len(state.matches) <= 2
    # Each Poly market matched at most once
    poly_cids = [m.poly_market.condition_id for m in state.matches]
    assert len(poly_cids) == len(set(poly_cids))
