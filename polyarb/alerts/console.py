from __future__ import annotations

from polyarb.colors import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from polyarb.models import ArbType, Opportunity, OrderSet

ARB_COLOR = {
    ArbType.SINGLE_UNDERPRICE: GREEN,
    ArbType.SINGLE_OVERPRICE: RED,
    ArbType.MULTI_UNDERPRICE: GREEN,
    ArbType.MULTI_OVERPRICE: RED,
}


class ConsoleAlerter:
    def alert(self, index: int, opp: Opportunity, order_set: OrderSet) -> None:
        color = ARB_COLOR.get(opp.arb_type, CYAN)
        print(f"\n{color}{BOLD}[{index}] {opp.summary()}{RESET}")
        print(f"{CYAN}{order_set.describe()}{RESET}")

    def info(self, msg: str) -> None:
        print(f"{YELLOW}{msg}{RESET}")
