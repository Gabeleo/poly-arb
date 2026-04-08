"""Cross-platform executor: places both legs concurrently and unwinds on partial failure."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    kalshi_order: dict | None = None
    poly_order: dict | None = None
    error: str = ""
    unwound: bool = False

    def describe(self) -> str:
        if self.success:
            k_id = (self.kalshi_order or {}).get("order_id", "?")
            p_id = (self.poly_order or {}).get("orderID", "?")
            return f"Both legs filled — Kalshi: {k_id}, Poly: {p_id}"
        if self.unwound:
            return f"Partial fill unwound — {self.error}"
        return f"Execution failed — {self.error}"


@dataclass
class CrossExecutor:
    """Orchestrates dual-leg arb execution across Kalshi and Polymarket."""

    kalshi: object  # AsyncKalshiClient
    poly: object  # AsyncPolymarketClient
    journal: object | None = None  # Optional ExecutionJournal

    async def execute(self, match: MatchedPair, config: Config) -> ExecutionResult:
        """Place both legs concurrently. Unwind on partial failure."""
        params = match.execution_params
        size = int(config.order_size)

        k = params["kalshi"]
        p = params["poly"]

        match_key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"

        # Journal: record execution and legs
        execution_id = ""
        k_row_id = None
        p_row_id = None
        if self.journal is not None:
            execution_id = uuid.uuid4().hex[:12]
            self.journal.record_execution(execution_id, match_key, 2)
            k_row_id = self.journal.record_attempt(
                execution_id, 0, "kalshi", k["ticker"], k["side"], "buy",
                k["price"], float(size),
            )
            self.journal.mark_sent(k_row_id)
            p_row_id = self.journal.record_attempt(
                execution_id, 1, "polymarket", p["token_id"], p["side"], "buy",
                p["price"], float(size),
            )
            self.journal.mark_sent(p_row_id)

        kalshi_coro = self.kalshi.create_order(
            ticker=k["ticker"],
            side=k["side"],
            action="buy",
            price_cents=max(1, min(99, round(k["price"] * 100))),
            count=size,
        )
        poly_coro = self.poly.create_order(
            token_id=p["token_id"],
            side="BUY",
            price=p["price"],
            size=float(size),
            order_type="FOK",
        )

        kalshi_result, poly_result = await asyncio.gather(
            kalshi_coro, poly_coro, return_exceptions=True,
        )

        kalshi_ok = not isinstance(kalshi_result, BaseException)
        poly_ok = not isinstance(poly_result, BaseException)

        # Journal: record results
        if self.journal is not None:
            if kalshi_ok and k_row_id is not None:
                oid = kalshi_result.get("order_id", "") if isinstance(kalshi_result, dict) else None
                self.journal.record_result(k_row_id, oid, "filled")
            elif not kalshi_ok and k_row_id is not None:
                self.journal.record_result(k_row_id, None, "failed", error=str(kalshi_result))
            if poly_ok and p_row_id is not None:
                oid = poly_result.get("orderID", "") if isinstance(poly_result, dict) else None
                self.journal.record_result(p_row_id, oid, "filled")
            elif not poly_ok and p_row_id is not None:
                self.journal.record_result(p_row_id, None, "failed", error=str(poly_result))

        # Both succeed
        if kalshi_ok and poly_ok:
            logger.info("Both legs filled for %s", match.kalshi_market.condition_id)
            if self.journal is not None and execution_id:
                self.journal.record_completion(execution_id, True, params["profit"])
            return ExecutionResult(
                success=True,
                kalshi_order=kalshi_result,
                poly_order=poly_result,
            )

        # Both fail
        if not kalshi_ok and not poly_ok:
            if self.journal is not None and execution_id:
                self.journal.record_completion(execution_id, False)
            return ExecutionResult(
                success=False,
                error=f"Both legs failed — Kalshi: {kalshi_result}, Poly: {poly_result}",
            )

        # Partial failure — attempt to unwind the successful leg
        if kalshi_ok and not poly_ok:
            unwound = await self._try_cancel_kalshi(kalshi_result)
            if self.journal is not None and k_row_id is not None and unwound:
                self.journal.record_cancel(k_row_id, "cancelled")
            if self.journal is not None and execution_id:
                self.journal.record_completion(execution_id, False)
            return ExecutionResult(
                success=False,
                kalshi_order=kalshi_result,
                error=f"Poly leg failed ({poly_result}); Kalshi {'unwound' if unwound else 'UNWIND FAILED'}",
                unwound=unwound,
            )

        # poly_ok and not kalshi_ok
        unwound = await self._try_cancel_poly(poly_result)
        if self.journal is not None and p_row_id is not None and unwound:
            self.journal.record_cancel(p_row_id, "cancelled")
        if self.journal is not None and execution_id:
            self.journal.record_completion(execution_id, False)
        return ExecutionResult(
            success=False,
            poly_order=poly_result,
            error=f"Kalshi leg failed ({kalshi_result}); Poly {'unwound' if unwound else 'UNWIND FAILED'}",
            unwound=unwound,
        )

    async def _try_cancel_kalshi(self, order: dict) -> bool:
        """Attempt to cancel a Kalshi order. Returns True on success."""
        order_id = order.get("order_id")
        if not order_id:
            return False
        try:
            await self.kalshi.cancel_order(order_id)
            logger.info("Cancelled Kalshi order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel Kalshi order %s: %s", order_id, exc)
            return False

    async def _try_cancel_poly(self, order: dict) -> bool:
        """Attempt to cancel a Polymarket order. Returns True on success."""
        order_id = order.get("orderID")
        if not order_id:
            return False
        try:
            await self.poly.cancel_order(order_id)
            logger.info("Cancelled Poly order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel Poly order %s: %s", order_id, exc)
            return False
