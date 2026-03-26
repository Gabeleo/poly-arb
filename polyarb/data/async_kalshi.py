"""Async Kalshi data provider using httpx and the Trading API v2 (no auth required for reads)."""

from __future__ import annotations

from datetime import datetime

import httpx

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"


def _parse_price(val: str | None) -> float | None:
    """Parse a Kalshi *_dollars field (e.g. '0.42') to float.

    Returns None for missing, zero, negative, or >1.0 values.
    Kalshi prices are probabilities in [0.01, 0.99].
    """
    if not val:
        return None
    try:
        p = float(val)
        return p if 0 < p < 1.0 else None
    except (ValueError, TypeError):
        return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_market(
    raw: dict, event_title: str = "", neg_risk: bool = False
) -> Market | None:
    """Parse a Kalshi market JSON object into our Market model.

    Returns None for non-binary, inactive, or unpriceable markets.
    """
    if raw.get("market_type") != "binary":
        return None

    if raw.get("status") != "active":
        return None

    ticker = raw.get("ticker", "")
    if not ticker:
        return None

    event_ticker = raw.get("event_ticker", "")

    # ── Prices ──────────────────────────────────────────────
    yes_bid = _parse_price(raw.get("yes_bid_dollars"))
    yes_ask = _parse_price(raw.get("yes_ask_dollars"))
    no_bid = _parse_price(raw.get("no_bid_dollars"))
    no_ask = _parse_price(raw.get("no_ask_dollars"))
    last_price = _parse_price(raw.get("last_price_dollars"))

    # YES midpoint — prefer spread centre, fall back to last trade
    if yes_bid is not None and yes_ask is not None:
        yes_mid = round((yes_bid + yes_ask) / 2, 4)
    elif last_price is not None:
        yes_mid = last_price
    elif yes_bid is not None:
        yes_mid = yes_bid
    elif yes_ask is not None:
        yes_mid = yes_ask
    else:
        return None  # no usable price data

    # NO midpoint
    if no_bid is not None and no_ask is not None:
        no_mid = round((no_bid + no_ask) / 2, 4)
    else:
        no_mid = round(1.0 - yes_mid, 4)

    # Default bid/ask when the book is empty on one side
    yes_bid = yes_bid or round(max(0.01, yes_mid - 0.01), 4)
    yes_ask = yes_ask or round(min(0.99, yes_mid + 0.01), 4)
    no_bid = no_bid or round(max(0.01, no_mid - 0.01), 4)
    no_ask = no_ask or round(min(0.99, no_mid + 0.01), 4)

    # ── Question text ───────────────────────────────────────
    yes_sub = raw.get("yes_sub_title", "")
    if event_title:
        # Only append subtitle when it carries real info (not just "Yes"/"No")
        if yes_sub and yes_sub.lower() not in ("yes", "no", ""):
            question = f"{event_title} — {yes_sub}"
        else:
            question = event_title
    else:
        question = yes_sub or ticker

    # ── Volume ──────────────────────────────────────────────
    vol_str = raw.get("volume_24h_fp") or raw.get("volume_fp") or "0"
    try:
        volume = float(vol_str)
    except (ValueError, TypeError):
        volume = 0.0

    return Market(
        condition_id=ticker,
        question=question,
        yes_token=Token(
            token_id=f"{ticker}:yes",
            side=Side.YES,
            midpoint=yes_mid,
            best_bid=yes_bid,
            best_ask=yes_ask,
        ),
        no_token=Token(
            token_id=f"{ticker}:no",
            side=Side.NO,
            midpoint=no_mid,
            best_bid=no_bid,
            best_ask=no_ask,
        ),
        neg_risk=neg_risk,
        event_slug=event_ticker,
        slug=ticker,
        volume=volume,
        end_date=_parse_dt(raw.get("close_time")),
        platform="kalshi",
    )


class AsyncKalshiDataProvider:
    """Fetches markets from the Kalshi Trading API v2 using httpx (no auth for reads)."""

    def __init__(
        self,
        limit: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._limit = limit
        self._event_titles: dict[str, str] = {}
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=KALSHI_API,
                timeout=15.0,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "polyarb/0.2",
                },
            )
            self._owns_client = True

    async def _fetch_json(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_active_markets(self) -> list[Market]:
        """Fetch active binary markets via the events endpoint.

        Uses /events?with_nested_markets=true so we get event titles
        and can determine mutually_exclusive (-> neg_risk) up front.
        """
        data = await self._fetch_json(
            "/events",
            {
                "limit": str(min(self._limit, 200)),
                "status": "open",
                "with_nested_markets": "true",
            },
        )

        markets: list[Market] = []
        for evt in data.get("events", []):
            event_ticker = evt.get("event_ticker", "")
            event_title = evt.get("title", event_ticker)
            mutually_exclusive = evt.get("mutually_exclusive", False)
            raw_markets = evt.get("markets", [])

            self._event_titles[event_ticker] = event_title
            is_neg_risk = mutually_exclusive and len(raw_markets) > 1

            for raw_mkt in raw_markets:
                m = _parse_market(raw_mkt, event_title=event_title, neg_risk=is_neg_risk)
                if m is not None:
                    markets.append(m)

        markets.sort(key=lambda m: m.volume)
        return markets

    async def get_events(self) -> list[Event]:
        """Return mutually-exclusive multi-market events for arb detection."""
        return group_events(await self.get_active_markets(), titles=self._event_titles)

    async def search_markets(self, query: str, limit: int = 5) -> list[Market]:
        """Client-side substring search (Kalshi has no server-side text search)."""
        all_markets = await self.get_active_markets()
        q = query.lower()
        matches = [m for m in all_markets if q in m.question.lower()]
        matches.sort(key=lambda m: m.volume, reverse=True)
        return matches[:limit]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
