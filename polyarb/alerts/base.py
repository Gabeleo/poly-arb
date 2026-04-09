from __future__ import annotations

from typing import Protocol

from polyarb.execution.orders import OrderSet
from polyarb.models import Opportunity


class Alerter(Protocol):
    def alert(self, index: int, opp: Opportunity, order_set: OrderSet) -> None: ...
    def info(self, msg: str) -> None: ...
