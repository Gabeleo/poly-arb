"""Tests for API middleware — auth and rate limiting."""

from __future__ import annotations

import os
from unittest.mock import patch

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
    try:
        with client.websocket_connect("/ws"):
            pass
        assert False, "should have raised"
    except Exception:
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
