"""Daemon state container with dedup, WS broadcast, and status reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Opportunity


@dataclass
class State:
    config: Config
    matches: list[MatchedPair] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    ws_clients: set = field(default_factory=set)
    scan_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_scan_at: datetime | None = None
    _seen_matches: set[str] = field(default_factory=set)
    _seen_opps: set[str] = field(default_factory=set)

    def update_matches(self, matches: list[MatchedPair]) -> list[MatchedPair]:
        """Replace match list, increment scan_count, return only NEW matches."""
        self.matches = matches
        self.scan_count += 1
        self.last_scan_at = datetime.now(timezone.utc)

        new: list[MatchedPair] = []
        for m in matches:
            key = f"{m.poly_market.condition_id}:{m.kalshi_market.condition_id}"
            if key not in self._seen_matches:
                self._seen_matches.add(key)
                new.append(m)
        return new

    def update_opportunities(self, opps: list[Opportunity]) -> list[Opportunity]:
        """Replace opportunity list, return only NEW opportunities."""
        self.opportunities = opps

        new: list[Opportunity] = []
        for o in opps:
            if o.key not in self._seen_opps:
                self._seen_opps.add(o.key)
                new.append(o)
        return new

    async def broadcast(self, message: dict) -> None:
        """Send message dict to all WS clients; remove dead connections."""
        dead: set = set()
        for ws in self.ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
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
        }
