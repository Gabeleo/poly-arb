"""Live Polymarket data provider using the Gamma API (no auth required for reads)."""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None  # use system defaults

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token

GAMMA_API = "https://gamma-api.polymarket.com"
_DEFAULT_SPREAD = 0.02


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

    raw_bid = raw.get("bestBid")
    raw_ask = raw.get("bestAsk")
    best_bid = float(raw_bid) if raw_bid else round(max(0.001, yes_mid - _DEFAULT_SPREAD / 2), 4)
    best_ask = float(raw_ask) if raw_ask else round(min(0.999, yes_mid + _DEFAULT_SPREAD / 2), 4)

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

    def _fetch_json(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{GAMMA_API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "polyarb/0.1",
        })
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read())

    def get_active_markets(self) -> list[Market]:
        data = self._fetch_json("/markets", {
            "limit": str(self._limit),
            "order": "volumeNum",
            "ascending": "false",
            "active": "true",
            "closed": "false",
        })
        raw_list = data if isinstance(data, list) else [data]
        markets = []
        for raw in raw_list:
            m = _parse_market(raw)
            if m is not None:
                markets.append(m)
        markets.sort(key=lambda m: m.volume)
        return markets

    def get_events(self) -> list[Event]:
        return group_events(self.get_active_markets())

    def get_expiring_soon(self, within_days: int = 7) -> list[Market]:
        markets = self.get_active_markets()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=within_days)
        return [
            m for m in markets
            if m.end_date is not None and now < m.end_date <= cutoff
        ]
