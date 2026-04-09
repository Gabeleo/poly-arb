"""Daemon scan engine: concurrent polling + detection via asyncio."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from polyarb.daemon.state import State
from polyarb.data.base import group_events
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.matching.encoder_client import EncoderClient
from polyarb.matching.matcher import MatchedPair, find_matches, generate_all_pairs
from polyarb.models import Market
from polyarb.observability import metrics
from polyarb.observability.context import new_scan_id

logger = logging.getLogger(__name__)


def _scan_timestamp() -> str:
    """UTC timestamp for the current scan cycle."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Timeouts and circuit breaker defaults
FETCH_TIMEOUT = 30.0  # seconds per provider fetch
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures before backoff
CIRCUIT_BREAKER_MAX_DELAY = 300.0  # 5-minute cap on backoff


class _CircuitBreaker:
    """Simple consecutive-failure counter with exponential backoff."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._failures = 0

    def record_success(self) -> None:
        if self._failures > 0:
            logger.info("Provider %s recovered after %d failures", self.name, self._failures)
        self._failures = 0
        metrics.circuit_breaker_state.labels(provider=self.name).set(0)

    def record_failure(self, exc: Exception) -> None:
        self._failures += 1
        logger.warning(
            "Provider %s failed (%d consecutive): %s",
            self.name, self._failures, exc,
        )
        metrics.fetch_errors.labels(provider=self.name).inc()
        if self.is_open:
            metrics.circuit_breaker_state.labels(provider=self.name).set(1)

    @property
    def is_open(self) -> bool:
        return self._failures >= CIRCUIT_BREAKER_THRESHOLD

    @property
    def backoff_delay(self) -> float:
        if not self.is_open:
            return 0.0
        delay = min(10.0 * (2 ** (self._failures - CIRCUIT_BREAKER_THRESHOLD)),
                    CIRCUIT_BREAKER_MAX_DELAY)
        return delay


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
    with metrics.encoder_duration.time():
        scores = await encoder_client.score_pairs(pairs)

    if scores is not None:
        # Keep best Kalshi match per Poly market (1:1 mapping)
        best: dict[str, MatchedPair] = {}
        for c, score in zip(candidates, scores):
            if score < final_threshold:
                continue
            key = c.poly_market.condition_id
            if key not in best or score > best[key].confidence:
                best[key] = MatchedPair(c.poly_market, c.kalshi_market, score)

        matches = sorted(best.values(), key=lambda m: m.confidence, reverse=True)
        return matches

    # Encoder failed — fall back to token-only matches
    logger.warning("Encoder unavailable, falling back to token matcher")
    metrics.encoder_errors.inc()
    return [c for c in candidates if c.confidence >= final_threshold]


# ── Scan sub-steps ────────────────────────────────────────────────


async def _fetch_markets(
    poly,
    kalshi,
    poly_cb: _CircuitBreaker,
    kalshi_cb: _CircuitBreaker,
) -> tuple[list[Market], list[Market]]:
    """Fetch markets from both providers with per-provider timeout and circuit breaker."""
    poly_markets: list[Market] = []
    kalshi_markets: list[Market] = []

    async def _fetch_poly():
        nonlocal poly_markets
        if poly_cb.is_open:
            logger.info("Poly provider circuit open, backing off %.0fs", poly_cb.backoff_delay)
            return
        try:
            with metrics.fetch_duration.labels(provider="poly").time():
                poly_markets = await asyncio.wait_for(
                    poly.get_active_markets(), timeout=FETCH_TIMEOUT,
                )
            metrics.markets_fetched.labels(provider="poly").set(len(poly_markets))
            poly_cb.record_success()
        except (asyncio.TimeoutError, Exception) as exc:
            poly_cb.record_failure(exc)

    async def _fetch_kalshi():
        nonlocal kalshi_markets
        if kalshi_cb.is_open:
            logger.info("Kalshi provider circuit open, backing off %.0fs", kalshi_cb.backoff_delay)
            return
        try:
            with metrics.fetch_duration.labels(provider="kalshi").time():
                kalshi_markets = await asyncio.wait_for(
                    kalshi.get_active_markets(), timeout=FETCH_TIMEOUT,
                )
            metrics.markets_fetched.labels(provider="kalshi").set(len(kalshi_markets))
            kalshi_cb.record_success()
        except (asyncio.TimeoutError, Exception) as exc:
            kalshi_cb.record_failure(exc)

    await asyncio.gather(_fetch_poly(), _fetch_kalshi())
    return poly_markets, kalshi_markets


async def _match_markets(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    encoder_client: EncoderClient | None,
    final_threshold: float,
    biencoder=None,
    candidate_threshold: float = 0.15,
) -> list[MatchedPair]:
    """Match markets across platforms using encoder or token-based fallback."""
    if not poly_markets or not kalshi_markets:
        return []

    if encoder_client is not None:
        candidates = await asyncio.to_thread(
            generate_all_pairs, poly_markets, kalshi_markets,
        )
        logger.info(
            "Generated %d candidate pairs (%d poly x %d kalshi)",
            len(candidates), len(poly_markets), len(kalshi_markets),
        )

        # Bi-encoder pre-filtering
        if biencoder is not None:
            candidates = await asyncio.to_thread(
                biencoder.filter_candidates,
                candidates,
                threshold=candidate_threshold,
            )
            logger.info("Bi-encoder filtered to %d candidates", len(candidates))

        return await _verify_candidates(candidates, encoder_client, final_threshold)

    # No encoder: use token-based matching only
    return await asyncio.to_thread(
        find_matches, poly_markets, kalshi_markets, final_threshold,
    )


async def _detect_opportunities(all_markets: list[Market], config):
    """Run single-platform and multi-market detection."""
    single_opps = await asyncio.to_thread(detect_single, all_markets, config)
    events = await asyncio.to_thread(group_events, all_markets)
    multi_opps = await asyncio.to_thread(detect_multi, events, config)
    return single_opps + multi_opps


async def _publish_results(
    state: State,
    matches: list[MatchedPair],
    all_opps,
    approval_manager,
    match_repo=None,
    scan_ts: str | None = None,
    scan_id: str | None = None,
) -> None:
    """Update state, broadcast to WS clients, notify approval manager, and record matches."""
    new_matches = state.update_matches(matches)
    new_opps = state.update_opportunities(all_opps)

    metrics.matches_found.set(len(matches))
    metrics.opportunities_found.set(len(all_opps))

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

    if approval_manager:
        await approval_manager.expire_stale()
        if new_matches:
            await approval_manager.on_new_matches(new_matches)

    # Record match snapshots to database
    if match_repo is not None and matches:
        match_dicts = []
        for m in matches:
            match_dicts.append({
                "poly_condition_id": m.poly_market.condition_id,
                "kalshi_ticker": m.kalshi_market.condition_id,
                "poly_question": m.poly_market.question,
                "kalshi_question": m.kalshi_market.question,
                "confidence": m.confidence,
                "poly_yes_bid": m.poly_market.yes_token.best_bid,
                "poly_yes_ask": m.poly_market.yes_token.best_ask,
                "poly_no_bid": m.poly_market.no_token.best_bid,
                "poly_no_ask": m.poly_market.no_token.best_ask,
                "kalshi_yes_bid": m.kalshi_market.yes_token.best_bid,
                "kalshi_yes_ask": m.kalshi_market.yes_token.best_ask,
                "kalshi_no_bid": m.kalshi_market.no_token.best_bid,
                "kalshi_no_ask": m.kalshi_market.no_token.best_ask,
            })
        try:
            await asyncio.to_thread(
                match_repo.insert_matches, scan_ts or "", scan_id or "", match_dicts,
            )
        except Exception:
            logger.exception("Failed to record match snapshots")


# ── Public API ────────────────────────────────────────────────────


async def run_scan_once(
    state: State, poly, kalshi, approval_manager=None,
    encoder_client: EncoderClient | None = None,
    poly_cb: _CircuitBreaker | None = None,
    kalshi_cb: _CircuitBreaker | None = None,
    biencoder=None,
    match_repo=None,
) -> None:
    """Fetch from both providers, detect matches and opportunities, update state."""
    poly_cb = poly_cb or _CircuitBreaker("poly")
    kalshi_cb = kalshi_cb or _CircuitBreaker("kalshi")

    sid = new_scan_id()
    scan_ts = _scan_timestamp()
    logger.info("Starting scan %s", sid)

    with metrics.scan_duration.time():
        try:
            poly_markets, kalshi_markets = await _fetch_markets(poly, kalshi, poly_cb, kalshi_cb)

            cfg = state.config
            matches = await _match_markets(
                poly_markets, kalshi_markets, encoder_client, cfg.match_final_threshold,
                biencoder=biencoder,
                candidate_threshold=cfg.match_candidate_threshold,
            )

            all_opps = await _detect_opportunities(poly_markets + kalshi_markets, cfg)

            await _publish_results(
                state, matches, all_opps, approval_manager,
                match_repo=match_repo, scan_ts=scan_ts, scan_id=sid,
            )
            metrics.scan_total.labels(status="success").inc()
        except Exception:
            metrics.scan_total.labels(status="error").inc()
            raise


async def run_scan_loop(
    state: State, poly, kalshi, approval_manager=None, telegram_bot=None,
    encoder_client: EncoderClient | None = None,
    stop_event: asyncio.Event | None = None,
    biencoder=None,
    match_repo=None,
) -> None:
    """Continuous scan loop with graceful shutdown support.

    If *stop_event* is set, the loop finishes the current scan then exits.
    """
    last_digest = time.monotonic()
    poly_cb = _CircuitBreaker("poly")
    kalshi_cb = _CircuitBreaker("kalshi")

    while True:
        # Check for shutdown request
        if stop_event and stop_event.is_set():
            logger.info("Shutdown requested, exiting scan loop")
            return

        try:
            await run_scan_once(
                state, poly, kalshi, approval_manager, encoder_client,
                poly_cb, kalshi_cb, biencoder=biencoder,
                match_repo=match_repo,
            )

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

            state.last_scan_error = None

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.last_scan_error = str(exc)
            logger.exception("Scan error (will retry)")

        # Sleep with support for early exit on shutdown
        delay = state.config.scan_interval
        # Add circuit breaker backoff if needed
        delay = max(delay, poly_cb.backoff_delay, kalshi_cb.backoff_delay)

        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                logger.info("Shutdown requested during sleep, exiting scan loop")
                return
            except asyncio.TimeoutError:
                pass  # Normal timeout — continue loop
        else:
            await asyncio.sleep(delay)
