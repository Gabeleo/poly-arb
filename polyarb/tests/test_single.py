from polyarb.config import Config
from polyarb.engine.single import detect_single
from polyarb.models import ArbType, Market, Side, Token


def _market(yes_mid: float, no_mid: float, cid: str = "test") -> Market:
    return Market(
        condition_id=cid,
        question="Test market",
        yes_token=Token(f"{cid}_y", Side.YES, yes_mid, yes_mid - 0.01, yes_mid + 0.01),
        no_token=Token(f"{cid}_n", Side.NO, no_mid, no_mid - 0.01, no_mid + 0.01),
    )


def test_normal_market_no_arb():
    config = Config(min_profit=0.005)
    markets = [_market(0.60, 0.40)]
    assert detect_single(markets, config) == []


def test_underprice_detected():
    config = Config(min_profit=0.005)
    # YES ask=0.41, NO ask=0.53 → buy cost=0.94, profit=0.06
    markets = [_market(0.40, 0.52)]
    opps = detect_single(markets, config)
    assert len(opps) == 1
    assert opps[0].arb_type == ArbType.SINGLE_UNDERPRICE
    assert opps[0].expected_profit_per_share > 0.005


def test_overprice_detected():
    config = Config(min_profit=0.005)
    # YES bid=0.54, NO bid=0.51 → sell proceeds=1.05, profit=0.05
    markets = [_market(0.55, 0.52)]
    opps = detect_single(markets, config)
    assert len(opps) == 1
    assert opps[0].arb_type == ArbType.SINGLE_OVERPRICE
    assert opps[0].expected_profit_per_share > 0.005


def test_high_prob_filtered():
    config = Config(min_profit=0.005, max_prob=0.95)
    # YES=0.97 → filtered out despite sum > 1
    markets = [_market(0.97, 0.10)]
    assert detect_single(markets, config) == []


def test_below_threshold_ignored():
    config = Config(min_profit=0.01)
    # YES ask=0.51, NO ask=0.505 → buy cost=1.015 (not underprice)
    # YES bid=0.49, NO bid=0.485 → sell=0.975 (not overprice)
    markets = [_market(0.50, 0.495)]
    assert detect_single(markets, config) == []


def test_profit_calculation_underprice():
    config = Config(min_profit=0.005)
    markets = [_market(0.40, 0.52)]
    opps = detect_single(markets, config)
    # buy cost = 0.41 + 0.53 = 0.94, profit = 0.06
    assert abs(opps[0].expected_profit_per_share - 0.06) < 0.001


def test_profit_calculation_overprice():
    config = Config(min_profit=0.005)
    markets = [_market(0.55, 0.52)]
    opps = detect_single(markets, config)
    # sell proceeds = 0.54 + 0.51 = 1.05, profit = 0.05
    assert abs(opps[0].expected_profit_per_share - 0.05) < 0.001
