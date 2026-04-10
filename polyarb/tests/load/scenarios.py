"""Benchmark scenarios for CPU-bound matching and scoring.

Run with pytest-benchmark::

    pytest polyarb/tests/load/scenarios.py -v

Or standalone::

    python polyarb/tests/load/scenarios.py
"""

from __future__ import annotations

import time

from polyarb.matching.matcher import find_matches, generate_all_pairs
from polyarb.tests.factories import MarketFactory


def bench_find_matches(n_poly: int, n_kalshi: int) -> float:
    """Benchmark token-based matching. Returns seconds elapsed."""
    poly_markets = MarketFactory.create_batch(n_poly, platform="polymarket")
    kalshi_markets = MarketFactory.create_batch(n_kalshi, platform="kalshi")

    start = time.perf_counter()
    find_matches(poly_markets, kalshi_markets, min_confidence=0.3)
    elapsed = time.perf_counter() - start
    return elapsed


def bench_generate_all_pairs(n_poly: int, n_kalshi: int) -> float:
    """Benchmark cartesian pair generation. Returns seconds elapsed."""
    poly_markets = MarketFactory.create_batch(n_poly, platform="polymarket")
    kalshi_markets = MarketFactory.create_batch(n_kalshi, platform="kalshi")

    start = time.perf_counter()
    generate_all_pairs(poly_markets, kalshi_markets, max_candidates=200)
    elapsed = time.perf_counter() - start
    return elapsed


# ── Pytest tests (no pytest-benchmark dependency) ────────────


def test_find_matches_100x100():
    elapsed = bench_find_matches(100, 100)
    assert elapsed < 5.0, f"100x100 matching took {elapsed:.2f}s (expected < 5s)"


def test_find_matches_500x500():
    elapsed = bench_find_matches(500, 500)
    assert elapsed < 30.0, f"500x500 matching took {elapsed:.2f}s (expected < 30s)"


def test_generate_pairs_100x100():
    elapsed = bench_generate_all_pairs(100, 100)
    assert elapsed < 5.0, f"100x100 pair gen took {elapsed:.2f}s (expected < 5s)"


def test_generate_pairs_500x500():
    elapsed = bench_generate_all_pairs(500, 500)
    assert elapsed < 30.0, f"500x500 pair gen took {elapsed:.2f}s (expected < 30s)"


if __name__ == "__main__":
    for label, n_p, n_k in [
        ("100x100", 100, 100),
        ("500x500", 500, 500),
        ("1000x1000", 1000, 1000),
    ]:
        t = bench_find_matches(n_p, n_k)
        print(f"find_matches {label}: {t:.3f}s")

        t = bench_generate_all_pairs(n_p, n_k)
        print(f"generate_all_pairs {label}: {t:.3f}s")
