"""Tests for fee calculation functions."""

from polyarb.fees import kalshi_taker_fee, net_profit_cross, net_profit_single, poly_taker_fee

# ── kalshi_taker_fee ───────────────────────────────────────


def test_kalshi_fee_at_midpoint():
    # p=0.50: max fee region. fee = ceil(0.07 * 1 * 0.50 * 0.50 * 100) / 100
    # = ceil(0.07 * 25) / 100 = ceil(1.75) / 100 = 0.02
    assert kalshi_taker_fee(1, 0.50) == 0.02


def test_kalshi_fee_at_low_price():
    # p=0.01: fee = ceil(0.07 * 1 * 0.01 * 0.99 * 100) / 100
    # = ceil(0.07 * 0.99) / 100 = ceil(0.0693) / 100 = ceil(0.0693) = 1 -> 0.01
    assert kalshi_taker_fee(1, 0.01) == 0.01


def test_kalshi_fee_at_high_price():
    # p=0.99: same as p=0.01 due to p*(1-p) symmetry
    assert kalshi_taker_fee(1, 0.99) == 0.01


def test_kalshi_fee_multiple_contracts():
    # 10 contracts at p=0.50: ceil(0.07 * 10 * 0.25 * 100) / 100
    # = ceil(17.5) / 100 = 0.18
    assert kalshi_taker_fee(10, 0.50) == 0.18


def test_kalshi_fee_multiplier():
    # multiplier=0.5 halves the rate
    fee_full = kalshi_taker_fee(1, 0.50, rate=0.07, multiplier=1.0)
    fee_half = kalshi_taker_fee(1, 0.50, rate=0.07, multiplier=0.5)
    assert fee_half < fee_full
    # ceil(0.07 * 0.5 * 0.25 * 100) / 100 = ceil(0.875) / 100 = 0.01
    assert fee_half == 0.01


def test_kalshi_fee_maker_rate():
    # Maker rate 0.0175 at p=0.50: ceil(0.0175 * 0.25 * 100) / 100 = ceil(0.4375) / 100 = 0.01
    assert kalshi_taker_fee(1, 0.50, rate=0.0175) == 0.01


def test_kalshi_fee_ceil_behavior():
    # Verify ceiling: p=0.40, rate=0.07
    # ceil(0.07 * 0.40 * 0.60 * 100) / 100 = ceil(1.68) / 100 = 0.02
    assert kalshi_taker_fee(1, 0.40) == 0.02


# ── poly_taker_fee ────────────────────────────────────────


def test_poly_fee_at_midpoint():
    # p=0.50: round(0.04 * 1 * 0.50 * 0.50, 4) = round(0.01, 4) = 0.01
    assert poly_taker_fee(1, 0.50) == 0.01


def test_poly_fee_at_low_price():
    # p=0.01: round(0.04 * 0.01 * 0.99, 4) = round(0.000396, 4) = 0.0004
    assert poly_taker_fee(1, 0.01) == 0.0004


def test_poly_fee_at_high_price():
    # p=0.99: same as p=0.01 due to symmetry
    assert poly_taker_fee(1, 0.99) == 0.0004


def test_poly_fee_multiple_shares():
    # 10 shares at p=0.50: round(10 * 0.04 * 0.25, 4) = 0.1
    assert poly_taker_fee(10, 0.50) == 0.1


def test_poly_fee_different_rates():
    # Sports rate=0.03 at p=0.50: round(0.03 * 0.25, 4) = 0.0075
    assert poly_taker_fee(1, 0.50, fee_rate=0.03) == 0.0075
    # Crypto rate=0.072 at p=0.50: round(0.072 * 0.25, 4) = 0.018
    assert poly_taker_fee(1, 0.50, fee_rate=0.072) == 0.018


def test_poly_fee_round_to_4_decimals():
    # p=0.41: round(0.04 * 0.41 * 0.59, 4) = round(0.009676, 4) = 0.0097
    assert poly_taker_fee(1, 0.41) == 0.0097


# ── p*(1-p) shape ─────────────────────────────────────────


def test_fee_maximal_at_midpoint():
    """Fee is highest at p=0.50 and decreases toward extremes."""
    mid = poly_taker_fee(1, 0.50)
    low = poly_taker_fee(1, 0.10)
    high = poly_taker_fee(1, 0.90)
    assert mid > low
    assert mid > high


def test_fee_symmetric():
    """p*(1-p) is symmetric around 0.50."""
    assert poly_taker_fee(1, 0.30) == poly_taker_fee(1, 0.70)
    assert kalshi_taker_fee(1, 0.20) == kalshi_taker_fee(1, 0.80)


def test_fee_approaches_zero_at_extremes():
    """Fees approach zero as price approaches 0 or 1."""
    assert poly_taker_fee(1, 0.01) < 0.001
    assert poly_taker_fee(1, 0.99) < 0.001


# ── net_profit_single ──────────────────────────────────────


def test_net_profit_single_underprice_poly():
    # YES=0.41, NO=0.53, buy_cost=0.94, gross=0.06
    # fees: poly_taker_fee(10, 0.41, 0.04) + poly_taker_fee(10, 0.53, 0.04)
    profit = net_profit_single(0.41, 0.53, "SINGLE_UNDERPRICE", 10, "polymarket")
    assert profit < 0.06 * 10  # less than gross
    assert profit > 0  # still profitable


def test_net_profit_single_overprice_poly():
    # YES bid=0.54, NO bid=0.51, proceeds=1.05, gross=0.05
    profit = net_profit_single(0.54, 0.51, "SINGLE_OVERPRICE", 10, "polymarket")
    assert profit < 0.05 * 10
    assert profit > 0


def test_net_profit_cross():
    # Kalshi YES ask=0.41, Poly NO ask=0.51, gross=0.08 per share
    profit = net_profit_cross(0.41, 0.51, 10, kalshi_rate=0.07, poly_rate=0.04)
    assert profit < 0.08 * 10
    assert profit > 0
