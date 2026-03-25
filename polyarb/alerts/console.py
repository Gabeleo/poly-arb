from __future__ import annotations

from polyarb.models import ArbType, Opportunity, OrderSet

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

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
