"""Tests for API middleware — auth and rate limiting."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from polyarb.api.app import create_app
from polyarb.config import Config
from polyarb.daemon.state import State


def _make_client(api_key: str | None = None, rate_env: dict | None = None) -> TestClient:
    state = State(config=Config())
    if rate_env:
        with patch.dict(os.environ, rate_env):
            app = create_app(state, api_key=api_key)
    else:
        app = create_app(state, api_key=api_key)
    return TestClient(app)


# ── Auth middleware ─────────────────────────────────────────


def test_auth_blocks_unauthenticated_config_post():
    client = _make_client(api_key="secret")
    resp = client.post("/config", json={"min_profit": 0.01})
    assert resp.status_code == 401


def test_auth_allows_authenticated_config_post():
    client = _make_client(api_key="secret")
    resp = client.post(
        "/config",
        json={"min_profit": 0.01},
        headers={"X-API-Key": "secret"},
    )
    assert resp.status_code == 200


def test_auth_allows_public_reads():
    client = _make_client(api_key="secret")
    assert client.get("/status").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/matches").status_code == 200


def test_auth_blocks_ws_without_key():
    state = State(config=Config())
    app = create_app(state, api_key="secret")
    client = TestClient(app)
    # WebSocket without api_key param should be rejected with 4003
    with pytest.raises(Exception), client.websocket_connect("/ws"):  # noqa: B017
        pass


# ── Rate limit middleware ──────────────────────────────────


def test_rate_limit_allows_under_limit():
    client = _make_client()
    resp = client.get("/status")
    assert resp.status_code == 200


def test_rate_limit_returns_429_when_exceeded():
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        # First 2 should pass (burst=2)
        assert client.get("/status").status_code == 200
        assert client.get("/status").status_code == 200
        # Third should be rate limited
        resp = client.get("/status")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


def test_rate_limit_exempts_health():
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "1", "RATE_LIMIT_BURST": "1"}):
        app = create_app(state)
        client = TestClient(app)

        # Use up the single token on /status
        assert client.get("/status").status_code == 200
        # /health should still work (exempt)
        assert client.get("/health").status_code == 200
        assert client.get("/health/live").status_code == 200


def test_rate_limit_exempts_metrics():
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "1", "RATE_LIMIT_BURST": "1"}):
        app = create_app(state)
        client = TestClient(app)

        # Use up the single token
        assert client.get("/status").status_code == 200
        # /metrics should still work (exempt)
        assert client.get("/metrics").status_code == 200


# ── Middleware ordering ───────────────────────────────────


def test_request_id_present_on_rate_limited_429():
    """RequestId is outermost — even 429 responses get X-Request-ID."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "1", "RATE_LIMIT_BURST": "1"}):
        app = create_app(state)
        client = TestClient(app)

        # Exhaust burst
        client.get("/status")
        # This should be 429
        resp = client.get("/status")
        assert resp.status_code == 429
        assert "x-request-id" in resp.headers


def test_request_id_present_on_auth_401():
    """RequestId runs before auth — 401 responses get X-Request-ID."""
    client = _make_client(api_key="secret")
    resp = client.post("/config", json={"min_profit": 0.01})
    assert resp.status_code == 401
    assert "x-request-id" in resp.headers


def test_request_id_present_with_auth_enabled():
    """RequestId runs first even when full auth stack is active."""
    client = _make_client(api_key="secret")
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers


# ── Proxy IP extraction ──────────────────────────────────


def test_xff_single_ip_rate_limits_correctly():
    """X-Forwarded-For single IP: separate buckets per IP."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        # Exhaust bucket for 10.0.0.1
        assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
        assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
        assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429

        # Different IP should still be allowed
        assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_xff_multiple_ips_uses_leftmost():
    """X-Forwarded-For with multiple IPs: rate limit keys on leftmost."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        # Both requests have same leftmost IP (203.0.113.50)
        assert (
            client.get("/status", headers={"X-Forwarded-For": "203.0.113.50, 10.0.0.1"}).status_code
            == 200
        )
        assert (
            client.get("/status", headers={"X-Forwarded-For": "203.0.113.50, 10.0.0.2"}).status_code
            == 200
        )
        # Third from same leftmost should be rate limited
        assert (
            client.get("/status", headers={"X-Forwarded-For": "203.0.113.50, 10.0.0.3"}).status_code
            == 429
        )


def test_x_real_ip_fallback():
    """X-Real-IP used when X-Forwarded-For is absent."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        assert client.get("/status", headers={"X-Real-IP": "10.0.0.5"}).status_code == 200
        assert client.get("/status", headers={"X-Real-IP": "10.0.0.5"}).status_code == 200
        assert client.get("/status", headers={"X-Real-IP": "10.0.0.5"}).status_code == 429

        # Different X-Real-IP gets its own bucket
        assert client.get("/status", headers={"X-Real-IP": "10.0.0.6"}).status_code == 200


def test_no_proxy_headers_uses_scope_client():
    """Without proxy headers, falls back to scope['client'] (existing behavior)."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        # TestClient sets scope["client"] to ("testclient", ...)
        assert client.get("/status").status_code == 200
        assert client.get("/status").status_code == 200
        assert client.get("/status").status_code == 429


def test_trusted_proxy_count_strips_spoofed():
    """TRUSTED_PROXY_COUNT=1 takes second-from-right, ignoring spoofed leftmost."""
    state = State(config=Config())
    with patch.dict(
        os.environ,
        {
            "RATE_LIMIT_PER_MIN": "2",
            "RATE_LIMIT_BURST": "2",
            "TRUSTED_PROXY_COUNT": "1",
        },
    ):
        app = create_app(state)
        client = TestClient(app)

        # "spoofed, real, proxy" — with TRUSTED_PROXY_COUNT=1, uses "real"
        hdr = {"X-Forwarded-For": "spoofed, real, proxy"}
        assert client.get("/status", headers=hdr).status_code == 200
        assert client.get("/status", headers=hdr).status_code == 200
        assert client.get("/status", headers=hdr).status_code == 429

        # Different "real" IP gets its own bucket
        hdr2 = {"X-Forwarded-For": "spoofed, other_real, proxy"}
        assert client.get("/status", headers=hdr2).status_code == 200


def test_trusted_proxy_count_zero_uses_leftmost():
    """TRUSTED_PROXY_COUNT=0 behaves like unset — uses leftmost entry."""
    state = State(config=Config())
    with patch.dict(
        os.environ,
        {
            "RATE_LIMIT_PER_MIN": "2",
            "RATE_LIMIT_BURST": "2",
            "TRUSTED_PROXY_COUNT": "0",
        },
    ):
        app = create_app(state)
        client = TestClient(app)

        hdr = {"X-Forwarded-For": "leftmost, middle, rightmost"}
        assert client.get("/status", headers=hdr).status_code == 200
        assert client.get("/status", headers=hdr).status_code == 200
        assert client.get("/status", headers=hdr).status_code == 429


def test_xff_whitespace_handling():
    """Whitespace in X-Forwarded-For entries is stripped."""
    state = State(config=Config())
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_BURST": "2"}):
        app = create_app(state)
        client = TestClient(app)

        # "10.0.0.1 , 10.0.0.2" — extracted IP should be "10.0.0.1" (stripped)
        assert (
            client.get("/status", headers={"X-Forwarded-For": "  10.0.0.1 , 10.0.0.2"}).status_code
            == 200
        )
        assert (
            client.get("/status", headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}).status_code
            == 200
        )
        # Same IP after stripping — should share bucket
        assert (
            client.get("/status", headers={"X-Forwarded-For": "10.0.0.1 , 10.0.0.2"}).status_code
            == 429
        )
