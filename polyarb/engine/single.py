from __future__ import annotations

from polyarb.config import Config
from polyarb.models import ArbType, Market, Opportunity


def detect_single(markets: list[Market], config: Config) -> list[Opportunity]:
    opps: list[Opportunity] = []
    for m in markets:
        if m.yes_token.midpoint > config.max_prob or m.no_token.midpoint > config.max_prob:
            continue

        # Underprice: buy YES at ask + buy NO at ask → guaranteed $1 payout
        buy_cost = m.yes_token.best_ask + m.no_token.best_ask
        underprice_profit = 1.0 - buy_cost
        if underprice_profit >= config.min_profit:
            opps.append(Opportunity(
                arb_type=ArbType.SINGLE_UNDERPRICE,
                markets=(m,),
                expected_profit_per_share=round(underprice_profit, 6),
            ))

        # Overprice: sell YES at bid + sell NO at bid → pay $1 on resolution
        sell_proceeds = m.yes_token.best_bid + m.no_token.best_bid
        overprice_profit = sell_proceeds - 1.0
        if overprice_profit >= config.min_profit:
            opps.append(Opportunity(
                arb_type=ArbType.SINGLE_OVERPRICE,
                markets=(m,),
                expected_profit_per_share=round(overprice_profit, 6),
            ))

    return opps
