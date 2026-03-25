from __future__ import annotations

from polyarb.config import Config
from polyarb.models import Action, ArbType, Opportunity, Order, OrderSet, Side


def build_order_set(opp: Opportunity, config: Config) -> OrderSet:
    size = config.order_size
    orders: list[Order] = []
    total_cost = 0.0
    expected_payout = 0.0

    if opp.arb_type == ArbType.SINGLE_UNDERPRICE:
        m = opp.markets[0]
        # Buy YES at ask + Buy NO at ask → guaranteed $1 payout
        yes_price = m.yes_token.best_ask
        no_price = m.no_token.best_ask
        orders.append(Order(m.yes_token.token_id, Side.YES, Action.BUY, yes_price, size))
        orders.append(Order(m.no_token.token_id, Side.NO, Action.BUY, no_price, size))
        total_cost = (yes_price + no_price) * size
        expected_payout = 1.0 * size

    elif opp.arb_type == ArbType.SINGLE_OVERPRICE:
        m = opp.markets[0]
        # Sell YES at bid + Sell NO at bid → pay $1 on resolution
        yes_price = m.yes_token.best_bid
        no_price = m.no_token.best_bid
        orders.append(Order(m.yes_token.token_id, Side.YES, Action.SELL, yes_price, size))
        orders.append(Order(m.no_token.token_id, Side.NO, Action.SELL, no_price, size))
        total_cost = 1.0 * size  # liability: must pay $1 on one side
        expected_payout = (yes_price + no_price) * size

    elif opp.arb_type == ArbType.MULTI_UNDERPRICE:
        # Buy all YES tokens → exactly one pays $1
        for m in opp.markets:
            price = m.yes_token.best_ask
            orders.append(Order(m.yes_token.token_id, Side.YES, Action.BUY, price, size))
            total_cost += price * size
        expected_payout = 1.0 * size

    elif opp.arb_type == ArbType.MULTI_OVERPRICE:
        # Buy all NO tokens → (N-1) pay $1
        for m in opp.markets:
            price = m.no_token.best_ask
            orders.append(Order(m.no_token.token_id, Side.NO, Action.BUY, price, size))
            total_cost += price * size
        expected_payout = (len(opp.markets) - 1) * 1.0 * size

    return OrderSet(
        opportunity=opp,
        orders=orders,
        total_cost=round(total_cost, 6),
        expected_payout=round(expected_payout, 6),
    )
