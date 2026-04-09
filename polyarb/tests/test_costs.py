"""Tests for cross-platform arbitrage cost model."""

from polyarb.analysis.costs import (
    FeeParams,
    compute_arb,
    is_profitable,
    kalshi_entry_fee,
    poly_taker_fee,
)

# ── Fee calculations ──────────────────────────────────────────


def test_poly_fee_at_midpoint():
    """Peak fee occurs at p=0.50."""
    fee = poly_taker_fee(0.50, fee_rate=0.05)
    assert abs(fee - 0.0125) < 1e-9  # 0.05 * 0.5 * 0.5


def test_poly_fee_at_extremes():
    """Fee approaches zero at p near 0 or 1."""
    fee_low = poly_taker_fee(0.05, fee_rate=0.05)
    fee_high = poly_taker_fee(0.95, fee_rate=0.05)
    assert fee_low < 0.003
    assert fee_high < 0.003
    assert abs(fee_low - fee_high) < 1e-9  # symmetric


def test_poly_fee_zero_rate():
    """Geopolitics category has 0% fee."""
    assert poly_taker_fee(0.50, fee_rate=0.0) == 0.0


def test_poly_fee_crypto_rate():
    """Crypto has highest fee rate at 7.2%."""
    fee = poly_taker_fee(0.50, fee_rate=0.072)
    assert abs(fee - 0.018) < 1e-9  # 0.072 * 0.25


def test_kalshi_fee_midpoint():
    """Mid-price contract hits the cap."""
    fee = kalshi_entry_fee(0.50, fee_cap=0.02)
    assert fee == 0.02  # 0.07 * 0.50 = 0.035 > cap


def test_kalshi_fee_cheap_contract():
    """Cheap contract fee is below cap."""
    fee = kalshi_entry_fee(0.10, fee_cap=0.02)
    assert abs(fee - 0.007) < 1e-9  # 0.07 * 0.10


def test_kalshi_fee_expensive_contract():
    """Expensive contract fee uses (1-p) side."""
    fee = kalshi_entry_fee(0.95, fee_cap=0.02)
    assert abs(fee - 0.0035) < 1e-9  # 0.07 * 0.05


# ── Arb computation ──────────────────────────────────────────


def test_clear_arb_profitable():
    """Large price gap survives fees."""
    result = compute_arb(
        poly_yes_ask=0.40,
        poly_no_ask=0.62,
        kalshi_yes_ask=0.55,
        kalshi_no_ask=0.50,
    )
    # Direction A: buy YES@0.40 on Poly + NO@0.50 on Kalshi = 0.90 gross
    assert result is not None
    assert result.direction == "poly_yes_kalshi_no"
    assert is_profitable(result)
    assert result.net_profit > 0.05  # ~$0.06 before fees, some eaten


def test_no_arb_when_prices_balanced():
    """Efficient pricing → no arb."""
    result = compute_arb(
        poly_yes_ask=0.52,
        poly_no_ask=0.52,
        kalshi_yes_ask=0.52,
        kalshi_no_ask=0.52,
    )
    assert result is not None
    assert not is_profitable(result)
    # Gross cost = 1.04 > 1.0, already negative before fees
    assert result.net_profit < 0


def test_fees_eat_small_delta():
    """A 3¢ raw delta is not profitable after fees."""
    # Direction A gross: 0.48 + 0.49 = 0.97 → 3¢ raw delta
    result = compute_arb(
        poly_yes_ask=0.48,
        poly_no_ask=0.54,
        kalshi_yes_ask=0.53,
        kalshi_no_ask=0.49,
    )
    assert result is not None
    assert result.direction == "poly_yes_kalshi_no"
    # Poly fee: 0.05 * 0.48 * 0.52 ≈ 0.01248
    # Kalshi fee: min(0.07*0.49, 0.02) = 0.02
    # Total fees ≈ 0.032, delta was 0.03 → net negative
    assert not is_profitable(result)


def test_picks_better_direction():
    """Returns the direction with higher net profit."""
    result = compute_arb(
        poly_yes_ask=0.35,
        poly_no_ask=0.70,
        kalshi_yes_ask=0.60,
        kalshi_no_ask=0.45,
    )
    assert result is not None
    # Dir A: 0.35 + 0.45 = 0.80 gross → ~17¢ raw delta
    # Dir B: 0.70 + 0.60 = 1.30 gross → very negative
    assert result.direction == "poly_yes_kalshi_no"
    assert is_profitable(result)


def test_reverse_direction_profitable():
    """Arb exists in the poly_no + kalshi_yes direction."""
    result = compute_arb(
        poly_yes_ask=0.65,
        poly_no_ask=0.38,
        kalshi_yes_ask=0.45,
        kalshi_no_ask=0.60,
    )
    assert result is not None
    # Dir B: 0.38 + 0.45 = 0.83 gross → 17¢ raw delta
    assert result.direction == "poly_no_kalshi_yes"
    assert is_profitable(result)


def test_custom_fee_params():
    """Custom fee params change profitability."""
    fees_high = FeeParams(poly_fee_rate=0.072, kalshi_fee_cap=0.02)
    fees_zero = FeeParams(poly_fee_rate=0.0, kalshi_fee_cap=0.0)

    result_high = compute_arb(0.45, 0.58, 0.53, 0.50, fees=fees_high)
    result_zero = compute_arb(0.45, 0.58, 0.53, 0.50, fees=fees_zero)

    assert result_zero is not None
    assert result_high is not None
    assert result_zero.net_profit > result_high.net_profit
    # With zero fees the raw delta is the full profit
    assert abs(result_zero.net_profit - (1.0 - 0.45 - 0.50)) < 1e-6


def test_breakeven_threshold():
    """Find the minimum delta that's profitable at default fees."""
    # At p≈0.50 on both sides:
    # Poly fee ≈ 0.05 * 0.5 * 0.5 = 0.0125
    # Kalshi fee = 0.02 (capped)
    # Total fees ≈ 0.0325
    # So need raw delta > ~3.3¢ for profitability

    # 4¢ delta should be profitable
    result_4c = compute_arb(
        poly_yes_ask=0.48,
        poly_no_ask=0.56,
        kalshi_yes_ask=0.56,
        kalshi_no_ask=0.48,
    )
    assert result_4c is not None
    assert result_4c.gross_cost == 0.96  # 4¢ raw delta
    assert is_profitable(result_4c)

    # 2¢ delta should not be
    result_2c = compute_arb(
        poly_yes_ask=0.49,
        poly_no_ask=0.55,
        kalshi_yes_ask=0.55,
        kalshi_no_ask=0.49,
    )
    assert result_2c is not None
    assert result_2c.gross_cost == 0.98  # 2¢ raw delta
    assert not is_profitable(result_2c)


def test_arb_result_fields():
    """All ArbResult fields are populated correctly."""
    fees = FeeParams(poly_fee_rate=0.05, kalshi_fee_cap=0.02)
    result = compute_arb(0.40, 0.62, 0.55, 0.50, fees=fees)

    assert result is not None
    assert result.poly_ask == 0.40
    assert result.kalshi_ask == 0.50
    assert result.gross_cost == 0.90
    pf = 0.05 * 0.40 * 0.60  # 0.012
    kf = min(0.07 * 0.50, 0.02)  # 0.02
    assert abs(result.poly_fee - pf) < 1e-6
    assert abs(result.kalshi_fee - kf) < 1e-6
    assert abs(result.net_profit - (1.0 - 0.90 - pf - kf)) < 1e-6
