from __future__ import annotations

from polyarb.config import Config
from polyarb.models import ArbType, Event, Opportunity


def detect_multi(events: list[Event], config: Config) -> list[Opportunity]:
    opps: list[Opportunity] = []
    for event in events:
        if len(event.markets) < 2:
            continue

        # Check max_prob filter on individual markets
        if any(m.yes_token.midpoint > config.max_prob for m in event.markets):
            continue

        yes_sum = event.yes_sum
        deviation = yes_sum - 1.0

        if abs(deviation) < config.min_profit:
            continue

        if deviation < 0:
            # Underprice: buy all YES tokens, one must pay $1
            profit = abs(deviation)
            opps.append(Opportunity(
                arb_type=ArbType.MULTI_UNDERPRICE,
                markets=list(event.markets),
                event=event,
                expected_profit_per_share=round(profit, 6),
            ))
        else:
            # Overprice: buy all NO tokens
            # Cost = sum(NO prices), payout = (N-1) * $1
            no_sum = sum(m.no_token.midpoint for m in event.markets)
            n = len(event.markets)
            profit = (n - 1) - no_sum
            if profit > config.min_profit:
                opps.append(Opportunity(
                    arb_type=ArbType.MULTI_OVERPRICE,
                    markets=list(event.markets),
                    event=event,
                    expected_profit_per_share=round(profit, 6),
                ))

    return opps
