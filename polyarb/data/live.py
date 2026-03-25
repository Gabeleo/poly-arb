"""Live Polymarket data provider using the Gamma API (no auth required for reads)."""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None  # use system defaults

from polyarb.models import Event, Market, Side, Token

GAMMA_API = "https://gamma-api.polymarket.com"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _parse_market(raw: dict) -> Market | None:
    prices = raw.get("outcomePrices")
    clob_ids = raw.get("clobTokenIds")
    if not prices or not clob_ids:
        return None
    # These fields come as JSON-encoded strings from the API
    if isinstance(prices, str):
        prices = json.loads(prices)
    if isinstance(clob_ids, str):
        clob_ids = json.loads(clob_ids)
    if len(prices) < 2 or len(clob_ids) < 2:
        return None

    yes_mid = float(prices[0])
    no_mid = float(prices[1])

    best_bid = float(raw.get("bestBid") or yes_mid)
    best_ask = float(raw.get("bestAsk") or yes_mid)

    event_slug = ""
    events = raw.get("events") or []
    if events:
        event_slug = events[0].get("slug", "")

    return Market(
        condition_id=raw.get("conditionId", raw.get("id", "")),
        question=raw.get("question", ""),
        yes_token=Token(
            token_id=clob_ids[0],
            side=Side.YES,
            midpoint=yes_mid,
            best_bid=best_bid,
            best_ask=best_ask,
        ),
        no_token=Token(
            token_id=clob_ids[1],
            side=Side.NO,
            midpoint=no_mid,
            best_bid=round(1.0 - best_ask, 4),
            best_ask=round(1.0 - best_bid, 4),
        ),
        neg_risk=bool(raw.get("negRisk")),
        event_slug=event_slug,
        slug=raw.get("slug", ""),
        volume=float(raw.get("volumeNum") or raw.get("volume") or 0),
        end_date=_parse_dt(raw.get("endDate")),
    )


class LiveDataProvider:
    """Fetches markets from the Polymarket Gamma API."""

    def __init__(self, limit: int = 100) -> None:
        self._limit = limit

    def _fetch_json(self, path: str, params: dict | None = None) -> list[dict]:
        url = f"{GAMMA_API}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "polyarb/0.1",
        })
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read())

    def get_active_markets(self) -> list[Market]:
        raw_list = self._fetch_json("/markets", {
            "limit": str(self._limit),
            "order": "volumeNum",
            "ascending": "false",
            "active": "true",
            "closed": "false",
        })
        markets = []
        for raw in raw_list:
            m = _parse_market(raw)
            if m is not None:
                markets.append(m)
        # Return in increasing volume order as requested
        markets.sort(key=lambda m: m.volume)
        return markets

    def get_events(self) -> list[Event]:
        markets = self.get_active_markets()
        neg_risk = [m for m in markets if m.neg_risk]
        events_map: dict[str, list[Market]] = {}
        for m in neg_risk:
            events_map.setdefault(m.event_slug, []).append(m)
        return [
            Event(slug=slug, title=f"Event: {slug}", markets=tuple(mlist))
            for slug, mlist in events_map.items()
        ]

    def get_expiring_soon(self, within_days: int = 7) -> list[Market]:
        markets = self.get_active_markets()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=within_days)
        return [
            m for m in markets
            if m.end_date is not None and now < m.end_date <= cutoff
        ]
