"""Property-based tests for fee calculations and cost model."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis.strategies import floats

from polyarb.analysis.costs import (
    FeeParams,
    compute_arb,
    kalshi_entry_fee,
    poly_taker_fee,
)
from polyarb.fees import net_profit_cross

# ── Strategies ───────────────────────────────────────────────

price = floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
size = floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
fee_rate = floats(min_value=0.0, max_value=0.10, allow_nan=False, allow_infinity=False)


# ── Poly fee properties ──────────────────────────────────────


@given(p=price, rate=fee_rate)
def test_poly_fee_non_negative(p, rate):
    assert poly_taker_fee(p, rate) >= 0.0


@given(p=price, rate=fee_rate)
def test_poly_fee_symmetric(p, rate):
    """fee(p) == fee(1-p): symmetric around 0.50."""
    assert abs(poly_taker_fee(p, rate) - poly_taker_fee(1.0 - p, rate)) < 1e-10


@given(p=price, rate=fee_rate)
def test_poly_fee_peaks_at_half(p, rate):
    """fee(0.50) >= fee(p) for all p."""
    assert poly_taker_fee(0.50, rate) >= poly_taker_fee(p, rate) - 1e-10


@given(p=price, rate=fee_rate)
def test_poly_fee_bounded(p, rate):
    """Maximum possible fee is rate * 0.25 (at p=0.50)."""
    assert poly_taker_fee(p, rate) <= rate * 0.25 + 1e-10


# ── Kalshi fee properties ────────────────────────────────────


@given(p=price)
def test_kalshi_fee_non_negative(p):
    assert kalshi_entry_fee(p, 0.02) >= 0.0


@given(p=price)
def test_kalshi_fee_capped(p):
    cap = 0.02
    assert kalshi_entry_fee(p, cap) <= cap + 1e-10


@given(p=price)
def test_kalshi_fee_symmetric(p):
    """fee(p) == fee(1-p): uses min(p, 1-p)."""
    assert abs(kalshi_entry_fee(p, 0.02) - kalshi_entry_fee(1.0 - p, 0.02)) < 1e-10


# ── compute_arb properties ──────────────────────────────────


@given(
    py_ask=price,
    pn_ask=price,
    ky_ask=price,
    kn_ask=price,
)
def test_compute_arb_returns_best_direction(py_ask, pn_ask, ky_ask, kn_ask):
    """compute_arb always returns the more profitable direction."""
    result = compute_arb(py_ask, pn_ask, ky_ask, kn_ask)
    assert result is not None
    # Check that the other direction is not more profitable
    fees = FeeParams()
    dir_a_profit = 1.0 - (py_ask + kn_ask) - poly_taker_fee(py_ask, fees.poly_fee_rate) - kalshi_entry_fee(kn_ask, fees.kalshi_fee_cap)
    dir_b_profit = 1.0 - (pn_ask + ky_ask) - poly_taker_fee(pn_ask, fees.poly_fee_rate) - kalshi_entry_fee(ky_ask, fees.kalshi_fee_cap)
    expected_profit = max(dir_a_profit, dir_b_profit)
    assert abs(result.net_profit - round(expected_profit, 6)) < 1e-5


@given(
    py_ask=price,
    pn_ask=price,
    ky_ask=price,
    kn_ask=price,
)
def test_compute_arb_gross_cost_is_sum(py_ask, pn_ask, ky_ask, kn_ask):
    """gross_cost = poly_ask + kalshi_ask for the chosen direction."""
    result = compute_arb(py_ask, pn_ask, ky_ask, kn_ask)
    assert result is not None
    assert abs(result.gross_cost - (result.poly_ask + result.kalshi_ask)) < 1e-5


@given(
    py_ask=price,
    pn_ask=price,
    ky_ask=price,
    kn_ask=price,
)
def test_compute_arb_profit_identity(py_ask, pn_ask, ky_ask, kn_ask):
    """net_profit = 1.0 - gross_cost - poly_fee - kalshi_fee."""
    result = compute_arb(py_ask, pn_ask, ky_ask, kn_ask)
    assert result is not None
    expected = 1.0 - result.gross_cost - result.poly_fee - result.kalshi_fee
    assert abs(result.net_profit - round(expected, 6)) < 1e-5


@given(
    py_ask=price,
    pn_ask=price,
    ky_ask=price,
    kn_ask=price,
)
def test_arb_result_is_frozen(py_ask, pn_ask, ky_ask, kn_ask):
    result = compute_arb(py_ask, pn_ask, ky_ask, kn_ask)
    assert result is not None
    try:
        result.net_profit = 999.0  # type: ignore[misc]
        raise AssertionError("Should be frozen")
    except AttributeError:
        pass


# ── Cross-platform net profit ────────────────────────────────


@given(k_price=price, p_price=price, s=size)
def test_net_profit_cross_deterministic(k_price, p_price, s):
    """Same inputs always produce same result."""
    a = net_profit_cross(k_price, p_price, s)
    b = net_profit_cross(k_price, p_price, s)
    assert a == b


@given(k_price=price, p_price=price, s=size)
@settings(max_examples=200)
def test_fees_reduce_gross(k_price, p_price, s):
    """Net profit is always <= gross profit (fees are non-negative)."""
    gross = (1.0 - k_price - p_price) * s
    net = net_profit_cross(k_price, p_price, s)
    assert net <= gross + 1e-8
