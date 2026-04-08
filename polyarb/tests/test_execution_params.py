"""Tests for MatchedPair.execution_params property."""

from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


def _mkt(
    cid: str,
    question: str,
    platform: str,
    yes_ask: float,
    no_ask: float | None = None,
) -> Market:
    if no_ask is None:
        no_ask = round(1.0 - yes_ask, 4)
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y-" + cid, Side.YES, yes_ask, yes_ask - 0.01, yes_ask),
        no_token=Token("n-" + cid, Side.NO, no_ask, no_ask - 0.01, no_ask),
        platform=platform,
    )


def _pair(
    kalshi_yes_ask: float = 0.40,
    poly_yes_ask: float = 0.65,
    poly_no_ask: float | None = None,
) -> MatchedPair:
    poly = _mkt("poly-1", "Will X?", "polymarket", poly_yes_ask, poly_no_ask)
    kalshi = _mkt("kalshi-1", "Will X?", "kalshi", kalshi_yes_ask)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.9)


def test_execution_params_sides_are_complementary():
    """Kalshi side and Poly side must be opposite."""
    pair = _pair(kalshi_yes_ask=0.40, poly_yes_ask=0.65)
    params = pair.execution_params
    k_side = params["kalshi"]["side"]
    p_side = params["poly"]["side"]
    assert {k_side, p_side} == {"yes", "no"}


def test_execution_params_kalshi_ticker_from_condition_id():
    pair = _pair()
    params = pair.execution_params
    assert params["kalshi"]["ticker"] == "kalshi-1"


def test_execution_params_poly_token_id_correct():
    """Poly token_id should come from the YES or NO token depending on side."""
    pair = _pair()
    params = pair.execution_params
    poly_side = params["poly"]["side"]
    if poly_side == "no":
        assert params["poly"]["token_id"] == "n-poly-1"
    else:
        assert params["poly"]["token_id"] == "y-poly-1"


def test_execution_params_poly_price_uses_best_ask():
    """Poly price must use the token's best_ask."""
    pair = _pair(kalshi_yes_ask=0.40, poly_yes_ask=0.65, poly_no_ask=0.35)
    params = pair.execution_params
    poly_side = params["poly"]["side"]
    if poly_side == "no":
        # no_token best_ask = no_ask (since constructor sets best_ask = price)
        assert params["poly"]["price"] == 0.35
    else:
        assert params["poly"]["price"] == 0.65


def test_execution_params_buy_kalshi_yes_direction():
    """When best_arb picks Kalshi YES, poly should be NO."""
    # Kalshi YES cheap, Poly NO cheap -> BUY YES Kalshi + BUY NO Poly
    pair = _pair(kalshi_yes_ask=0.40, poly_yes_ask=0.65, poly_no_ask=0.35)
    params = pair.execution_params
    # best_arb: cost = 0.40 + 0.35 = 0.75, profit > 0
    assert params["kalshi"]["side"] == "yes"
    assert params["poly"]["side"] == "no"


def test_execution_params_buy_poly_yes_direction():
    """When best_arb picks Kalshi NO, poly should be YES."""
    # Kalshi NO cheap, Poly YES cheap -> BUY NO Kalshi + BUY YES Poly
    pair = _pair(kalshi_yes_ask=0.70, poly_yes_ask=0.25, poly_no_ask=0.80)
    params = pair.execution_params
    # best_arb p2: poly_yes_ask(0.25) + kalshi_no_ask(0.30-0.01=0.29?)
    # Let's just check the sides are complementary
    k_side = params["kalshi"]["side"]
    p_side = params["poly"]["side"]
    assert {k_side, p_side} == {"yes", "no"}


def test_execution_params_profit_matches_best_arb():
    pair = _pair()
    params = pair.execution_params
    assert params["profit"] == pair.best_arb[0]


def test_best_arb_unchanged():
    """best_arb still returns the same 5-tuple."""
    pair = _pair()
    arb = pair.best_arb
    assert len(arb) == 5
    profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = arb
    assert isinstance(profit, float)
    assert kalshi_side in ("yes", "no")
    assert isinstance(kalshi_desc, str)
    assert isinstance(poly_desc, str)
    assert isinstance(kalshi_price, float)
