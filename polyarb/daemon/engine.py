"""Daemon scan engine: concurrent polling + detection via asyncio."""

from __future__ import annotations

import asyncio
import logging
import time

from polyarb.daemon.state import State
from polyarb.data.base import group_events
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.matching.matcher import find_matches

logger = logging.getLogger(__name__)


async def run_scan_once(state: State, poly, kalshi, approval_manager=None) -> None:
    """Fetch from both providers, detect matches and opportunities, update state."""
    # Concurrent fetch from both platforms
    poly_markets, kalshi_markets = await asyncio.gather(
        poly.get_active_markets(),
        kalshi.get_active_markets(),
    )

    # CPU-bound detection offloaded to threads
    matches = await asyncio.to_thread(find_matches, poly_markets, kalshi_markets)

    all_markets = poly_markets + kalshi_markets
    single_opps = await asyncio.to_thread(detect_single, all_markets, state.config)

    events = await asyncio.to_thread(group_events, all_markets)
    multi_opps = await asyncio.to_thread(detect_multi, events, state.config)

    all_opps = single_opps + multi_opps

    # Update state (returns only new items)
    new_matches = state.update_matches(matches)
    new_opps = state.update_opportunities(all_opps)

    # Broadcast new items to WS clients
    if new_matches:
        await state.broadcast({
            "type": "new_matches",
            "data": [m.to_dict() for m in new_matches],
        })

    if new_opps:
        await state.broadcast({
            "type": "new_opportunities",
            "data": [o.to_dict() for o in new_opps],
        })

    # Approval manager hook (Telegram notifications)
    if approval_manager:
        await approval_manager.expire_stale()
        if new_matches:
            await approval_manager.on_new_matches(new_matches)


async def run_scan_loop(
    state: State, poly, kalshi, approval_manager=None, telegram_bot=None,
) -> None:
    """Continuous scan loop; catches exceptions to stay alive."""
    last_digest = time.monotonic()
    while True:
        try:
            await run_scan_once(state, poly, kalshi, approval_manager)

            # Hourly digest of top single-platform opps
            if telegram_bot and state.opportunities:
                now = time.monotonic()
                if now - last_digest >= state.config.digest_interval:
                    try:
                        await telegram_bot.send_digest(state.opportunities)
                        last_digest = now
                        logger.info("Digest sent (%d opps)", len(state.opportunities))
                    except Exception:
                        logger.exception("Failed to send digest")

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scan error (will retry)")
        await asyncio.sleep(state.config.scan_interval)
