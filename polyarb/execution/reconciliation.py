"""Periodic position reconciliation against exchanges.

Compares internal position state (``positions`` table) against exchange
APIs and flags discrepancies as risk events.  Conservative: detects and
reports — does NOT auto-correct financial positions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

logger = logging.getLogger(__name__)


class PositionQuerier(Protocol):
    """Read-only position access for reconciliation."""

    def get_open_positions(self) -> list[dict]: ...


class RiskEventWriter(Protocol):
    """Records risk events for discrepancies."""

    def record_risk_event(
        self,
        event_type: str,
        severity: str,
        details: str,
        execution_id: str | None = None,
    ) -> None: ...


class ExchangePositionClient(Protocol):
    """Queries positions from an exchange."""

    async def get_positions(self, ticker: str = "") -> list[dict]: ...


@dataclass(frozen=True)
class Discrepancy:
    """A mismatch between internal and exchange position state."""

    platform: str
    ticker: str
    side: str
    internal_qty: float
    exchange_qty: float
    discrepancy_type: str  # "missing_on_exchange", "missing_internal", "quantity_mismatch"

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "ticker": self.ticker,
            "side": self.side,
            "internal_qty": self.internal_qty,
            "exchange_qty": self.exchange_qty,
            "type": self.discrepancy_type,
        }


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""

    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    positions_checked: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return len(self.discrepancies) == 0


async def reconcile(
    position_store: PositionQuerier,
    kalshi_client: ExchangePositionClient | None = None,
    risk_recorder: RiskEventWriter | None = None,
) -> ReconciliationResult:
    """Compare internal positions against exchange state.

    Only reconciles Kalshi positions (Polymarket SDK lacks a positions
    endpoint).  Discrepancies are logged and optionally written to
    ``risk_events``.
    """
    result = ReconciliationResult()
    open_positions = position_store.get_open_positions()
    result.positions_checked = len(open_positions)

    if kalshi_client is None:
        logger.warning("No Kalshi client available — skipping reconciliation")
        return result

    # Group internal positions by platform
    kalshi_positions = [p for p in open_positions if p["platform"] == "kalshi"]

    for pos in kalshi_positions:
        ticker = pos["ticker"]
        try:
            exchange_positions = await kalshi_client.get_positions(ticker=ticker)
        except Exception as exc:
            logger.error("Failed to query Kalshi positions for %s: %s", ticker, exc)
            continue

        if not exchange_positions:
            # We think we have a position, exchange says no
            disc = Discrepancy(
                platform="kalshi",
                ticker=ticker,
                side=pos["side"],
                internal_qty=pos["quantity"],
                exchange_qty=0.0,
                discrepancy_type="missing_on_exchange",
            )
            result.discrepancies.append(disc)
            logger.warning("Position discrepancy: %s", disc.to_dict())
            continue

        # Match by ticker — exchange may return multiple positions
        exchange_qty = sum(
            abs(float(ep.get("quantity", ep.get("position", 0)))) for ep in exchange_positions
        )
        internal_qty = pos["quantity"]

        if abs(exchange_qty - internal_qty) > 0.01:
            disc = Discrepancy(
                platform="kalshi",
                ticker=ticker,
                side=pos["side"],
                internal_qty=internal_qty,
                exchange_qty=exchange_qty,
                discrepancy_type="quantity_mismatch",
            )
            result.discrepancies.append(disc)
            logger.warning("Position discrepancy: %s", disc.to_dict())

    # Record discrepancies as risk events
    if risk_recorder is not None:
        for disc in result.discrepancies:
            risk_recorder.record_risk_event(
                event_type="position_discrepancy",
                severity="warning",
                details=json.dumps(disc.to_dict()),
            )

    if result.clean:
        logger.info("Reconciliation clean: %d positions checked", result.positions_checked)
    else:
        logger.warning(
            "Reconciliation found %d discrepancies across %d positions",
            len(result.discrepancies),
            result.positions_checked,
        )

    return result
