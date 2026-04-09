"""Async Polymarket data provider using httpx and the Gamma API (no auth required for reads)."""

from __future__ import annotations

import json
from datetime import datetime

import httpx

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token

GAMMA_API = "https://gamma-api.polymarket.com"
_DEFAULT_SPREAD = 0.02


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


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
        volume_24h=float(raw.get("volume24hr") or 0),
        end_date=_parse_dt(raw.get("endDate")),
    )


class AsyncLiveDataProvider:
    """Fetches markets from the Polymarket Gamma API using httpx."""

    def __init__(
        self,
        limit: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._limit = limit
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=GAMMA_API,
                timeout=15.0,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "polyarb/0.2",
                },
            )
            self._owns_client = True

    async def _fetch_json(self, path: str, params: dict | None = None) -> list | dict:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_active_markets(self) -> list[Market]:
        data = await self._fetch_json(
            "/markets",
            {
                "limit": str(self._limit),
                "order": "volumeNum",
                "ascending": "false",
                "active": "true",
                "closed": "false",
            },
        )
        raw_list = data if isinstance(data, list) else [data]
        markets = []
        for raw in raw_list:
            m = _parse_market(raw)
            if m is not None:
                markets.append(m)
        markets.sort(key=lambda m: m.volume)
        return markets

    async def get_events(self) -> list[Event]:
        return group_events(await self.get_active_markets())

    async def search_markets(self, query: str, limit: int = 5) -> list[Market]:
        """Search markets by name, sorted by 24h volume descending."""
        fetch_limit = min(max(limit * 10, 100), 500)
        data = await self._fetch_json(
            "/markets",
            {
                "limit": str(fetch_limit),
                "order": "volumeNum",
                "ascending": "false",
                "active": "true",
                "closed": "false",
            },
        )
        raw_list = data if isinstance(data, list) else [data]
        markets = [m for raw in raw_list if (m := _parse_market(raw)) is not None]
        q = query.lower()
        markets = [m for m in markets if q in m.question.lower()]
        return markets[:limit]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
