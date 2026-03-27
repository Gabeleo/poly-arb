"""Daemon scan engine: concurrent polling + detection via asyncio."""

from __future__ import annotations

import asyncio
import logging
import time

from polyarb.daemon.state import State
from polyarb.data.base import group_events
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.matching.encoder_client import EncoderClient
from polyarb.matching.matcher import MatchedPair, find_matches

logger = logging.getLogger(__name__)


async def _verify_candidates(
    candidates: list[MatchedPair],
    encoder_client: EncoderClient,
    final_threshold: float,
) -> list[MatchedPair]:
    """Score candidates via cross-encoder; fall back to token scores on failure."""
    if not candidates:
        return []

    pairs = [
        (c.poly_market.question, c.kalshi_market.question) for c in candidates
    ]
    scores = await encoder_client.score_pairs(pairs)

    if scores is not None:
        matches = [
            MatchedPair(c.poly_market, c.kalshi_market, score)
            for c, score in zip(candidates, scores)
            if score >= final_threshold
        ]
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    # Encoder failed — fall back to token-only scores
    logger.warning("Encoder unavailable, using token scores")
    return [c for c in candidates if c.confidence >= final_threshold]


async def run_scan_once(
    state: State, poly, kalshi, approval_manager=None,
    encoder_client: EncoderClient | None = None,
) -> None:
    """Fetch from both providers, detect matches and opportunities, update state."""
    # Concurrent fetch from both platforms
    poly_markets, kalshi_markets = await asyncio.gather(
        poly.get_active_markets(),
        kalshi.get_active_markets(),
    )

    cfg = state.config

    # Phase 1: cheap token filter (recall-optimised)
    candidate_threshold = cfg.match_candidate_threshold if encoder_client else cfg.match_final_threshold
    candidates = await asyncio.to_thread(
        find_matches, poly_markets, kalshi_markets, candidate_threshold,
    )

    # Phase 2: cross-encoder verification (precision gate)
    if encoder_client is not None:
        matches = await _verify_candidates(candidates, encoder_client, cfg.match_final_threshold)
    else:
        matches = candidates

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
    encoder_client: EncoderClient | None = None,
) -> None:
    """Continuous scan loop; catches exceptions to stay alive."""
    last_digest = time.monotonic()
    while True:
        try:
            await run_scan_once(state, poly, kalshi, approval_manager, encoder_client)

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
