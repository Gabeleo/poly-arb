from __future__ import annotations

import random

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token


def _tok(token_id: str, side: Side, mid: float) -> Token:
    spread = 0.01
    return Token(
        token_id=token_id,
        side=side,
        midpoint=round(mid, 4),
        best_bid=round(max(0.001, mid - spread), 4),
        best_ask=round(min(0.999, mid + spread), 4),
    )


def _market(
    cid: str,
    question: str,
    yes_mid: float,
    no_mid: float,
    neg_risk: bool = False,
    event_slug: str = "",
) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=_tok(f"{cid}_yes", Side.YES, yes_mid),
        no_token=_tok(f"{cid}_no", Side.NO, no_mid),
        neg_risk=neg_risk,
        event_slug=event_slug,
    )


class MockDataProvider:
    def __init__(self, drift: bool = False) -> None:
        self._drift = drift
        self._tick = 0

    def _jitter(self, base: float, amount: float = 0.005) -> float:
        if not self._drift:
            return base
        return round(base + random.uniform(-amount, amount), 4)

    def get_active_markets(self) -> list[Market]:
        self._tick += 1
        return [
            # Normal market — no arb
            _market(
                "normal_1",
                "Will BTC hit $100k by July?",
                self._jitter(0.62),
                self._jitter(0.38),
            ),
            # Single-condition underprice: YES + NO < 1
            _market(
                "single_under",
                "Will ETH flip BTC by 2027?",
                self._jitter(0.40),
                self._jitter(0.52),
            ),
            # Single-condition overprice: YES + NO > 1
            _market(
                "single_over",
                "Will SOL reach $500?",
                self._jitter(0.55),
                self._jitter(0.52),
            ),
            # NegRisk markets (part of events)
            _market(
                "pres_rep",
                "Republican wins 2028?",
                self._jitter(0.45),
                self._jitter(0.55),
                neg_risk=True,
                event_slug="2028-pres",
            ),
            _market(
                "pres_dem",
                "Democrat wins 2028?",
                self._jitter(0.35),
                self._jitter(0.65),
                neg_risk=True,
                event_slug="2028-pres",
            ),
            _market(
                "pres_ind",
                "Independent wins 2028?",
                self._jitter(0.12),
                self._jitter(0.88),
                neg_risk=True,
                event_slug="2028-pres",
            ),
        ]

    def get_events(self) -> list[Event]:
        return group_events(self.get_active_markets())

    def search_markets(self, query: str, limit: int = 5) -> list[Market]:
        q = query.lower()
        return [m for m in self.get_active_markets() if q in m.question.lower()][:limit]

    def get_expiring_within(self, hours: float, limit: int = 5) -> list[Market]:
        return []  # mock markets have no end dates
