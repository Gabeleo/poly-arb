"""Tests for the cross-encoder HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from polyarb.matching.encoder_client import EncoderClient


# ── Mock transports ──────────────────────────────────────────


def _ok_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    n = len(body["pairs"])
    return httpx.Response(200, json={"scores": [0.85] * n})


def _error_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="internal server error")


def _bad_json_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"wrong_key": []})


def _wrong_count_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"scores": [0.5]})


def _health_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok"})


def _health_down(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"detail": "model not loaded"})


# ── score_pairs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_pairs_returns_scores():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    scores = await enc.score_pairs([("Will BTC hit $100k?", "Bitcoin above $100k?")])

    assert scores == [0.85]


@pytest.mark.asyncio
async def test_score_pairs_batch():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    pairs = [("a", "b"), ("c", "d"), ("e", "f")]
    scores = await enc.score_pairs(pairs)

    assert scores == [0.85, 0.85, 0.85]


@pytest.mark.asyncio
async def test_score_pairs_returns_none_on_http_error():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_error_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.score_pairs([("a", "b")]) is None


@pytest.mark.asyncio
async def test_score_pairs_returns_none_on_bad_json():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_bad_json_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.score_pairs([("a", "b")]) is None


@pytest.mark.asyncio
async def test_score_pairs_returns_none_on_wrong_count():
    """Encoder returns fewer scores than pairs sent."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(_wrong_count_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.score_pairs([("a", "b"), ("c", "d")]) is None


@pytest.mark.asyncio
async def test_score_pairs_returns_none_on_connection_error():
    def raise_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(raise_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.score_pairs([("a", "b")]) is None


# ── health ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_ok():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_health_ok))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.health() is True


@pytest.mark.asyncio
async def test_health_down():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_health_down))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.health() is False


@pytest.mark.asyncio
async def test_health_connection_error():
    def raise_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(raise_handler))
    enc = EncoderClient("http://encoder:8000", client=client)

    assert await enc.health() is False
