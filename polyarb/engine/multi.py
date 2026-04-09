from __future__ import annotations

from polyarb.config import Config
from polyarb.models import ArbType, Event, Opportunity


def detect_multi(events: list[Event], config: Config) -> list[Opportunity]:
    opps: list[Opportunity] = []
    for event in events:
        if len(event.markets) < 2:
            continue

        if any(m.yes_token.midpoint > config.max_prob for m in event.markets):
            continue

        # Underprice: buy all YES at ask, exactly one pays $1
        yes_ask_sum = sum(m.yes_token.best_ask for m in event.markets)
        underprice_profit = 1.0 - yes_ask_sum
        if underprice_profit >= config.min_profit:
            opps.append(
                Opportunity(
                    arb_type=ArbType.MULTI_UNDERPRICE,
                    markets=event.markets,
                    event=event,
                    expected_profit_per_share=round(underprice_profit, 6),
                )
            )

        # Overprice: buy all NO at ask, (N-1) pay $1
        no_ask_sum = sum(m.no_token.best_ask for m in event.markets)
        n = len(event.markets)
        overprice_profit = (n - 1) - no_ask_sum
        if overprice_profit >= config.min_profit:
            opps.append(
                Opportunity(
                    arb_type=ArbType.MULTI_OVERPRICE,
                    markets=event.markets,
                    event=event,
                    expected_profit_per_share=round(overprice_profit, 6),
                )
            )

    return opps
