"""Tests for async data providers using httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest


# ── Polymarket mock data ──────────────────────────────────────


POLY_MARKETS_RESPONSE = [
    {
        "conditionId": "cond_abc",
        "question": "Will it rain tomorrow?",
        "outcomePrices": '["0.65","0.35"]',
        "clobTokenIds": '["tok_yes_1","tok_no_1"]',
        "bestBid": "0.63",
        "bestAsk": "0.67",
        "negRisk": False,
        "events": [{"slug": "weather-event"}],
        "slug": "will-it-rain",
        "volumeNum": 5000,
        "endDate": "2025-12-31T23:59:59Z",
    },
    {
        "conditionId": "cond_neg1",
        "question": "Candidate A wins?",
        "outcomePrices": '["0.40","0.60"]',
        "clobTokenIds": '["tok_yes_a","tok_no_a"]',
        "negRisk": True,
        "events": [{"slug": "election-2025"}],
        "slug": "candidate-a-wins",
        "volumeNum": 8000,
        "endDate": None,
    },
    {
        "conditionId": "cond_neg2",
        "question": "Candidate B wins?",
        "outcomePrices": '["0.55","0.45"]',
        "clobTokenIds": '["tok_yes_b","tok_no_b"]',
        "negRisk": True,
        "events": [{"slug": "election-2025"}],
        "slug": "candidate-b-wins",
        "volumeNum": 7000,
        "endDate": None,
    },
]


def poly_handler(request: httpx.Request) -> httpx.Response:
    """Mock handler for Polymarket Gamma API."""
    path = request.url.path
    if path == "/markets":
        return httpx.Response(200, json=POLY_MARKETS_RESPONSE)
    return httpx.Response(404)


# ── Kalshi mock data ──────────────────────────────────────────


KALSHI_EVENTS_RESPONSE = {
    "events": [
        {
            "event_ticker": "EVT_RAIN",
            "title": "Rain Tomorrow",
            "mutually_exclusive": False,
            "markets": [
                {
                    "ticker": "RAIN-YES",
                    "event_ticker": "EVT_RAIN",
                    "market_type": "binary",
                    "status": "active",
                    "yes_bid_dollars": "0.55",
                    "yes_ask_dollars": "0.60",
                    "no_bid_dollars": "0.40",
                    "no_ask_dollars": "0.45",
                    "yes_sub_title": "Will it rain?",
                    "volume_24h_fp": "3000",
                    "close_time": "2025-12-31T23:59:59Z",
                },
            ],
        },
        {
            "event_ticker": "EVT_ELECT",
            "title": "Election 2025",
            "mutually_exclusive": True,
            "markets": [
                {
                    "ticker": "ELECT-A",
                    "event_ticker": "EVT_ELECT",
                    "market_type": "binary",
                    "status": "active",
                    "yes_bid_dollars": "0.30",
                    "yes_ask_dollars": "0.35",
                    "no_bid_dollars": "0.65",
                    "no_ask_dollars": "0.70",
                    "yes_sub_title": "Candidate A",
                    "volume_24h_fp": "5000",
                    "close_time": None,
                },
                {
                    "ticker": "ELECT-B",
                    "event_ticker": "EVT_ELECT",
                    "market_type": "binary",
                    "status": "active",
                    "yes_bid_dollars": "0.60",
                    "yes_ask_dollars": "0.65",
                    "no_bid_dollars": "0.35",
                    "no_ask_dollars": "0.40",
                    "yes_sub_title": "Candidate B",
                    "volume_24h_fp": "6000",
                    "close_time": None,
                },
            ],
        },
    ],
}


def kalshi_handler(request: httpx.Request) -> httpx.Response:
    """Mock handler for Kalshi Trading API v2."""
    path = request.url.path
    # When base_url is set, the full path includes /trade-api/v2 prefix
    if path == "/events" or path.endswith("/events"):
        return httpx.Response(200, json=KALSHI_EVENTS_RESPONSE)
    return httpx.Response(404)


# ── Polymarket async provider tests ──────────────────────────


@pytest.mark.asyncio
async def test_poly_get_active_markets():
    from polyarb.data.async_live import AsyncLiveDataProvider

    transport = httpx.MockTransport(poly_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gamma-api.polymarket.com")
    provider = AsyncLiveDataProvider(limit=100, client=client)
    markets = await provider.get_active_markets()

    assert len(markets) == 3
    # Check first market parsed correctly
    rain = [m for m in markets if m.condition_id == "cond_abc"][0]
    assert rain.yes_token.midpoint == 0.65
    assert rain.no_token.midpoint == 0.35
    assert rain.condition_id == "cond_abc"
    assert rain.yes_token.best_bid == 0.63
    assert rain.yes_token.best_ask == 0.67
    assert rain.platform == "polymarket"
    assert rain.event_slug == "weather-event"

    await provider.close()


@pytest.mark.asyncio
async def test_poly_get_events():
    from polyarb.data.async_live import AsyncLiveDataProvider

    transport = httpx.MockTransport(poly_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gamma-api.polymarket.com")
    provider = AsyncLiveDataProvider(limit=100, client=client)
    events = await provider.get_events()

    # Only neg_risk markets get grouped into events
    assert len(events) == 1
    evt = events[0]
    assert evt.slug == "election-2025"
    assert len(evt.markets) == 2

    await provider.close()


@pytest.mark.asyncio
async def test_poly_search_markets():
    from polyarb.data.async_live import AsyncLiveDataProvider

    transport = httpx.MockTransport(poly_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gamma-api.polymarket.com")
    provider = AsyncLiveDataProvider(limit=100, client=client)
    results = await provider.search_markets("rain", limit=5)

    assert len(results) == 1
    assert "rain" in results[0].question.lower()

    await provider.close()


# ── Kalshi async provider tests ──────────────────────────────


@pytest.mark.asyncio
async def test_kalshi_get_active_markets():
    from polyarb.data.async_kalshi import AsyncKalshiDataProvider

    transport = httpx.MockTransport(kalshi_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.elections.kalshi.com/trade-api/v2")
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    markets = await provider.get_active_markets()

    # 3 binary active markets across 2 events
    assert len(markets) == 3
    rain = [m for m in markets if m.condition_id == "RAIN-YES"][0]
    assert rain.platform == "kalshi"
    assert rain.yes_token.midpoint == 0.575  # (0.55 + 0.60) / 2
    assert rain.yes_token.best_bid == 0.55
    assert rain.yes_token.best_ask == 0.60


@pytest.mark.asyncio
async def test_kalshi_neg_risk_detection():
    from polyarb.data.async_kalshi import AsyncKalshiDataProvider

    transport = httpx.MockTransport(kalshi_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.elections.kalshi.com/trade-api/v2")
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    markets = await provider.get_active_markets()

    # Rain event is NOT mutually_exclusive -> neg_risk=False
    rain = [m for m in markets if m.condition_id == "RAIN-YES"][0]
    assert rain.neg_risk is False

    # Election event IS mutually_exclusive with >1 market -> neg_risk=True
    elect_a = [m for m in markets if m.condition_id == "ELECT-A"][0]
    assert elect_a.neg_risk is True

    await provider.close()


@pytest.mark.asyncio
async def test_kalshi_get_events():
    from polyarb.data.async_kalshi import AsyncKalshiDataProvider

    transport = httpx.MockTransport(kalshi_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.elections.kalshi.com/trade-api/v2")
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    events = await provider.get_events()

    # Only the mutually_exclusive election event with >1 market
    assert len(events) == 1
    assert events[0].title == "Election 2025"
    assert len(events[0].markets) == 2

    await provider.close()


@pytest.mark.asyncio
async def test_kalshi_search_markets():
    from polyarb.data.async_kalshi import AsyncKalshiDataProvider

    transport = httpx.MockTransport(kalshi_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.elections.kalshi.com/trade-api/v2")
    provider = AsyncKalshiDataProvider(limit=100, client=client)
    results = await provider.search_markets("Rain", limit=5)

    assert len(results) == 1
    assert "rain" in results[0].question.lower()

    await provider.close()
