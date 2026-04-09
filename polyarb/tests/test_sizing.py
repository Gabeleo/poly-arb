"""Tests for Kelly Criterion position sizing module."""

from __future__ import annotations

import pytest

from polyarb.sizing import kelly_fraction_raw, kelly_size


# ── kelly_fraction_raw ───────────────────────────────────────


def test_kelly_fraction_raw_basic():
    # net_profit=0.125, cost=0.875 -> 0.125/0.875 = 0.142857...
    result = kelly_fraction_raw(0.125, 0.875)
    assert abs(result - 0.142857) < 1e-4


def test_kelly_fraction_raw_no_edge():
    assert kelly_fraction_raw(0.0, 0.875) == 0.0
    assert kelly_fraction_raw(-0.05, 0.875) == 0.0


def test_kelly_fraction_raw_invalid_cost():
    assert kelly_fraction_raw(0.1, 0.0) == 0.0
    assert kelly_fraction_raw(0.1, -0.5) == 0.0


# ── kelly_size: invalid inputs return 0.0 ────────────────────


def test_kelly_size_no_edge():
    assert kelly_size(0.0, 0.875, 1000.0) == 0.0
    assert kelly_size(-0.01, 0.875, 1000.0) == 0.0


def test_kelly_size_zero_bankroll():
    assert kelly_size(0.125, 0.875, 0.0) == 0.0


def test_kelly_size_negative_bankroll():
    assert kelly_size(0.125, 0.875, -100.0) == 0.0


def test_kelly_size_zero_cost():
    assert kelly_size(0.125, 0.0, 1000.0) == 0.0


def test_kelly_size_zero_fraction():
    # fraction=0.0 means position = 0.0 * ... = 0.0 < min_position -> 0.0
    assert kelly_size(0.125, 0.875, 1000.0, fraction=0.0) == 0.0


# ── kelly_size: basic calculation ────────────────────────────


def test_kelly_size_half_kelly():
    """Hand-calculated example from the spec.

    net_profit=0.125, cost=0.875, bankroll=1000, fraction=0.5
    kelly_raw = 0.125/0.875 = 0.142857
    half_kelly = 0.5 * 0.142857 = 0.071429
    position = 0.071429 * 1000 / 0.875 = 81.632...
    """
    result = kelly_size(0.125, 0.875, 1000.0, fraction=0.5)
    assert abs(result - 81.63) < 0.1


def test_kelly_size_full_kelly():
    """Full Kelly = 2x half Kelly."""
    half = kelly_size(0.125, 0.875, 1000.0, fraction=0.5)
    full = kelly_size(0.125, 0.875, 1000.0, fraction=1.0)
    assert abs(full - 2 * half) < 0.01


# ── kelly_size: caps and floors ──────────────────────────────


def test_kelly_size_max_position_cap():
    """max_position should cap the result."""
    uncapped = kelly_size(0.125, 0.875, 1000.0, fraction=0.5)
    assert uncapped > 50  # sanity: uncapped is ~81.6
    capped = kelly_size(0.125, 0.875, 1000.0, fraction=0.5, max_position=50.0)
    assert capped == 50.0


def test_kelly_size_bankroll_cap():
    """Position can't exceed bankroll / cost."""
    # bankroll=50, cost=0.875 -> max ~57 contracts
    # But Kelly wants ~81.6 * (50/1000) = ~4.08 contracts
    result = kelly_size(0.125, 0.875, 50.0, fraction=0.5)
    assert result <= 50.0 / 0.875 + 0.01


def test_kelly_size_below_min_position():
    """Very small edge + small bankroll -> below min -> returns 0.0."""
    # tiny edge: net_profit=0.001, cost=0.999, bankroll=10, fraction=0.5
    # kelly_raw = 0.001/0.999 ~ 0.001
    # position = 0.5 * 0.001 * 10 / 0.999 ~ 0.005 < 1.0
    result = kelly_size(0.001, 0.999, 10.0, fraction=0.5)
    assert result == 0.0


def test_kelly_size_custom_min_position():
    """With a higher min_position, marginal trades get rejected."""
    # This would normally give ~81.6 contracts
    result = kelly_size(0.125, 0.875, 1000.0, fraction=0.5, min_position=100.0)
    assert result == 0.0  # 81.6 < 100


def test_kelly_size_large_edge_small_bankroll():
    """Large edge but small bankroll — limited by bankroll cap."""
    # net_profit=0.5, cost=0.5, bankroll=20
    # kelly_raw = 1.0, full kelly position = 1.0 * 20 / 0.5 = 40
    # but bankroll cap = 20 / 0.5 = 40 -> OK at fraction=1.0
    # half kelly = 0.5 * 1.0 * 20 / 0.5 = 20
    result = kelly_size(0.5, 0.5, 20.0, fraction=0.5)
    assert abs(result - 20.0) < 0.01


def test_kelly_size_max_position_tighter_than_bankroll():
    """max_position < bankroll/cost should be the binding constraint."""
    result = kelly_size(0.5, 0.5, 20.0, fraction=1.0, max_position=5.0)
    assert result == 5.0
