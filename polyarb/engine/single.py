from __future__ import annotations

from polyarb.config import Config
from polyarb.models import ArbType, Market, Opportunity


def detect_single(markets: list[Market], config: Config) -> list[Opportunity]:
    opps: list[Opportunity] = []
    for m in markets:
        yes_p = m.yes_token.midpoint
        no_p = m.no_token.midpoint

        if yes_p > config.max_prob or no_p > config.max_prob:
            continue

        total = yes_p + no_p
        deviation = total - 1.0

        if abs(deviation) < config.min_profit:
            continue

        if deviation < 0:
            # Underprice: buy YES + buy NO, guaranteed $1 payout
            profit = abs(deviation)
            opps.append(Opportunity(
                arb_type=ArbType.SINGLE_UNDERPRICE,
                markets=[m],
                expected_profit_per_share=round(profit, 6),
            ))
        else:
            # Overprice: sell YES + sell NO, collect > $1
            profit = deviation
            opps.append(Opportunity(
                arb_type=ArbType.SINGLE_OVERPRICE,
                markets=[m],
                expected_profit_per_share=round(profit, 6),
            ))

    return opps
