from __future__ import annotations

from typing import Protocol

from polyarb.models import Event, Market


class DataProvider(Protocol):
    def get_active_markets(self) -> list[Market]: ...
    def get_events(self) -> list[Event]: ...
