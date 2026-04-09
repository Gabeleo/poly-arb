"""Daemon state container with dedup, WS broadcast, and status reporting."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Opportunity

logger = logging.getLogger(__name__)


@dataclass
class State:
    config: Config
    matches: list[MatchedPair] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    ws_clients: set = field(default_factory=set)
    scan_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_scan_at: datetime | None = None
    last_scan_error: str | None = None
    biencoder_enabled: bool = False
    _seen_matches: dict[str, float] = field(default_factory=dict)
    _seen_opps: dict[str, float] = field(default_factory=dict)

    def _prune(self, seen: dict[str, float]) -> dict[str, float]:
        """Remove entries older than ``dedup_window`` seconds."""
        cutoff = time.monotonic() - self.config.dedup_window
        return {k: ts for k, ts in seen.items() if ts > cutoff}

    def update_matches(self, matches: list[MatchedPair]) -> list[MatchedPair]:
        """Replace match list, increment scan_count, return only NEW matches."""
        self.matches = matches
        self.scan_count += 1
        self.last_scan_at = datetime.now(timezone.utc)

        self._seen_matches = self._prune(self._seen_matches)

        now = time.monotonic()
        new: list[MatchedPair] = []
        for m in matches:
            key = f"{m.poly_market.condition_id}:{m.kalshi_market.condition_id}"
            if key not in self._seen_matches:
                self._seen_matches[key] = now
                new.append(m)
        return new

    def update_opportunities(self, opps: list[Opportunity]) -> list[Opportunity]:
        """Replace opportunity list, return only NEW opportunities."""
        self.opportunities = opps

        self._seen_opps = self._prune(self._seen_opps)

        now = time.monotonic()
        new: list[Opportunity] = []
        for o in opps:
            if o.key not in self._seen_opps:
                self._seen_opps[o.key] = now
                new.append(o)
        return new

    async def broadcast(self, message: dict) -> None:
        """Send message dict to all WS clients; remove dead connections."""
        dead: set = set()
        for ws in self.ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                logger.debug("Removing dead WS client: %s", ws)
                dead.add(ws)
        if dead:
            logger.info("Pruned %d dead WebSocket client(s)", len(dead))
        self.ws_clients -= dead

    def status_dict(self) -> dict:
        """Return current status as a JSON-serializable dict."""
        now = datetime.now(timezone.utc)
        uptime = (now - self.started_at).total_seconds()
        return {
            "uptime_seconds": round(uptime, 1),
            "scan_count": self.scan_count,
            "connected_clients": len(self.ws_clients),
            "match_count": len(self.matches),
            "opportunity_count": len(self.opportunities),
            "biencoder_enabled": self.biencoder_enabled,
        }
