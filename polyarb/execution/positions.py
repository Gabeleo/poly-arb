"""Real-time position tracking across platforms.

On successful execution, opens positions for both legs.
On settlement/expiry, closes positions and records realized P&L.
Positions are persisted in the database and queried — never held in memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class PositionStore(Protocol):
    """Minimal interface for position persistence."""

    def open_position(
        self,
        platform: str,
        ticker: str,
        side: str,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> int: ...

    def close_position(
        self,
        position_id: int,
        realized_pnl: float | None = None,
    ) -> None: ...

    def update_position(
        self,
        position_id: int,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> None: ...

    def get_open_positions(self) -> list[dict]: ...

    def get_position_by_market(self, platform: str, ticker: str, side: str) -> dict | None: ...


@dataclass
class PositionTracker:
    """Manages position lifecycle: open on fill, close on settlement.

    Uses a ``PositionStore`` (typically ``SqlitePositionRepository``)
    for persistence.  Does not hold positions in memory — all queries
    go through the store.
    """

    store: PositionStore

    def record_fill(
        self,
        platform: str,
        ticker: str,
        side: str,
        quantity: float,
        price: float,
        execution_id: str | None = None,
    ) -> int:
        """Record a filled order as an open position.

        If an open position already exists for the same market/side,
        updates the average price and adds to quantity.  Returns the
        position row ID.
        """
        existing = self.store.get_position_by_market(platform, ticker, side)

        if existing is not None:
            # Average in — weighted mean of old and new
            old_qty = existing["quantity"]
            old_price = existing["avg_price"]
            new_qty = old_qty + quantity
            new_price = (old_price * old_qty + price * quantity) / new_qty

            self.store.update_position(
                existing["id"],
                quantity=new_qty,
                avg_price=new_price,
                execution_id=execution_id,
            )
            logger.info(
                "Averaged into position %s/%s/%s: qty %.2f @ %.4f -> qty %.2f @ %.4f",
                platform,
                ticker,
                side,
                old_qty,
                old_price,
                new_qty,
                new_price,
            )
            return existing["id"]

        pos_id = self.store.open_position(
            platform=platform,
            ticker=ticker,
            side=side,
            quantity=quantity,
            avg_price=price,
            execution_id=execution_id,
        )
        logger.info(
            "Opened position %s/%s/%s: qty %.2f @ %.4f (exec=%s)",
            platform,
            ticker,
            side,
            quantity,
            price,
            execution_id,
        )
        return pos_id

    def close(
        self,
        platform: str,
        ticker: str,
        side: str,
        settlement_price: float | None = None,
    ) -> float | None:
        """Close an open position, computing realized P&L if price given.

        Returns realized P&L or None if no open position found.
        """
        existing = self.store.get_position_by_market(platform, ticker, side)
        if existing is None:
            logger.warning("No open position to close for %s/%s/%s", platform, ticker, side)
            return None

        realized_pnl: float | None = None
        if settlement_price is not None:
            realized_pnl = (settlement_price - existing["avg_price"]) * existing["quantity"]

        self.store.close_position(existing["id"], realized_pnl=realized_pnl)
        logger.info(
            "Closed position %s/%s/%s: realized_pnl=%s",
            platform,
            ticker,
            side,
            f"{realized_pnl:.4f}" if realized_pnl is not None else "N/A",
        )
        return realized_pnl

    def get_open(self) -> list[dict]:
        """Return all open positions."""
        return self.store.get_open_positions()
