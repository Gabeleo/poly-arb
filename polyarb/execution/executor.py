from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from polyarb.models import OrderSet


class Executor(Protocol):
    def execute(self, order_set: OrderSet) -> bool: ...


@dataclass
class MockExecutor:
    trades: list[OrderSet] = field(default_factory=list)
    total_profit: float = 0.0

    def execute(self, order_set: OrderSet) -> bool:
        self.trades.append(order_set)
        self.total_profit += order_set.expected_profit
        print(f"\033[92m  ✓ Paper trade executed. "
              f"Profit=${order_set.expected_profit:.4f} "
              f"| Cumulative=${self.total_profit:.4f}\033[0m")
        return True


class LiveExecutor:
    """Stub for future Polymarket CLOB integration."""

    def execute(self, order_set: OrderSet) -> bool:
        raise NotImplementedError("Live execution not yet implemented")
