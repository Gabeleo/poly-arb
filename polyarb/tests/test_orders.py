from polyarb.config import Config
from polyarb.execution.orders import build_order_set
from polyarb.models import (
    Action, ArbType, Event, Market, Opportunity, Side, Token,
)


def _market(cid: str, yes_mid: float, no_mid: float) -> Market:
    spread = 0.01
    return Market(
        condition_id=cid,
        question=f"Market {cid}",
        yes_token=Token(f"{cid}_y", Side.YES, yes_mid, yes_mid - spread, yes_mid + spread),
        no_token=Token(f"{cid}_n", Side.NO, no_mid, no_mid - spread, no_mid + spread),
        neg_risk=True,
        event_slug="test",
    )


def test_single_underprice_orders():
    config = Config(order_size=10)
    m = _market("x", 0.40, 0.52)
    opp = Opportunity(ArbType.SINGLE_UNDERPRICE, (m,), expected_profit_per_share=0.08)
    os = build_order_set(opp, config)

    assert len(os.orders) == 2
    assert os.orders[0].action == Action.BUY
    assert os.orders[0].side == Side.YES
    assert os.orders[1].action == Action.BUY
    assert os.orders[1].side == Side.NO
    # Cost = (0.41 + 0.53) * 10 = 9.4, Payout = 10
    assert os.expected_payout == 10.0
    assert os.expected_profit > 0


def test_single_overprice_orders():
    config = Config(order_size=10)
    m = _market("x", 0.55, 0.52)
    opp = Opportunity(ArbType.SINGLE_OVERPRICE, (m,), expected_profit_per_share=0.07)
    os = build_order_set(opp, config)

    assert len(os.orders) == 2
    assert os.orders[0].action == Action.SELL
    assert os.orders[0].side == Side.YES
    assert os.orders[1].action == Action.SELL
    assert os.orders[1].side == Side.NO
    assert os.expected_profit > 0


def test_multi_underprice_orders():
    config = Config(order_size=5)
    markets = (_market("a", 0.40, 0.60), _market("b", 0.30, 0.70), _market("c", 0.20, 0.80))
    event = Event("e", "Test", markets)
    opp = Opportunity(ArbType.MULTI_UNDERPRICE, markets, event=event, expected_profit_per_share=0.10)
    os = build_order_set(opp, config)

    assert len(os.orders) == 3
    assert all(o.action == Action.BUY and o.side == Side.YES for o in os.orders)
    assert os.expected_payout == 5.0
    assert os.expected_profit > 0


def test_multi_overprice_orders():
    config = Config(order_size=5)
    markets = (_market("a", 0.50, 0.50), _market("b", 0.40, 0.60), _market("c", 0.20, 0.80))
    event = Event("e", "Test", markets)
    opp = Opportunity(ArbType.MULTI_OVERPRICE, markets, event=event, expected_profit_per_share=0.10)
    os = build_order_set(opp, config)

    assert len(os.orders) == 3
    assert all(o.action == Action.BUY and o.side == Side.NO for o in os.orders)
    # Payout = (3-1) * 5 = 10
    assert os.expected_payout == 10.0


def test_order_set_describe():
    config = Config(order_size=1)
    m = _market("x", 0.40, 0.52)
    opp = Opportunity(ArbType.SINGLE_UNDERPRICE, (m,), expected_profit_per_share=0.08)
    os = build_order_set(opp, config)
    desc = os.describe()
    assert "SINGLE_UNDERPRICE" in desc
    assert "BUY" in desc
    assert "Profit" in desc
