from polyarb.config import Config
from polyarb.engine.multi import detect_multi
from polyarb.models import ArbType, Event, Market, Side, Token


def _market(cid: str, yes_mid: float, no_mid: float) -> Market:
    return Market(
        condition_id=cid,
        question=f"Option {cid}",
        yes_token=Token(f"{cid}_y", Side.YES, yes_mid, yes_mid - 0.01, yes_mid + 0.01),
        no_token=Token(f"{cid}_n", Side.NO, no_mid, no_mid - 0.01, no_mid + 0.01),
        neg_risk=True,
        event_slug="test-event",
    )


def _event(markets: list[Market]) -> Event:
    return Event(slug="test-event", title="Test Event", markets=tuple(markets))


def test_normal_event_no_arb():
    config = Config(min_profit=0.005)
    # 0.50 + 0.30 + 0.20 = 1.00
    event = _event([_market("a", 0.50, 0.50), _market("b", 0.30, 0.70), _market("c", 0.20, 0.80)])
    assert detect_multi([event], config) == []


def test_underprice_detected():
    config = Config(min_profit=0.005)
    # 0.40 + 0.30 + 0.20 = 0.90 → deviation=-0.10
    event = _event([_market("a", 0.40, 0.60), _market("b", 0.30, 0.70), _market("c", 0.20, 0.80)])
    opps = detect_multi([event], config)
    assert len(opps) == 1
    assert opps[0].arb_type == ArbType.MULTI_UNDERPRICE
    assert abs(opps[0].expected_profit_per_share - 0.10) < 0.001


def test_overprice_detected():
    config = Config(min_profit=0.005)
    # YES: 0.50 + 0.40 + 0.20 = 1.10 → overprice
    # NO: 0.50 + 0.60 + 0.80 = 1.90, payout = 2*$1 = 2.0, profit = 0.10
    event = _event([_market("a", 0.50, 0.50), _market("b", 0.40, 0.60), _market("c", 0.20, 0.80)])
    opps = detect_multi([event], config)
    assert len(opps) == 1
    assert opps[0].arb_type == ArbType.MULTI_OVERPRICE


def test_single_market_event_ignored():
    config = Config(min_profit=0.005)
    event = _event([_market("a", 0.80, 0.20)])
    assert detect_multi([event], config) == []


def test_high_prob_filtered():
    config = Config(min_profit=0.005, max_prob=0.95)
    event = _event([_market("a", 0.97, 0.03), _market("b", 0.10, 0.90)])
    assert detect_multi([event], config) == []
