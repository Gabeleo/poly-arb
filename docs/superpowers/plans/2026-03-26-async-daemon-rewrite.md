# Async Daemon Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace synchronous urllib-based architecture with an async daemon + thin CLI client, completing roadmap steps 3 (drop stdlib constraint) and 4 (move to async).

**Architecture:** Async daemon polls Polymarket and Kalshi concurrently, runs detection/matching, exposes results via REST API + WS push channel. Thin CLI client connects to daemon over localhost. Old sync CLI preserved for `--mock` mode.

**Tech Stack:** httpx (async HTTP), websockets (WS client), uvicorn + starlette (ASGI server), pytest-asyncio (async tests)

**Spec:** `docs/superpowers/specs/2026-03-26-async-daemon-rewrite-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `polyarb/data/async_live.py` | Async Polymarket data provider (httpx) |
| `polyarb/data/async_kalshi.py` | Async Kalshi data provider (httpx) |
| `polyarb/execution/async_kalshi.py` | Async Kalshi HTTP client for order placement |
| `polyarb/daemon/__init__.py` | Package marker |
| `polyarb/daemon/state.py` | In-memory state container (matches, opps, WS clients, dedup) |
| `polyarb/daemon/engine.py` | Async scan loop (concurrent fetch, detect, dedup, broadcast) |
| `polyarb/daemon/server.py` | Starlette REST API + WS endpoint |
| `polyarb/daemon/__main__.py` | Daemon entry point (wires providers, state, engine, uvicorn) |
| `polyarb/client/__init__.py` | Package marker |
| `polyarb/client/api.py` | Sync httpx wrapper for daemon REST API |
| `polyarb/client/ws_listener.py` | Background thread WS listener for push alerts |
| `polyarb/client/cli.py` | cmd.Cmd shell that talks to daemon |
| `polyarb/client/__main__.py` | Client entry point |
| `polyarb/tests/test_serialization.py` | Tests for to_dict() on all models |
| `polyarb/tests/test_async_providers.py` | Tests for async data providers (mocked httpx) |
| `polyarb/tests/test_daemon_state.py` | Tests for State dedup/update/broadcast |
| `polyarb/tests/test_daemon_engine.py` | Tests for scan loop logic |
| `polyarb/tests/test_server.py` | Tests for REST endpoints (Starlette TestClient) |
| `polyarb/tests/test_client_api.py` | Tests for DaemonClient (mocked httpx) |

### Modified files

| File | Change |
|------|--------|
| `pyproject.toml` | Add httpx, websockets, uvicorn, starlette, pytest-asyncio deps |
| `polyarb/models.py` | Add `to_dict()` to Token, Market, Event, Opportunity, Order, OrderSet |
| `polyarb/matching/matcher.py` | Add `to_dict()` to MatchedPair |
| `polyarb/data/base.py` | Add `AsyncDataProvider` protocol |
| `polyarb/__main__.py` | Route `--daemon` / `--mock` / default (client) |
| `Dockerfile` | Change entrypoint to daemon, expose 8080 |
| `compose.yaml` | Split into daemon + client services |

### Deleted files (step 5 only)

| File | Reason |
|------|--------|
| `polyarb/data/live.py` | Replaced by `async_live.py` |
| `polyarb/data/kalshi.py` | Replaced by `async_kalshi.py` |
| `polyarb/engine/scanner.py` | Replaced by daemon scan loop |
| `polyarb/cli.py` | Replaced by `client/cli.py` |

---

## Task 1: Async Data Layer

**Files:**
- Modify: `pyproject.toml`
- Modify: `polyarb/models.py`
- Modify: `polyarb/matching/matcher.py`
- Modify: `polyarb/data/base.py`
- Create: `polyarb/data/async_live.py`
- Create: `polyarb/data/async_kalshi.py`
- Create: `polyarb/execution/async_kalshi.py`
- Create: `polyarb/tests/test_serialization.py`
- Create: `polyarb/tests/test_async_providers.py`

### Step 1: Update dependencies

- [ ] **Update pyproject.toml**

Replace the current content with:

```toml
[project]
name = "polyarb"
version = "0.2.0"
description = "Polymarket arbitrage detection & semi-auto trading system"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "websockets>=12.0",
    "uvicorn>=0.30",
    "starlette>=0.37",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-asyncio>=0.24"]
trade = ["cryptography>=41.0"]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["polyarb/tests"]
asyncio_mode = "auto"
```

Note: `certifi` removed (httpx bundles its own CA certs). `pytest-asyncio` added. `asyncio_mode = "auto"` means async test functions are detected automatically without decorating each one.

- [ ] **Install updated deps**

Run: `pip install -e ".[dev,trade]"`
Expected: All packages install successfully, including httpx, websockets, uvicorn, starlette, pytest-asyncio.

- [ ] **Run existing tests to confirm nothing broke**

Run: `pytest -v`
Expected: All existing tests pass (test_single, test_multi, test_orders, test_matching, test_kalshi, test_kalshi_exec).

### Step 2: Add to_dict() serialization to models

- [ ] **Write serialization tests**

Create `polyarb/tests/test_serialization.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from polyarb.matching.matcher import MatchedPair
from polyarb.models import (
    Action,
    ArbType,
    Event,
    Market,
    Opportunity,
    Order,
    OrderSet,
    Side,
    Token,
)


def _make_token(side: Side = Side.YES, mid: float = 0.6) -> Token:
    return Token(
        token_id="tok_abc",
        side=side,
        midpoint=mid,
        best_bid=mid - 0.01,
        best_ask=mid + 0.01,
    )


def _make_market(question: str = "Will X happen?", platform: str = "polymarket") -> Market:
    return Market(
        condition_id="cond_123",
        question=question,
        yes_token=_make_token(Side.YES, 0.6),
        no_token=_make_token(Side.NO, 0.4),
        neg_risk=False,
        event_slug="event-slug",
        slug="market-slug",
        volume=50000.0,
        end_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
        platform=platform,
    )


def test_token_to_dict():
    t = _make_token()
    d = t.to_dict()
    assert d == {
        "token_id": "tok_abc",
        "side": "YES",
        "midpoint": 0.6,
        "best_bid": 0.59,
        "best_ask": 0.61,
    }


def test_market_to_dict():
    m = _make_market()
    d = m.to_dict()
    assert d["condition_id"] == "cond_123"
    assert d["question"] == "Will X happen?"
    assert d["platform"] == "polymarket"
    assert d["volume"] == 50000.0
    assert d["end_date"] == "2026-06-01T00:00:00+00:00"
    assert d["yes_token"]["side"] == "YES"
    assert d["no_token"]["side"] == "NO"


def test_market_to_dict_no_end_date():
    m = Market(
        condition_id="c1",
        question="Q?",
        yes_token=_make_token(Side.YES),
        no_token=_make_token(Side.NO),
    )
    d = m.to_dict()
    assert d["end_date"] is None


def test_event_to_dict():
    m1 = _make_market("Outcome A")
    m2 = _make_market("Outcome B")
    e = Event(slug="evt", title="My Event", markets=(m1, m2))
    d = e.to_dict()
    assert d["slug"] == "evt"
    assert d["title"] == "My Event"
    assert len(d["markets"]) == 2
    assert d["markets"][0]["question"] == "Outcome A"


def test_opportunity_to_dict():
    m = _make_market()
    opp = Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(m,),
        expected_profit_per_share=0.02,
    )
    d = opp.to_dict()
    assert d["arb_type"] == "SINGLE_UNDERPRICE"
    assert d["expected_profit_per_share"] == 0.02
    assert d["event"] is None
    assert len(d["markets"]) == 1


def test_order_to_dict():
    o = Order(token_id="tok1", side=Side.YES, action=Action.BUY, price=0.6, size=10.0)
    d = o.to_dict()
    assert d == {
        "token_id": "tok1",
        "side": "YES",
        "action": "BUY",
        "price": 0.6,
        "size": 10.0,
    }


def test_order_set_to_dict():
    m = _make_market()
    opp = Opportunity(arb_type=ArbType.SINGLE_UNDERPRICE, markets=(m,))
    o = Order(token_id="tok1", side=Side.YES, action=Action.BUY, price=0.6, size=10.0)
    os = OrderSet(opportunity=opp, orders=[o], total_cost=6.0, expected_payout=10.0)
    d = os.to_dict()
    assert d["total_cost"] == 6.0
    assert d["expected_payout"] == 10.0
    assert d["expected_profit"] == 4.0
    assert len(d["orders"]) == 1
    assert d["opportunity"]["arb_type"] == "SINGLE_UNDERPRICE"


def test_matched_pair_to_dict():
    pm = _make_market("Will BTC hit 100k?", platform="polymarket")
    km = _make_market("Bitcoin above 100k?", platform="kalshi")
    pair = MatchedPair(poly_market=pm, kalshi_market=km, confidence=0.85)
    d = pair.to_dict()
    assert d["confidence"] == 0.85
    assert d["poly_market"]["platform"] == "polymarket"
    assert d["kalshi_market"]["platform"] == "kalshi"
    assert "yes_spread" in d
    assert "profit_buy_kalshi_yes" in d
    assert "profit_buy_poly_yes" in d
    assert "best_arb" in d
    assert d["best_arb"]["profit"] == pair.best_arb[0]
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_serialization.py -v`
Expected: All tests FAIL with `AttributeError: ... has no attribute 'to_dict'`

- [ ] **Add to_dict() to models.py**

Add `to_dict()` method to each class in `polyarb/models.py`. Add these methods to the respective classes:

In `Token`:
```python
    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "side": self.side.value,
            "midpoint": self.midpoint,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
        }
```

In `Market`:
```python
    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "question": self.question,
            "yes_token": self.yes_token.to_dict(),
            "no_token": self.no_token.to_dict(),
            "neg_risk": self.neg_risk,
            "event_slug": self.event_slug,
            "slug": self.slug,
            "volume": self.volume,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "platform": self.platform,
        }
```

In `Event`:
```python
    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "markets": [m.to_dict() for m in self.markets],
        }
```

In `Opportunity`:
```python
    def to_dict(self) -> dict:
        return {
            "arb_type": self.arb_type.value,
            "markets": [m.to_dict() for m in self.markets],
            "event": self.event.to_dict() if self.event else None,
            "expected_profit_per_share": self.expected_profit_per_share,
            "key": self.key,
        }
```

In `Order`:
```python
    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "side": self.side.value,
            "action": self.action.value,
            "price": self.price,
            "size": self.size,
        }
```

In `OrderSet`:
```python
    def to_dict(self) -> dict:
        return {
            "opportunity": self.opportunity.to_dict(),
            "orders": [o.to_dict() for o in self.orders],
            "total_cost": self.total_cost,
            "expected_payout": self.expected_payout,
            "expected_profit": self.expected_profit,
        }
```

- [ ] **Add to_dict() to MatchedPair in matcher.py**

Add to the `MatchedPair` class in `polyarb/matching/matcher.py`:

```python
    def to_dict(self) -> dict:
        profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = self.best_arb
        return {
            "poly_market": self.poly_market.to_dict(),
            "kalshi_market": self.kalshi_market.to_dict(),
            "confidence": self.confidence,
            "yes_spread": self.yes_spread,
            "profit_buy_kalshi_yes": self.profit_buy_kalshi_yes,
            "profit_buy_poly_yes": self.profit_buy_poly_yes,
            "best_arb": {
                "profit": profit,
                "kalshi_side": kalshi_side,
                "kalshi_desc": kalshi_desc,
                "poly_desc": poly_desc,
                "kalshi_price": kalshi_price,
            },
        }
```

- [ ] **Run serialization tests**

Run: `pytest polyarb/tests/test_serialization.py -v`
Expected: All 8 tests PASS.

### Step 3: Add AsyncDataProvider protocol

- [ ] **Add protocol to base.py**

Add to `polyarb/data/base.py` after the existing `DataProvider` class:

```python
class AsyncDataProvider(Protocol):
    async def get_active_markets(self) -> list[Market]: ...
    async def get_events(self) -> list[Event]: ...
    async def search_markets(self, query: str, limit: int = 5) -> list[Market]: ...
    async def close(self) -> None: ...
```

### Step 4: Write async Polymarket provider

- [ ] **Write tests for AsyncLiveDataProvider**

Create `polyarb/tests/test_async_providers.py`:

```python
from __future__ import annotations

import json

import httpx
import pytest

from polyarb.data.async_live import AsyncLiveDataProvider
from polyarb.data.async_kalshi import AsyncKalshiDataProvider


# ── Polymarket fixtures ─────────────────────────────────────


POLY_MARKET_RAW = {
    "conditionId": "0xabc123",
    "question": "Will BTC hit 100k by June?",
    "outcomePrices": '["0.65","0.35"]',
    "clobTokenIds": '["tok_yes","tok_no"]',
    "bestBid": "0.63",
    "bestAsk": "0.67",
    "negRisk": False,
    "slug": "btc-100k-june",
    "volumeNum": 250000,
    "endDate": "2026-06-01T00:00:00Z",
    "events": [{"slug": "btc-milestones"}],
}

POLY_MARKET_MINIMAL = {
    "conditionId": "0xdef456",
    "question": "Will ETH flip BTC?",
    "outcomePrices": '["0.10","0.90"]',
    "clobTokenIds": '["tok_y2","tok_n2"]',
    "volumeNum": 5000,
}


def _poly_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[POLY_MARKET_RAW, POLY_MARKET_MINIMAL])


# ── Kalshi fixtures ──────────────────────────────────────────


KALSHI_EVENT_RAW = {
    "events": [
        {
            "event_ticker": "BTC100K",
            "title": "Bitcoin above 100k",
            "mutually_exclusive": False,
            "markets": [
                {
                    "ticker": "BTC100K-26JUN",
                    "market_type": "binary",
                    "status": "active",
                    "event_ticker": "BTC100K",
                    "yes_bid_dollars": "0.60",
                    "yes_ask_dollars": "0.64",
                    "no_bid_dollars": "0.36",
                    "no_ask_dollars": "0.40",
                    "volume_24h_fp": "1200",
                    "close_time": "2026-06-01T00:00:00Z",
                },
            ],
        },
        {
            "event_ticker": "PRES2028",
            "title": "2028 Presidential Election",
            "mutually_exclusive": True,
            "markets": [
                {
                    "ticker": "PRES-28-DEM",
                    "market_type": "binary",
                    "status": "active",
                    "event_ticker": "PRES2028",
                    "yes_bid_dollars": "0.45",
                    "yes_ask_dollars": "0.49",
                    "no_bid_dollars": "0.51",
                    "no_ask_dollars": "0.55",
                    "volume_24h_fp": "800",
                    "close_time": "2028-11-05T00:00:00Z",
                },
                {
                    "ticker": "PRES-28-REP",
                    "market_type": "binary",
                    "status": "active",
                    "event_ticker": "PRES2028",
                    "yes_bid_dollars": "0.50",
                    "yes_ask_dollars": "0.54",
                    "no_bid_dollars": "0.46",
                    "no_ask_dollars": "0.50",
                    "volume_24h_fp": "900",
                    "close_time": "2028-11-05T00:00:00Z",
                },
            ],
        },
    ],
    "cursor": "",
}

KALSHI_MARKETS_RAW = {
    "markets": [
        {
            "ticker": "BTC100K-26JUN",
            "market_type": "binary",
            "status": "active",
            "event_ticker": "BTC100K",
            "yes_bid_dollars": "0.60",
            "yes_ask_dollars": "0.64",
            "volume_24h_fp": "1200",
            "close_time": "2026-06-30T00:00:00Z",
        },
    ],
    "cursor": "",
}


def _kalshi_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "/events" in path:
        return httpx.Response(200, json=KALSHI_EVENT_RAW)
    if "/markets" in path:
        return httpx.Response(200, json=KALSHI_MARKETS_RAW)
    return httpx.Response(404)


# ── Polymarket tests ─────────────────────────────────────────


async def test_poly_get_active_markets():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_poly_handler))
    provider = AsyncLiveDataProvider(limit=10, client=client)
    markets = await provider.get_active_markets()
    assert len(markets) == 2
    btc = next(m for m in markets if "BTC" in m.question)
    assert btc.condition_id == "0xabc123"
    assert btc.yes_token.midpoint == 0.65
    assert btc.yes_token.best_bid == 0.63
    assert btc.yes_token.best_ask == 0.67
    assert btc.no_token.midpoint == 0.35
    assert btc.platform == "polymarket"
    assert btc.event_slug == "btc-milestones"
    await provider.close()


async def test_poly_get_events():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_poly_handler))
    provider = AsyncLiveDataProvider(limit=10, client=client)
    events = await provider.get_events()
    # Neither fixture is neg_risk, so no events
    assert events == []
    await provider.close()


async def test_poly_search_markets():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_poly_handler))
    provider = AsyncLiveDataProvider(limit=100, client=client)
    results = await provider.search_markets("btc", limit=5)
    assert len(results) == 1
    assert "BTC" in results[0].question
    await provider.close()


# ── Kalshi tests ─────────────────────────────────────────────


async def test_kalshi_get_active_markets():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_kalshi_handler))
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    markets = await provider.get_active_markets()
    assert len(markets) == 3  # 1 from BTC100K + 2 from PRES2028
    btc = next(m for m in markets if "BTC100K" in m.condition_id)
    assert btc.yes_token.midpoint == 0.62  # (0.60 + 0.64) / 2
    assert btc.platform == "kalshi"
    await provider.close()


async def test_kalshi_neg_risk_detection():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_kalshi_handler))
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    markets = await provider.get_active_markets()
    pres_markets = [m for m in markets if "PRES" in m.condition_id]
    assert len(pres_markets) == 2
    assert all(m.neg_risk for m in pres_markets)
    btc = next(m for m in markets if "BTC" in m.condition_id)
    assert not btc.neg_risk
    await provider.close()


async def test_kalshi_get_events():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_kalshi_handler))
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    events = await provider.get_events()
    assert len(events) == 1  # only PRES2028 is mutually_exclusive
    assert events[0].slug == "PRES2028"
    assert len(events[0].markets) == 2
    await provider.close()


async def test_kalshi_search_markets():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_kalshi_handler))
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    results = await provider.search_markets("bitcoin", limit=5)
    assert len(results) == 1
    assert "Bitcoin" in results[0].question
    await provider.close()
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_async_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.data.async_live'`

- [ ] **Implement AsyncLiveDataProvider**

Create `polyarb/data/async_live.py`:

```python
"""Async Polymarket data provider using the Gamma API (httpx)."""

from __future__ import annotations

import json

import httpx

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token

GAMMA_API = "https://gamma-api.polymarket.com"
_DEFAULT_SPREAD = 0.02


def _parse_dt(s: str | None):
    if not s:
        return None
    from datetime import datetime

    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _parse_market(raw: dict) -> Market | None:
    prices = raw.get("outcomePrices")
    clob_ids = raw.get("clobTokenIds")
    if not prices or not clob_ids:
        return None
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


class AsyncLiveDataProvider:
    """Async Polymarket Gamma API provider using httpx."""

    def __init__(self, limit: int = 100, client: httpx.AsyncClient | None = None) -> None:
        self._limit = limit
        self._client = client or httpx.AsyncClient(
            base_url=GAMMA_API,
            timeout=15.0,
            headers={"Accept": "application/json", "User-Agent": "polyarb/0.1"},
        )
        self._owns_client = client is None

    async def _fetch_json(self, path: str, params: dict | None = None) -> list | dict:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_active_markets(self) -> list[Market]:
        data = await self._fetch_json("/markets", {
            "limit": str(self._limit),
            "order": "volumeNum",
            "ascending": "false",
            "active": "true",
            "closed": "false",
        })
        raw_list = data if isinstance(data, list) else [data]
        markets = [m for raw in raw_list if (m := _parse_market(raw)) is not None]
        markets.sort(key=lambda m: m.volume)
        return markets

    async def get_events(self) -> list[Event]:
        return group_events(await self.get_active_markets())

    async def search_markets(self, query: str, limit: int = 5) -> list[Market]:
        fetch_limit = min(max(limit * 10, 100), 500)
        data = await self._fetch_json("/markets", {
            "limit": str(fetch_limit),
            "order": "volumeNum",
            "ascending": "false",
            "active": "true",
            "closed": "false",
        })
        raw_list = data if isinstance(data, list) else [data]
        markets = [m for raw in raw_list if (m := _parse_market(raw)) is not None]
        q = query.lower()
        markets = [m for m in markets if q in m.question.lower()]
        return markets[:limit]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
```

- [ ] **Implement AsyncKalshiDataProvider**

Create `polyarb/data/async_kalshi.py`:

```python
"""Async Kalshi data provider using the Trading API v2 (httpx)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from polyarb.data.base import group_events
from polyarb.models import Event, Market, Side, Token

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"


def _parse_price(val: str | None) -> float | None:
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
    if raw.get("market_type") != "binary":
        return None
    if raw.get("status") != "active":
        return None
    ticker = raw.get("ticker", "")
    if not ticker:
        return None

    event_ticker = raw.get("event_ticker", "")

    yes_bid = _parse_price(raw.get("yes_bid_dollars"))
    yes_ask = _parse_price(raw.get("yes_ask_dollars"))
    no_bid = _parse_price(raw.get("no_bid_dollars"))
    no_ask = _parse_price(raw.get("no_ask_dollars"))
    last_price = _parse_price(raw.get("last_price_dollars"))

    if yes_bid is not None and yes_ask is not None:
        yes_mid = round((yes_bid + yes_ask) / 2, 4)
    elif last_price is not None:
        yes_mid = last_price
    elif yes_bid is not None:
        yes_mid = yes_bid
    elif yes_ask is not None:
        yes_mid = yes_ask
    else:
        return None

    if no_bid is not None and no_ask is not None:
        no_mid = round((no_bid + no_ask) / 2, 4)
    else:
        no_mid = round(1.0 - yes_mid, 4)

    yes_bid = yes_bid or round(max(0.01, yes_mid - 0.01), 4)
    yes_ask = yes_ask or round(min(0.99, yes_mid + 0.01), 4)
    no_bid = no_bid or round(max(0.01, no_mid - 0.01), 4)
    no_ask = no_ask or round(min(0.99, no_mid + 0.01), 4)

    yes_sub = raw.get("yes_sub_title", "")
    if event_title:
        if yes_sub and yes_sub.lower() not in ("yes", "no", ""):
            question = f"{event_title} — {yes_sub}"
        else:
            question = event_title
    else:
        question = yes_sub or ticker

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
    """Async Kalshi Trading API v2 provider using httpx."""

    def __init__(self, limit: int = 100, client: httpx.AsyncClient | None = None) -> None:
        self._limit = limit
        self._event_titles: dict[str, str] = {}
        self._client = client or httpx.AsyncClient(
            base_url=KALSHI_API,
            timeout=15.0,
            headers={"Accept": "application/json", "User-Agent": "polyarb/0.1"},
        )
        self._owns_client = client is None

    async def _fetch_json(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_active_markets(self) -> list[Market]:
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
        return group_events(await self.get_active_markets(), titles=self._event_titles)

    async def search_markets(self, query: str, limit: int = 5) -> list[Market]:
        all_markets = await self.get_active_markets()
        q = query.lower()
        matches = [m for m in all_markets if q in m.question.lower()]
        matches.sort(key=lambda m: m.volume, reverse=True)
        return matches[:limit]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
```

- [ ] **Run async provider tests**

Run: `pytest polyarb/tests/test_async_providers.py -v`
Expected: All 7 tests PASS.

### Step 5: Write async Kalshi execution client

- [ ] **Implement AsyncKalshiClient**

Create `polyarb/execution/async_kalshi.py`:

```python
"""Async Kalshi execution client using httpx.

Reuses KalshiAuth from the sync module for RSA-PSS signing (pure crypto, no I/O).
"""

from __future__ import annotations

import json

import httpx

from polyarb.execution.kalshi import KALSHI_DEMO, KALSHI_PROD, KalshiAuth


class AsyncKalshiClient:
    """Async authenticated HTTP client for the Kalshi Trading API v2."""

    def __init__(self, auth: KalshiAuth, demo: bool = True) -> None:
        self.auth = auth
        self.base_url = KALSHI_DEMO if demo else KALSHI_PROD
        self.demo = demo
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        api_path = f"/trade-api/v2{path}"
        headers = self.auth.headers(method, api_path)
        content = json.dumps(body).encode("utf-8") if body is not None else None
        resp = await self._client.request(method, url, headers=headers, content=content)
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("message", resp.text)
            except Exception:
                msg = resp.text
            raise RuntimeError(f"Kalshi API {resp.status_code}: {msg}")
        return resp.json() if resp.content else {}

    async def get_balance(self) -> float:
        data = await self._request("GET", "/portfolio/balance")
        return data.get("balance", 0) / 100.0

    async def get_positions(self, ticker: str = "") -> list[dict]:
        params = f"?limit=100&ticker={ticker}" if ticker else "?limit=100"
        data = await self._request("GET", f"/portfolio/positions{params}")
        return data.get("market_positions", [])

    async def create_order(
        self,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        count: int,
        time_in_force: str = "immediate_or_cancel",
    ) -> dict:
        price_field = "yes_price" if side == "yes" else "no_price"
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            price_field: price_cents,
            "count": count,
            "time_in_force": time_in_force,
        }
        data = await self._request("POST", "/portfolio/orders", body)
        return data.get("order", {})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def close(self) -> None:
        await self._client.aclose()
```

### Step 6: Run full test suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All existing tests + new serialization tests + new async provider tests PASS.

- [ ] **Commit**

```bash
git add pyproject.toml polyarb/models.py polyarb/matching/matcher.py polyarb/data/base.py polyarb/data/async_live.py polyarb/data/async_kalshi.py polyarb/execution/async_kalshi.py polyarb/tests/test_serialization.py polyarb/tests/test_async_providers.py
git commit -m "Add async data layer with httpx providers and model serialization

- Add httpx, websockets, uvicorn, starlette, pytest-asyncio deps
- Add to_dict() serialization to all model classes and MatchedPair
- Add AsyncDataProvider protocol alongside existing sync DataProvider
- Add AsyncLiveDataProvider (Polymarket via httpx)
- Add AsyncKalshiDataProvider (Kalshi via httpx)
- Add AsyncKalshiClient for async order execution
- Existing sync code untouched"
```

---

## Task 2: Daemon Core

**Files:**
- Create: `polyarb/daemon/__init__.py`
- Create: `polyarb/daemon/state.py`
- Create: `polyarb/daemon/engine.py`
- Create: `polyarb/daemon/server.py`
- Create: `polyarb/daemon/__main__.py`
- Create: `polyarb/tests/test_daemon_state.py`
- Create: `polyarb/tests/test_daemon_engine.py`
- Create: `polyarb/tests/test_server.py`

### Step 1: Write state tests

- [ ] **Create package marker**

Create `polyarb/daemon/__init__.py` as an empty file.

- [ ] **Write tests for State**

Create `polyarb/tests/test_daemon_state.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from polyarb.daemon.state import State
from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.models import ArbType, Market, Opportunity, Side, Token


def _make_market(cid: str = "c1", question: str = "Q?", platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(token_id=f"{cid}:y", side=Side.YES, midpoint=0.6, best_bid=0.59, best_ask=0.61),
        no_token=Token(token_id=f"{cid}:n", side=Side.NO, midpoint=0.4, best_bid=0.39, best_ask=0.41),
        platform=platform,
    )


def _make_pair(poly_q: str = "BTC 100k?", kalshi_q: str = "Bitcoin 100k?", conf: float = 0.8) -> MatchedPair:
    return MatchedPair(
        poly_market=_make_market("p1", poly_q, "polymarket"),
        kalshi_market=_make_market("k1", kalshi_q, "kalshi"),
        confidence=conf,
    )


def _make_opp(cid: str = "c1") -> Opportunity:
    return Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(_make_market(cid),),
        expected_profit_per_share=0.02,
    )


def test_state_creation():
    state = State(config=Config())
    assert state.scan_count == 0
    assert state.matches == []
    assert state.opportunities == []
    assert state.last_scan_at is None


def test_update_matches_returns_new_only():
    state = State(config=Config())
    pair1 = _make_pair("A?", "A?")
    pair2 = _make_pair("B?", "B?")

    new = state.update_matches([pair1, pair2])
    assert len(new) == 2
    assert len(state.matches) == 2

    # Same pairs again — no new ones
    new = state.update_matches([pair1, pair2])
    assert len(new) == 0
    assert len(state.matches) == 2


def test_update_matches_detects_new_additions():
    state = State(config=Config())
    pair1 = _make_pair("A?", "A?")
    state.update_matches([pair1])

    pair2 = _make_pair("B?", "B?")
    new = state.update_matches([pair1, pair2])
    assert len(new) == 1
    assert len(state.matches) == 2


def test_update_opportunities_dedup():
    state = State(config=Config())
    opp1 = _make_opp("c1")
    opp2 = _make_opp("c2")

    new = state.update_opportunities([opp1, opp2])
    assert len(new) == 2

    new = state.update_opportunities([opp1, opp2])
    assert len(new) == 0


def test_update_increments_scan_count():
    state = State(config=Config())
    state.update_matches([])
    assert state.scan_count == 1
    assert state.last_scan_at is not None


async def test_broadcast_to_ws_clients():
    state = State(config=Config())
    received: list[dict] = []

    class FakeWS:
        async def send_json(self, data: dict) -> None:
            received.append(data)

    state.ws_clients.add(FakeWS())
    await state.broadcast({"type": "test", "data": "hello"})
    assert len(received) == 1
    assert received[0]["type"] == "test"


async def test_broadcast_removes_disconnected_clients():
    state = State(config=Config())

    class BrokenWS:
        async def send_json(self, data: dict) -> None:
            raise Exception("disconnected")

    state.ws_clients.add(BrokenWS())
    await state.broadcast({"type": "test"})
    assert len(state.ws_clients) == 0


def test_status_dict():
    state = State(config=Config())
    d = state.status_dict()
    assert "uptime_seconds" in d
    assert d["scan_count"] == 0
    assert d["connected_clients"] == 0
    assert d["match_count"] == 0
    assert d["opportunity_count"] == 0
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_daemon_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.daemon.state'`

- [ ] **Implement State**

Create `polyarb/daemon/state.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Opportunity


def _match_key(pair: MatchedPair) -> str:
    return f"{pair.poly_market.condition_id}:{pair.kalshi_market.condition_id}"


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
        self.scan_count += 1
        self.last_scan_at = datetime.now(timezone.utc)
        self.matches = matches
        new: list[MatchedPair] = []
        for pair in matches:
            key = _match_key(pair)
            if key not in self._seen_matches:
                self._seen_matches.add(key)
                new.append(pair)
        return new

    def update_opportunities(self, opps: list[Opportunity]) -> list[Opportunity]:
        self.opportunities = opps
        new: list[Opportunity] = []
        for opp in opps:
            if opp.key not in self._seen_opps:
                self._seen_opps.add(opp.key)
                new.append(opp)
        return new

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list = []
        for ws in self.ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def status_dict(self) -> dict:
        now = datetime.now(timezone.utc)
        uptime = (now - self.started_at).total_seconds()
        return {
            "uptime_seconds": round(uptime, 1),
            "scan_count": self.scan_count,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "connected_clients": len(self.ws_clients),
            "match_count": len(self.matches),
            "opportunity_count": len(self.opportunities),
        }
```

- [ ] **Run state tests**

Run: `pytest polyarb/tests/test_daemon_state.py -v`
Expected: All 9 tests PASS.

### Step 2: Write engine tests and implementation

- [ ] **Write tests for scan loop**

Create `polyarb/tests/test_daemon_engine.py`:

```python
from __future__ import annotations

import asyncio

from polyarb.config import Config
from polyarb.daemon.engine import run_scan_once
from polyarb.daemon.state import State
from polyarb.models import Market, Side, Token


def _make_market(cid: str, question: str, platform: str, yes_mid: float = 0.6) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(token_id=f"{cid}:y", side=Side.YES, midpoint=yes_mid, best_bid=yes_mid - 0.01, best_ask=yes_mid + 0.01),
        no_token=Token(token_id=f"{cid}:n", side=Side.NO, midpoint=round(1 - yes_mid, 4), best_bid=round(1 - yes_mid - 0.01, 4), best_ask=round(1 - yes_mid + 0.01, 4)),
        platform=platform,
        event_slug=f"evt-{cid}",
        slug=cid,
    )


class FakeProvider:
    def __init__(self, markets: list[Market]):
        self._markets = markets
        self.call_count = 0

    async def get_active_markets(self):
        self.call_count += 1
        return self._markets

    async def get_events(self):
        return []

    async def search_markets(self, query, limit=5):
        return []

    async def close(self):
        pass


async def test_scan_once_fetches_both_platforms():
    poly = FakeProvider([_make_market("p1", "BTC 100k?", "polymarket")])
    kalshi = FakeProvider([_make_market("k1", "Bitcoin 100k?", "kalshi")])
    state = State(config=Config(min_profit=0.0))

    await run_scan_once(state, poly, kalshi)

    assert poly.call_count == 1
    assert kalshi.call_count == 1
    assert state.scan_count == 1


async def test_scan_once_finds_matches():
    poly = FakeProvider([_make_market("p1", "BTC 100k by June?", "polymarket", 0.65)])
    kalshi = FakeProvider([_make_market("k1", "BTC 100k by June?", "kalshi", 0.60)])
    state = State(config=Config(min_profit=0.0))

    await run_scan_once(state, poly, kalshi)

    assert len(state.matches) >= 1


async def test_scan_once_dedup():
    poly = FakeProvider([_make_market("p1", "BTC 100k?", "polymarket")])
    kalshi = FakeProvider([_make_market("k1", "BTC 100k?", "kalshi")])
    state = State(config=Config(min_profit=0.0))

    await run_scan_once(state, poly, kalshi)
    first_scan_count = state.scan_count

    # Run again — same data, no new matches should be returned
    await run_scan_once(state, poly, kalshi)
    assert state.scan_count == first_scan_count + 1
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_daemon_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.daemon.engine'`

- [ ] **Implement engine**

Create `polyarb/daemon/engine.py`:

```python
"""Async scan loop — polls both platforms, runs detection, updates state."""

from __future__ import annotations

import asyncio
import logging

from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.data.base import AsyncDataProvider, group_events
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.matching.matcher import find_matches

logger = logging.getLogger(__name__)


async def run_scan_once(
    state: State,
    poly: AsyncDataProvider,
    kalshi: AsyncDataProvider,
) -> None:
    config = state.config

    poly_markets, kalshi_markets = await asyncio.gather(
        poly.get_active_markets(),
        kalshi.get_active_markets(),
    )

    all_markets = poly_markets + kalshi_markets

    matches = await asyncio.to_thread(find_matches, poly_markets, kalshi_markets)

    single_opps = await asyncio.to_thread(detect_single, all_markets, config)
    events = await asyncio.to_thread(group_events, all_markets)
    multi_opps = await asyncio.to_thread(detect_multi, events, config)

    new_matches = state.update_matches(matches)
    state.update_opportunities(single_opps + multi_opps)

    for match in new_matches:
        await state.broadcast({"type": "new_opportunity", "data": match.to_dict()})

    logger.info(
        "Scan #%d: %d poly, %d kalshi, %d matches (%d new), %d opps",
        state.scan_count,
        len(poly_markets),
        len(kalshi_markets),
        len(state.matches),
        len(new_matches),
        len(state.opportunities),
    )


async def run_scan_loop(
    state: State,
    poly: AsyncDataProvider,
    kalshi: AsyncDataProvider,
) -> None:
    while True:
        try:
            await run_scan_once(state, poly, kalshi)
        except Exception:
            logger.exception("Scan loop error")
        await asyncio.sleep(state.config.scan_interval)
```

- [ ] **Run engine tests**

Run: `pytest polyarb/tests/test_daemon_engine.py -v`
Expected: All 3 tests PASS.

### Step 3: Write server tests and implementation

- [ ] **Write tests for REST API**

Create `polyarb/tests/test_server.py`:

```python
from __future__ import annotations

from starlette.testclient import TestClient

from polyarb.config import Config
from polyarb.daemon.server import create_app
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import ArbType, Market, Opportunity, Side, Token


def _make_market(cid: str = "c1", question: str = "Q?", platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(token_id=f"{cid}:y", side=Side.YES, midpoint=0.6, best_bid=0.59, best_ask=0.61),
        no_token=Token(token_id=f"{cid}:n", side=Side.NO, midpoint=0.4, best_bid=0.39, best_ask=0.41),
        platform=platform,
    )


def _make_pair() -> MatchedPair:
    return MatchedPair(
        poly_market=_make_market("p1", "BTC 100k?", "polymarket"),
        kalshi_market=_make_market("k1", "Bitcoin 100k?", "kalshi"),
        confidence=0.85,
    )


def _make_state() -> State:
    return State(config=Config())


def test_get_status():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "uptime_seconds" in data
    assert data["scan_count"] == 0


def test_get_matches_empty():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/matches")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_matches_with_data():
    state = _make_state()
    state.matches = [_make_pair()]
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/matches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["confidence"] == 0.85


def test_get_match_by_id():
    state = _make_state()
    state.matches = [_make_pair()]
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/matches/1")
    assert resp.status_code == 200
    assert resp.json()["confidence"] == 0.85


def test_get_match_by_id_not_found():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/matches/99")
    assert resp.status_code == 404


def test_get_opportunities_empty():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/opportunities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_opportunities_with_data():
    state = _make_state()
    m = _make_market()
    state.opportunities = [
        Opportunity(arb_type=ArbType.SINGLE_UNDERPRICE, markets=(m,), expected_profit_per_share=0.02)
    ]
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/opportunities")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["arb_type"] == "SINGLE_UNDERPRICE"


def test_get_config():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["min_profit"] == 0.005
    assert data["scan_interval"] == 10.0


def test_post_config():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.post("/config", json={"scan_interval": 5.0, "min_profit": 0.01})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_interval"] == 5.0
    assert data["min_profit"] == 0.01
    assert state.config.scan_interval == 5.0


def test_post_config_rejects_unknown_keys():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.post("/config", json={"bogus_key": 123})
    assert resp.status_code == 400


def test_execute_not_connected():
    state = _make_state()
    state.matches = [_make_pair()]
    app = create_app(state)
    client = TestClient(app)
    resp = client.post("/execute/1")
    assert resp.status_code == 409
    assert "not connected" in resp.json()["error"].lower()


def test_execute_not_found():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.post("/execute/99")
    assert resp.status_code == 404
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.daemon.server'`

- [ ] **Implement server**

Create `polyarb/daemon/server.py`:

```python
"""Starlette REST API + WebSocket push endpoint."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from polyarb.daemon.state import State


def create_app(state: State, kalshi_client=None) -> Starlette:

    async def get_status(request: Request) -> JSONResponse:
        return JSONResponse(state.status_dict())

    async def get_matches(request: Request) -> JSONResponse:
        return JSONResponse([m.to_dict() for m in state.matches])

    async def get_match(request: Request) -> JSONResponse:
        idx = int(request.path_params["id"]) - 1  # 1-based to 0-based
        if idx < 0 or idx >= len(state.matches):
            return JSONResponse({"error": "Match not found"}, status_code=404)
        return JSONResponse(state.matches[idx].to_dict())

    async def get_opportunities(request: Request) -> JSONResponse:
        return JSONResponse([o.to_dict() for o in state.opportunities])

    async def execute_match(request: Request) -> JSONResponse:
        idx = int(request.path_params["id"]) - 1
        if idx < 0 or idx >= len(state.matches):
            return JSONResponse({"error": "Match not found"}, status_code=404)

        if kalshi_client is None:
            return JSONResponse(
                {"error": "Kalshi executor not connected. Set KALSHI_API_KEY and KALSHI_KEY_FILE env vars."},
                status_code=409,
            )

        pair = state.matches[idx]
        profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = pair.best_arb
        ticker = pair.kalshi_market.condition_id
        price_cents = max(1, min(99, round(kalshi_price * 100)))
        size = max(1, int(state.config.order_size))

        try:
            result = await kalshi_client.create_order(
                ticker=ticker,
                side=kalshi_side,
                action="buy",
                price_cents=price_cents,
                count=size,
            )
            return JSONResponse({
                "status": "ok",
                "order": result,
                "kalshi_desc": kalshi_desc,
                "poly_desc": poly_desc,
                "profit_per_share": profit,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def get_config(request: Request) -> JSONResponse:
        return JSONResponse(state.config.__dict__)

    async def post_config(request: Request) -> JSONResponse:
        body = await request.json()
        for key, val in body.items():
            if not hasattr(state.config, key):
                return JSONResponse(
                    {"error": f"Unknown config key: {key}"}, status_code=400
                )
        for key, val in body.items():
            cur = getattr(state.config, key)
            setattr(state.config, key, type(cur)(val))
        return JSONResponse(state.config.__dict__)

    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            state.ws_clients.discard(websocket)

    routes = [
        Route("/status", get_status, methods=["GET"]),
        Route("/matches", get_matches, methods=["GET"]),
        Route("/matches/{id:int}", get_match, methods=["GET"]),
        Route("/opportunities", get_opportunities, methods=["GET"]),
        Route("/execute/{id:int}", execute_match, methods=["POST"]),
        Route("/config", get_config, methods=["GET"]),
        Route("/config", post_config, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
    ]

    return Starlette(routes=routes)
```

- [ ] **Run server tests**

Run: `pytest polyarb/tests/test_server.py -v`
Expected: All 12 tests PASS.

### Step 4: Write daemon entry point

- [ ] **Implement daemon __main__**

Create `polyarb/daemon/__main__.py`:

```python
"""Daemon entry point: python -m polyarb.daemon"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn

from polyarb.config import Config
from polyarb.daemon.engine import run_scan_loop
from polyarb.daemon.server import create_app
from polyarb.daemon.state import State
from polyarb.data.async_kalshi import AsyncKalshiDataProvider
from polyarb.data.async_live import AsyncLiveDataProvider


def _build_kalshi_client():
    api_key = os.environ.get("KALSHI_API_KEY", "")
    key_file = os.environ.get("KALSHI_KEY_FILE", "")
    if not api_key or not key_file:
        return None
    try:
        from polyarb.execution.kalshi import KalshiAuth
        from polyarb.execution.async_kalshi import AsyncKalshiClient
        auth = KalshiAuth(api_key, key_file)
        is_live = os.environ.get("KALSHI_ENV", "demo").lower() == "live"
        return AsyncKalshiClient(auth, demo=not is_live)
    except ImportError:
        logging.warning("cryptography not installed — Kalshi execution disabled")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb.daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="API port")
    parser.add_argument("--interval", type=float, default=5.0, help="Scan interval (seconds)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config(scan_interval=args.interval)
    state = State(config=config)

    poly = AsyncLiveDataProvider(limit=100)
    kalshi = AsyncKalshiDataProvider(limit=200)
    kalshi_client = _build_kalshi_client()

    app = create_app(state, kalshi_client=kalshi_client)

    scan_task: asyncio.Task | None = None

    @app.on_event("startup")
    async def startup():
        nonlocal scan_task
        loop = asyncio.get_event_loop()
        scan_task = loop.create_task(run_scan_loop(state, poly, kalshi))
        logging.info(
            "Daemon started on %s:%d (interval=%.1fs, kalshi_exec=%s)",
            args.host, args.port, args.interval,
            "enabled" if kalshi_client else "disabled",
        )

    @app.on_event("shutdown")
    async def shutdown():
        if scan_task:
            scan_task.cancel()
        await poly.close()
        await kalshi.close()
        if kalshi_client:
            await kalshi_client.close()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
```

### Step 5: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS (existing + serialization + async providers + state + engine + server).

- [ ] **Commit**

```bash
git add polyarb/daemon/
git add polyarb/tests/test_daemon_state.py polyarb/tests/test_daemon_engine.py polyarb/tests/test_server.py
git commit -m "Add async daemon with scan loop, state, REST API and WS push

- State container with dedup, WS broadcast, status reporting
- Engine: concurrent platform polling via asyncio.gather, CPU-bound
  detection offloaded to threads
- Starlette REST API: /status, /matches, /opportunities, /execute,
  /config endpoints + /ws push channel
- Daemon entry point with uvicorn, lifespan-managed scan loop"
```

---

## Task 3: Thin CLI Client

**Files:**
- Create: `polyarb/client/__init__.py`
- Create: `polyarb/client/api.py`
- Create: `polyarb/client/ws_listener.py`
- Create: `polyarb/client/cli.py`
- Create: `polyarb/client/__main__.py`
- Modify: `polyarb/__main__.py`
- Create: `polyarb/tests/test_client_api.py`

### Step 1: Write client API tests

- [ ] **Create package marker**

Create `polyarb/client/__init__.py` as an empty file.

- [ ] **Write tests for DaemonClient**

Create `polyarb/tests/test_client_api.py`:

```python
from __future__ import annotations

import httpx

from polyarb.client.api import DaemonClient


STATUS_RESPONSE = {
    "uptime_seconds": 120.5,
    "scan_count": 10,
    "last_scan_at": "2026-03-26T12:00:00+00:00",
    "connected_clients": 1,
    "match_count": 3,
    "opportunity_count": 2,
}

MATCHES_RESPONSE = [
    {
        "confidence": 0.85,
        "poly_market": {"condition_id": "p1", "question": "BTC 100k?"},
        "kalshi_market": {"condition_id": "k1", "question": "Bitcoin 100k?"},
        "best_arb": {"profit": 0.02, "kalshi_side": "yes"},
    }
]

CONFIG_RESPONSE = {"min_profit": 0.005, "scan_interval": 5.0, "order_size": 10.0}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    method = request.method

    if path == "/status" and method == "GET":
        return httpx.Response(200, json=STATUS_RESPONSE)
    if path == "/matches" and method == "GET":
        return httpx.Response(200, json=MATCHES_RESPONSE)
    if path == "/matches/1" and method == "GET":
        return httpx.Response(200, json=MATCHES_RESPONSE[0])
    if path == "/matches/99" and method == "GET":
        return httpx.Response(404, json={"error": "not found"})
    if path == "/opportunities" and method == "GET":
        return httpx.Response(200, json=[])
    if path == "/config" and method == "GET":
        return httpx.Response(200, json=CONFIG_RESPONSE)
    if path == "/config" and method == "POST":
        return httpx.Response(200, json=CONFIG_RESPONSE)
    if path == "/execute/1" and method == "POST":
        return httpx.Response(409, json={"error": "not connected"})
    return httpx.Response(404)


def _make_client() -> DaemonClient:
    transport = httpx.MockTransport(_handler)
    http_client = httpx.Client(transport=transport, base_url="http://test")
    return DaemonClient(client=http_client)


def test_get_status():
    c = _make_client()
    data = c.get_status()
    assert data["scan_count"] == 10
    assert data["uptime_seconds"] == 120.5


def test_get_matches():
    c = _make_client()
    data = c.get_matches()
    assert len(data) == 1
    assert data[0]["confidence"] == 0.85


def test_get_match():
    c = _make_client()
    data = c.get_match(1)
    assert data["confidence"] == 0.85


def test_get_match_not_found():
    c = _make_client()
    data = c.get_match(99)
    assert data is None


def test_get_opportunities():
    c = _make_client()
    data = c.get_opportunities()
    assert data == []


def test_get_config():
    c = _make_client()
    data = c.get_config()
    assert data["scan_interval"] == 5.0


def test_set_config():
    c = _make_client()
    data = c.set_config({"scan_interval": 5.0})
    assert data["scan_interval"] == 5.0


def test_execute_not_connected():
    c = _make_client()
    data = c.execute(1)
    assert "error" in data
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_client_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.client.api'`

- [ ] **Implement DaemonClient**

Create `polyarb/client/api.py`:

```python
"""Sync HTTP client for the polyarb daemon REST API."""

from __future__ import annotations

import httpx


class DaemonClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._owns_client = client is None

    def _get(self, path: str) -> dict | list | None:
        resp = self._client.get(path)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> dict:
        return self._get("/status")

    def get_matches(self) -> list[dict]:
        return self._get("/matches") or []

    def get_match(self, match_id: int) -> dict | None:
        return self._get(f"/matches/{match_id}")

    def get_opportunities(self) -> list[dict]:
        return self._get("/opportunities") or []

    def execute(self, match_id: int) -> dict:
        resp = self._client.post(f"/execute/{match_id}")
        return resp.json()

    def get_config(self) -> dict:
        return self._get("/config")

    def set_config(self, data: dict) -> dict:
        resp = self._client.post("/config", json=data)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
```

- [ ] **Run client API tests**

Run: `pytest polyarb/tests/test_client_api.py -v`
Expected: All 8 tests PASS.

### Step 2: Implement WS listener

- [ ] **Create ws_listener.py**

Create `polyarb/client/ws_listener.py`:

```python
"""Background thread that listens on the daemon's /ws endpoint for push alerts."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

import websockets.sync.client as ws_sync


def start_ws_listener(
    url: str = "ws://127.0.0.1:8080/ws",
    on_message: Callable[[dict], None] | None = None,
) -> threading.Thread:
    def _run():
        while True:
            try:
                with ws_sync.connect(url) as ws:
                    for raw in ws:
                        if on_message:
                            try:
                                data = json.loads(raw)
                                on_message(data)
                            except (json.JSONDecodeError, Exception):
                                pass
            except Exception:
                time.sleep(2)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
```

### Step 3: Implement client CLI

- [ ] **Create client CLI**

Create `polyarb/client/cli.py`:

```python
"""Thin CLI client that talks to the polyarb daemon via REST."""

from __future__ import annotations

import cmd
import shutil
import sys

from polyarb.client.api import DaemonClient
from polyarb.client.ws_listener import start_ws_listener
from polyarb.colors import BOLD as B, CYAN, DIM, GREEN, RED, RESET as R, YELLOW


def _cols() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _link(url: str, text: str) -> str:
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


class ClientShell(cmd.Cmd):
    prompt = f"{B}polyarb> {R}"

    def __init__(self, daemon_url: str = "http://127.0.0.1:8080") -> None:
        super().__init__()
        self._url = daemon_url
        self._api = DaemonClient(base_url=daemon_url)
        ws_url = daemon_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self._ws_thread = start_ws_listener(url=ws_url, on_message=self._on_push)
        self.intro = (
            f"\n{B}{CYAN}  polyarb client{R} — connected to {daemon_url}\n"
            f"  Type {B}help{R} for commands, {B}quit{R} to exit.\n"
        )

    def _on_push(self, data: dict) -> None:
        msg_type = data.get("type", "")
        if msg_type == "new_opportunity":
            arb = data.get("data", {}).get("best_arb", {})
            profit = arb.get("profit", 0)
            desc = arb.get("kalshi_desc", "")
            if profit > 0:
                print(f"\n{GREEN}{B}  [ALERT] New arb: {desc} — profit/share ${profit:.4f}{R}")
                print(f"  Run {B}cross{R} to see details.\n{self.prompt}", end="", flush=True)

    # ── Commands ───────────────────────────────────────────

    def do_status(self, arg: str) -> None:
        """Show daemon status."""
        try:
            data = self._api.get_status()
        except Exception as e:
            print(f"{RED}Connection failed: {e}{R}")
            return
        print(f"\n{B}Daemon Status{R}")
        print(f"  Uptime       : {data['uptime_seconds']:.0f}s")
        print(f"  Scans        : {data['scan_count']}")
        print(f"  Last scan    : {data.get('last_scan_at', '—')}")
        print(f"  WS clients   : {data['connected_clients']}")
        print(f"  Matches      : {data['match_count']}")
        print(f"  Opportunities: {data['opportunity_count']}")
        print()

    def do_cross(self, arg: str) -> None:
        """Show cross-platform matches from the daemon."""
        try:
            matches = self._api.get_matches()
        except Exception as e:
            print(f"{RED}Connection failed: {e}{R}")
            return

        if not matches:
            print(f"{YELLOW}No cross-platform matches found.{R}")
            return

        w = _cols()
        qw = max(15, (w - 60) // 2)
        arb_count = sum(1 for m in matches if m.get("best_arb", {}).get("profit", 0) > 0)

        print(f"\n{B}{GREEN}{len(matches)} matches, {arb_count} with positive arb:{R}\n")
        print(
            f"{B}{'#':>3}  {'Conf':>5}  {'Arb':>8}  {'Kalshi leg':<14}  "
            f"{'Polymarket':<{qw}}  {'Kalshi':<{qw}}{R}"
        )
        print("─" * min(w, 120))

        for i, m in enumerate(matches, 1):
            arb = m.get("best_arb", {})
            profit = arb.get("profit", 0)
            kalshi_desc = arb.get("kalshi_desc", "")
            color = GREEN if profit > 0 else ""
            short_side = kalshi_desc.replace("BUY ", "").replace(" on Kalshi", "")
            pm_q = _trunc(m.get("poly_market", {}).get("question", "?"), qw)
            km_q = _trunc(m.get("kalshi_market", {}).get("question", "?"), qw)
            sign = "+" if profit > 0 else ""
            print(
                f"{color}{i:>3}  {m.get('confidence', 0):>5.0%}  "
                f"{sign}${profit:>6.4f}  {short_side:<14}  "
                f"{pm_q}  {km_q}{R}"
            )

        print(f"\n  Use {B}opp <#>{R} for details, {B}execute <#>{R} to trade.\n")

    def do_opp(self, arg: str) -> None:
        """Show details for a match. Usage: opp <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: opp <#>{R}")
            return

        try:
            data = self._api.get_match(idx)
        except Exception as e:
            print(f"{RED}Connection failed: {e}{R}")
            return

        if data is None:
            print(f"{YELLOW}Match #{idx} not found.{R}")
            return

        arb = data.get("best_arb", {})
        profit = arb.get("profit", 0)
        color = GREEN if profit > 0 else YELLOW

        print(f"\n{B}{color}Cross-Platform Match #{idx}{R}  ({data.get('confidence', 0):.0%} confidence)\n")

        pm = data.get("poly_market", {})
        km = data.get("kalshi_market", {})

        print(f"  {B}Polymarket:{R} {pm.get('question', '?')}")
        yt = pm.get("yes_token", {})
        nt = pm.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        print(f"\n  {B}Kalshi:{R} {km.get('question', '?')}")
        yt = km.get("yes_token", {})
        nt = km.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        print(f"\n  {B}Arb (at ask prices):{R}")
        print(f"    {arb.get('kalshi_desc', '')}  +  {arb.get('poly_desc', '')}")
        print(f"    Profit/share: {color}${profit:.4f}{R}")
        print()

    def do_execute(self, arg: str) -> None:
        """Execute a cross-platform arb. Usage: execute <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: execute <#>{R}")
            return

        try:
            data = self._api.execute(idx)
        except Exception as e:
            print(f"{RED}Connection failed: {e}{R}")
            return

        if "error" in data:
            print(f"{RED}  {data['error']}{R}")
            return

        print(f"{GREEN}  Order placed: {data.get('kalshi_desc', '')}{R}")
        print(f"  Profit/share: ${data.get('profit_per_share', 0):.4f}")
        order = data.get("order", {})
        if order:
            print(f"  Status: {order.get('status', '?')}, filled: {order.get('fill_count_fp', '?')}")
        print(f"\n  {CYAN}{data.get('poly_desc', '')} — execute manually on Polymarket{R}\n")

    def do_config(self, arg: str) -> None:
        """View or set config. Usage: config [key=value]"""
        if not arg:
            try:
                data = self._api.get_config()
            except Exception as e:
                print(f"{RED}Connection failed: {e}{R}")
                return
            print(f"\n{B}Config{R}")
            for k, v in data.items():
                print(f"  {k:<16} = {v}")
            print()
            return

        try:
            key, val = arg.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Try to parse as number
            try:
                parsed = int(val)
            except ValueError:
                try:
                    parsed = float(val)
                except ValueError:
                    parsed = val
            data = self._api.set_config({key: parsed})
            print(f"{GREEN}{key} = {data.get(key, val)}{R}")
        except ValueError:
            print(f"{YELLOW}Usage: config key=value{R}")
        except Exception as e:
            print(f"{RED}{e}{R}")

    def do_quit(self, arg: str) -> bool:
        """Exit polyarb client."""
        self._api.close()
        print(f"{DIM}Goodbye.{R}")
        return True

    do_exit = do_quit
    do_q = do_quit
    do_EOF = do_quit

    def emptyline(self) -> None:
        pass

    def default(self, line: str) -> None:
        print(f"{YELLOW}Unknown command: {line!r}. Type {B}help{R}{YELLOW} for commands.{R}")


def _parse_int(s: str, default: int) -> int:
    s = s.strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return default
```

### Step 4: Create entry points

- [ ] **Create client __main__**

Create `polyarb/client/__main__.py`:

```python
"""Client entry point: python -m polyarb.client"""

from __future__ import annotations

import argparse

from polyarb.client.cli import ClientShell


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb.client")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Daemon URL")
    args = parser.parse_args()

    shell = ClientShell(daemon_url=args.url)
    shell.cmdloop()


if __name__ == "__main__":
    main()
```

- [ ] **Update top-level __main__.py**

Replace `polyarb/__main__.py` with:

```python
import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="Start the daemon")
    mode.add_argument("--mock", action="store_true", help="Run old CLI with mock data")
    mode.add_argument("--poly", action="store_true", help="(legacy) Old CLI with live Polymarket data")
    mode.add_argument("--kalshi", action="store_true", help="(legacy) Old CLI with live Kalshi data")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Daemon URL (client mode)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (daemon mode)")
    parser.add_argument("--port", type=int, default=8080, help="API port (daemon mode)")
    parser.add_argument("--interval", type=float, default=5.0, help="Scan interval (daemon mode)")
    args = parser.parse_args()

    if args.daemon:
        sys.argv = [sys.argv[0]]
        if args.host != "127.0.0.1":
            sys.argv += ["--host", args.host]
        if args.port != 8080:
            sys.argv += ["--port", str(args.port)]
        if args.interval != 5.0:
            sys.argv += ["--interval", str(args.interval)]
        from polyarb.daemon.__main__ import main as daemon_main
        daemon_main()
    elif args.mock or args.poly or args.kalshi:
        from polyarb.cli import PolyarbShell
        shell = PolyarbShell(live=args.poly, kalshi=args.kalshi)
        shell.cmdloop()
    else:
        from polyarb.client.cli import ClientShell
        shell = ClientShell(daemon_url=args.url)
        shell.cmdloop()


if __name__ == "__main__":
    main()
```

### Step 5: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Commit**

```bash
git add polyarb/client/ polyarb/__main__.py polyarb/tests/test_client_api.py
git commit -m "Add thin CLI client that connects to daemon via REST + WS

- DaemonClient: sync httpx wrapper for all daemon REST endpoints
- WS listener: background thread for real-time opportunity alerts
- ClientShell: cmd.Cmd REPL with cross/opp/execute/config/status
- Updated __main__.py: --daemon / --mock / default (client) routing"
```

---

## Task 4: Docker

**Files:**
- Modify: `Dockerfile`
- Modify: `compose.yaml`

### Step 1: Update Dockerfile

- [ ] **Update Dockerfile**

Replace `Dockerfile` with:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# Accept proxy config as build args (for sandbox/CI environments)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG PROXY_CA_CERT_B64

# Install proxy CA cert if provided
RUN if [ -n "$PROXY_CA_CERT_B64" ]; then \
      echo "$PROXY_CA_CERT_B64" | base64 -d > /usr/local/share/ca-certificates/proxy-ca.crt && \
      update-ca-certificates && \
      export PIP_CERT=/usr/local/share/ca-certificates/proxy-ca.crt; \
    fi

COPY pyproject.toml .
COPY polyarb/ polyarb/

RUN pip install --no-cache-dir -e ".[dev,trade]"

EXPOSE 8080

ENTRYPOINT ["python", "-m", "polyarb"]
```

### Step 2: Update compose.yaml

- [ ] **Update compose.yaml**

Replace `compose.yaml` with:

```yaml
services:
  daemon:
    build:
      context: .
      args:
        HTTP_PROXY: ${HTTP_PROXY:-}
        HTTPS_PROXY: ${HTTPS_PROXY:-}
        NO_PROXY: ${NO_PROXY:-}
        PROXY_CA_CERT_B64: ${PROXY_CA_CERT_B64:-}
    ports:
      - "8080:8080"
    command: ["--daemon", "--host", "0.0.0.0"]
    environment:
      - KALSHI_API_KEY=${KALSHI_API_KEY:-}
      - KALSHI_KEY_FILE=/run/secrets/kalshi_key
      - KALSHI_ENV=${KALSHI_ENV:-demo}
      - HTTP_PROXY=${HTTP_PROXY:-}
      - HTTPS_PROXY=${HTTPS_PROXY:-}
      - http_proxy=${http_proxy:-}
      - https_proxy=${https_proxy:-}
      - NO_PROXY=${NO_PROXY:-}
      - no_proxy=${no_proxy:-}
    volumes:
      - ${KALSHI_KEY_FILE:-.docker-dummy-key}:/run/secrets/kalshi_key:ro

  client:
    build:
      context: .
    stdin_open: true
    tty: true
    network_mode: "service:daemon"
    command: ["--url", "http://127.0.0.1:8080"]
    depends_on:
      - daemon
```

### Step 3: Verify and commit

- [ ] **Verify Docker build**

Run: `docker compose build`
Expected: Both services build successfully.

- [ ] **Commit**

```bash
git add Dockerfile compose.yaml
git commit -m "Update Docker config for daemon + client architecture

- Dockerfile: expose 8080, default entrypoint is polyarb (routes via args)
- compose.yaml: daemon service (--host 0.0.0.0) + client service
  (shares daemon network, connects via localhost)"
```

---

## Task 5: Cleanup

**Files:**
- Delete: `polyarb/data/live.py`
- Delete: `polyarb/data/kalshi.py`
- Delete: `polyarb/engine/scanner.py`
- Delete: `polyarb/cli.py`
- Modify: `pyproject.toml` (remove certifi)

### Step 1: Remove old sync data providers

- [ ] **Delete old files**

```bash
rm polyarb/data/live.py polyarb/data/kalshi.py
```

- [ ] **Delete scanner**

```bash
rm polyarb/engine/scanner.py
```

- [ ] **Delete old CLI**

```bash
rm polyarb/cli.py
```

### Step 2: Update __main__.py legacy paths

- [ ] **Update legacy mode in __main__.py**

The `--mock`, `--poly`, `--kalshi` flags referenced `polyarb.cli.PolyarbShell` which no longer exists. Update the legacy block in `polyarb/__main__.py` to remove `--poly` and `--kalshi` flags, keeping only `--mock` which uses MockDataProvider through the client. Replace the legacy section:

```python
    elif args.mock or args.poly or args.kalshi:
        from polyarb.cli import PolyarbShell
        shell = PolyarbShell(live=args.poly, kalshi=args.kalshi)
        shell.cmdloop()
```

with:

```python
    elif args.mock:
        from polyarb.data.mock import MockDataProvider
        from polyarb.execution.executor import MockExecutor
        from polyarb.engine.single import detect_single
        from polyarb.engine.multi import detect_multi
        from polyarb.data.base import group_events
        from polyarb.config import Config

        config = Config()
        provider = MockDataProvider(drift=True)
        markets = provider.get_active_markets()
        events = group_events(markets)
        opps = detect_single(markets, config) + detect_multi(events, config)
        if opps:
            for i, opp in enumerate(opps, 1):
                print(f"[{i}] {opp.summary()}")
        else:
            print("No opportunities found in mock data.")
```

Also remove `--poly` and `--kalshi` from the argument parser.

### Step 3: Remove certifi dependency

- [ ] **Update pyproject.toml**

The `certifi` dep was already removed in Task 1 when we rewrote the deps. Verify it is not listed. No change needed if Task 1 was done correctly.

### Step 4: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS. Tests for old sync providers (`test_kalshi.py`, `test_kalshi_exec.py`) import from `polyarb.execution.kalshi` (which still exists) and `polyarb.data.kalshi` (deleted). Tests that imported deleted modules will fail and should be removed.

- [ ] **Remove tests for deleted modules**

```bash
rm polyarb/tests/test_kalshi.py
```

Note: `test_kalshi_exec.py` imports from `polyarb.execution.kalshi` which still exists (we kept it — only the data provider was deleted). Verify it still passes:

Run: `pytest polyarb/tests/test_kalshi_exec.py -v`
Expected: PASS (KalshiAuth and sync KalshiClient are still in execution/kalshi.py).

- [ ] **Run all tests again**

Run: `pytest -v`
Expected: All remaining tests PASS.

- [ ] **Commit**

```bash
git add -A
git commit -m "Remove old sync data providers, scanner, and CLI

- Delete polyarb/data/live.py (replaced by async_live.py)
- Delete polyarb/data/kalshi.py (replaced by async_kalshi.py)
- Delete polyarb/engine/scanner.py (replaced by daemon scan loop)
- Delete polyarb/cli.py (replaced by client/cli.py)
- Delete test_kalshi.py (tested deleted data provider)
- Simplify --mock mode to standalone detection run
- execution/kalshi.py retained (KalshiAuth used by async client)"
```
