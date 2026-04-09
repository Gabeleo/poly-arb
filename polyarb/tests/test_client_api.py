"""Tests for polyarb.client.api DaemonClient using httpx.MockTransport."""

import json

import httpx

from polyarb.client.api import DaemonClient

# ── Mock transport ──────────────────────────────────────────


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Route by path + method and return canned JSON."""
    path = request.url.path
    method = request.method

    if method == "GET" and path == "/status":
        return httpx.Response(200, json={"scan_count": 5, "uptime_seconds": 120.0})

    if method == "GET" and path == "/matches":
        return httpx.Response(
            200,
            json=[
                {"id": 1, "confidence": 0.85},
                {"id": 2, "confidence": 0.72},
            ],
        )

    if method == "GET" and path == "/matches/1":
        return httpx.Response(
            200,
            json={"id": 1, "confidence": 0.85, "poly_market": {}, "kalshi_market": {}},
        )

    if method == "GET" and path == "/matches/99":
        return httpx.Response(404, json={"error": "not found"})

    if method == "GET" and path == "/opportunities":
        return httpx.Response(200, json=[{"arb_type": "single_underprice", "profit": 0.03}])

    if method == "GET" and path == "/config":
        return httpx.Response(200, json={"scan_interval": 5.0, "min_profit": 0.005})

    if method == "POST" and path == "/config":
        body = json.loads(request.content)
        merged = {"scan_interval": 5.0, "min_profit": 0.005}
        merged.update(body)
        return httpx.Response(200, json=merged)

    if method == "POST" and path == "/execute/1":
        return httpx.Response(409, json={"error": "no kalshi client connected"})

    return httpx.Response(404, json={"error": "not found"})


def _make_client() -> DaemonClient:
    transport = httpx.MockTransport(_mock_handler)
    http = httpx.Client(base_url="http://testserver", transport=transport)
    return DaemonClient(client=http)


# ── Tests ───────────────────────────────────────────────────


def test_get_status():
    dc = _make_client()
    result = dc.get_status()
    assert isinstance(result, dict)
    assert result["scan_count"] == 5
    assert result["uptime_seconds"] == 120.0
    dc.close()


def test_get_matches():
    dc = _make_client()
    result = dc.get_matches()
    assert isinstance(result, list)
    assert len(result) == 2
    dc.close()


def test_get_match():
    dc = _make_client()
    result = dc.get_match(1)
    assert isinstance(result, dict)
    assert result["confidence"] == 0.85
    dc.close()


def test_get_match_not_found():
    dc = _make_client()
    result = dc.get_match(99)
    assert result is None
    dc.close()


def test_get_opportunities():
    dc = _make_client()
    result = dc.get_opportunities()
    assert isinstance(result, list)
    assert len(result) == 1
    dc.close()


def test_get_config():
    dc = _make_client()
    result = dc.get_config()
    assert isinstance(result, dict)
    assert result["scan_interval"] == 5.0
    dc.close()


def test_set_config():
    dc = _make_client()
    result = dc.set_config({"scan_interval": 3.0})
    assert isinstance(result, dict)
    assert result["scan_interval"] == 3.0
    dc.close()


def test_execute_not_connected():
    dc = _make_client()
    result = dc.execute(1)
    assert isinstance(result, dict)
    assert "error" in result
    dc.close()
