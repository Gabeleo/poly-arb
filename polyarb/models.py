from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256


class Side(Enum):
    YES = "YES"
    NO = "NO"


class Action(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ArbType(Enum):
    SINGLE_UNDERPRICE = "SINGLE_UNDERPRICE"
    SINGLE_OVERPRICE = "SINGLE_OVERPRICE"
    MULTI_UNDERPRICE = "MULTI_UNDERPRICE"
    MULTI_OVERPRICE = "MULTI_OVERPRICE"


@dataclass(frozen=True)
class Token:
    token_id: str
    side: Side
    midpoint: float
    best_bid: float = 0.0
    best_ask: float = 0.0

    @property
    def display_price(self) -> str:
        return f"{self.midpoint:.3f}"


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    yes_token: Token
    no_token: Token
    neg_risk: bool = False
    event_slug: str = ""
    slug: str = ""
    volume: float = 0.0
    end_date: datetime | None = None
    platform: str = "polymarket"

    @property
    def url(self) -> str:
        if not self.event_slug:
            return ""
        if self.platform == "kalshi":
            return f"https://kalshi.com/events/{self.event_slug}"
        return f"https://polymarket.com/event/{self.event_slug}"

    @property
    def yes_no_sum(self) -> float:
        return self.yes_token.midpoint + self.no_token.midpoint

    @property
    def spread(self) -> float:
        return abs(self.yes_no_sum - 1.0)


@dataclass(frozen=True)
class Event:
    slug: str
    title: str
    markets: tuple[Market, ...] = ()

    @property
    def yes_sum(self) -> float:
        return sum(m.yes_token.midpoint for m in self.markets)


@dataclass(frozen=True)
class Opportunity:
    arb_type: ArbType
    markets: tuple[Market, ...]
    event: Event | None = None
    expected_profit_per_share: float = 0.0

    @property
    def key(self) -> str:
        ids = sorted(m.condition_id for m in self.markets)
        return sha256(f"{self.arb_type.value}:{'|'.join(ids)}".encode()).hexdigest()[:12]

    def summary(self) -> str:
        if self.arb_type in (ArbType.SINGLE_UNDERPRICE, ArbType.SINGLE_OVERPRICE):
            m = self.markets[0]
            return (
                f"[{self.arb_type.value}] {m.question}\n"
                f"  YES={m.yes_token.display_price} NO={m.no_token.display_price} "
                f"sum={m.yes_no_sum:.3f} profit/share=${self.expected_profit_per_share:.4f}"
            )
        else:
            title = self.event.title if self.event else "Unknown"
            prices = " | ".join(
                f"{m.question}: YES={m.yes_token.display_price}" for m in self.markets
            )
            yes_sum = sum(m.yes_token.midpoint for m in self.markets)
            return (
                f"[{self.arb_type.value}] {title}\n"
                f"  {prices}\n"
                f"  sum(YES)={yes_sum:.3f} profit/share=${self.expected_profit_per_share:.4f}"
            )


@dataclass(frozen=True)
class Order:
    token_id: str
    side: Side
    action: Action
    price: float
    size: float

    def describe(self) -> str:
        return f"{self.action.value} {self.size:.0f}x {self.side.value} @ {self.price:.3f}"


@dataclass
class OrderSet:
    opportunity: Opportunity
    orders: list[Order] = field(default_factory=list)
    total_cost: float = 0.0
    expected_payout: float = 0.0

    @property
    def expected_profit(self) -> float:
        return self.expected_payout - self.total_cost

    def describe(self) -> str:
        lines = [f"OrderSet ({self.opportunity.arb_type.value}):"]
        for o in self.orders:
            lines.append(f"  {o.describe()}")
        lines.append(f"  Cost=${self.total_cost:.4f} Payout=${self.expected_payout:.4f} Profit=${self.expected_profit:.4f}")
        return "\n".join(lines)
