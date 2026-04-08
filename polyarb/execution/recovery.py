"""Orphan detection and resolution for the execution journal.

On daemon startup, checks for legs left in 'sent' status from a previous
run and attempts to determine their actual state by querying exchanges.

Conservative: detects and reports, does NOT auto-unwind positions.
"""

from __future__ import annotations

import logging

from polyarb.execution.journal import ExecutionJournal

logger = logging.getLogger(__name__)


async def check_orphans(
    journal: ExecutionJournal,
    kalshi_client=None,
    poly_client=None,
) -> list[dict]:
    """Find legs with status='sent' and no result.

    Returns the list of orphaned leg dicts from the journal.
    """
    return journal.get_orphans()


async def resolve_orphan(
    journal: ExecutionJournal,
    orphan: dict,
    kalshi_client=None,
    poly_client=None,
) -> str:
    """Attempt to determine if an orphaned leg was filled or not.

    Returns action taken: 'confirmed_fill', 'confirmed_no_fill',
    'manual_review'.
    """
    row_id = orphan["id"]
    platform = orphan["platform"]
    ticker = orphan["ticker"]

    client = kalshi_client if platform == "kalshi" else poly_client

    if client is None:
        logger.warning(
            "No %s client available to resolve orphan %d (%s)",
            platform, row_id, ticker,
        )
        journal.mark_orphaned(row_id)
        return "manual_review"

    try:
        if platform == "kalshi":
            positions = await client.get_positions(ticker=ticker)
            if positions:
                logger.info(
                    "Orphan %d: found position for %s on Kalshi", row_id, ticker,
                )
                journal.record_result(row_id, None, "filled")
                return "confirmed_fill"
            else:
                logger.info(
                    "Orphan %d: no position for %s on Kalshi", row_id, ticker,
                )
                journal.record_result(row_id, None, "failed", error="no position found")
                return "confirmed_no_fill"
        else:
            # Polymarket — SDK doesn't have a positions endpoint;
            # mark for manual review
            journal.mark_orphaned(row_id)
            return "manual_review"

    except Exception as exc:
        logger.error("Failed to resolve orphan %d: %s", row_id, exc)
        journal.mark_orphaned(row_id)
        return "manual_review"
