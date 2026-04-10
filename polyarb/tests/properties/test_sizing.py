"""Property-based tests for Kelly Criterion position sizing."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis.strategies import floats

from polyarb.sizing import kelly_fraction_raw, kelly_size

# ── Strategies ───────────────────────────────────────────────

profit = floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False)
cost = floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False)
bankroll = floats(min_value=10.0, max_value=100000.0, allow_nan=False, allow_infinity=False)
fraction = floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False)
max_pos = floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False)


# ── kelly_fraction_raw ───────────────────────────────────────


@given(p=profit, c=cost)
def test_raw_fraction_positive_with_edge(p, c):
    assert kelly_fraction_raw(p, c) > 0.0


@given(c=cost)
def test_raw_fraction_zero_with_no_edge(c):
    assert kelly_fraction_raw(0.0, c) == 0.0
    assert kelly_fraction_raw(-0.01, c) == 0.0


@given(p=profit)
def test_raw_fraction_zero_with_invalid_cost(p):
    assert kelly_fraction_raw(p, 0.0) == 0.0
    assert kelly_fraction_raw(p, -1.0) == 0.0


@given(p=profit, c=cost)
def test_raw_fraction_is_ratio(p, c):
    assert abs(kelly_fraction_raw(p, c) - p / c) < 1e-10


# ── kelly_size: zero-output cases ────────────────────────────


@given(c=cost, b=bankroll)
def test_zero_size_when_no_edge(c, b):
    assert kelly_size(0.0, c, b) == 0.0


@given(p=profit, b=bankroll)
def test_zero_size_when_invalid_cost(p, b):
    assert kelly_size(p, 0.0, b) == 0.0


@given(p=profit, c=cost)
def test_zero_size_when_no_bankroll(p, c):
    assert kelly_size(p, c, 0.0) == 0.0


# ── kelly_size: cap properties ───────────────────────────────


@given(p=profit, c=cost, b=bankroll, mp=max_pos)
def test_size_respects_max_position(p, c, b, mp):
    s = kelly_size(p, c, b, max_position=mp)
    assert s <= mp + 1e-10


@given(p=profit, c=cost, b=bankroll)
def test_size_respects_bankroll_cap(p, c, b):
    """Can't buy more contracts than bankroll allows."""
    s = kelly_size(p, c, b)
    bankroll_cap = b / c
    assert s <= bankroll_cap + 1e-10


# ── kelly_size: monotonicity ────────────────────────────────


@given(p=profit, c=cost, b=bankroll)
@settings(max_examples=200)
def test_size_increases_with_edge(p, c, b):
    """Doubling the edge should not decrease the size."""
    s1 = kelly_size(p, c, b, max_position=1e9)
    s2 = kelly_size(p * 2, c, b, max_position=1e9)
    assert s2 >= s1 - 1e-10


@given(p=profit, c=cost, b=bankroll)
@settings(max_examples=200)
def test_size_increases_with_bankroll(p, c, b):
    """Doubling bankroll should not decrease the size."""
    s1 = kelly_size(p, c, b, max_position=1e9)
    s2 = kelly_size(p, c, b * 2, max_position=1e9)
    assert s2 >= s1 - 1e-10


# ── kelly_size: fraction scaling ─────────────────────────────


@given(p=profit, c=cost, b=bankroll)
def test_half_kelly_is_half(p, c, b):
    """Half-Kelly <= full-Kelly size. Exact halving holds when uncapped and above floor."""
    full = kelly_size(p, c, b, fraction=1.0, max_position=1e9, min_position=0.0)
    half = kelly_size(p, c, b, fraction=0.5, max_position=1e9, min_position=0.0)
    if full > 0:
        # Half-Kelly is always <= full Kelly
        assert half <= full + 1e-8
        # When full Kelly is not bankroll-capped, halving is exact
        bankroll_cap = b / c
        if full < bankroll_cap - 1e-6:
            assert abs(half - full / 2) < 1e-8


@given(p=profit, c=cost, b=bankroll)
def test_full_kelly_gte_half_kelly(p, c, b):
    full = kelly_size(p, c, b, fraction=1.0)
    half = kelly_size(p, c, b, fraction=0.5)
    assert full >= half - 1e-10


# ── kelly_size: min_position floor ───────────────────────────


@given(p=profit, c=cost, b=bankroll)
def test_size_is_zero_or_gte_min(p, c, b):
    """Result is either 0.0 (below floor) or >= min_position."""
    s = kelly_size(p, c, b, min_position=5.0)
    assert s == 0.0 or s >= 5.0


@given(b=bankroll)
def test_tiny_edge_below_min_returns_zero(b):
    """Very small edge should produce size below min → return 0."""
    s = kelly_size(0.0001, 1.0, b, fraction=0.01, min_position=1.0)
    # With fraction=0.01 and tiny edge, Kelly size is very small
    if b < 10000:  # avoid gigantic bankrolls overwhelming the tiny edge
        assert s == 0.0
