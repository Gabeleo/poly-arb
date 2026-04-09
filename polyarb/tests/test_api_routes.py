"""Tests for API route modules."""

from __future__ import annotations

from datetime import UTC

from starlette.testclient import TestClient

from polyarb.api.app import create_app
from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import ArbType, Market, Opportunity, Side, Token

# ── Helpers ────────────────────────────────────────────────


def _mkt(cid: str, platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=f"Will {cid}?",
        yes_token=Token("y", Side.YES, 0.50, 0.49, 0.51),
        no_token=Token("n", Side.NO, 0.50, 0.49, 0.51),
        platform=platform,
    )


def _pair(poly_cid: str, kalshi_cid: str) -> MatchedPair:
    return MatchedPair(
        poly_market=_mkt(poly_cid, "polymarket"),
        kalshi_market=_mkt(kalshi_cid, "kalshi"),
        confidence=0.8,
    )


def _opp(cid: str) -> Opportunity:
    return Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(_mkt(cid),),
        expected_profit_per_share=0.05,
    )


def _client(state: State | None = None) -> TestClient:
    if state is None:
        state = State(config=Config())
    return TestClient(create_app(state))


# ── Health routes ──────────────────────────────────────────


def test_health_starting():
    client = _client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["checks"]["scan_loop"] == "starting"


def test_health_live_always_200():
    assert _client().get("/health/live").status_code == 200


def test_health_ready_before_first_scan():
    state = State(config=Config())
    client = _client(state)
    resp = client.get("/health/ready")
    assert resp.status_code == 503


def test_health_ready_after_scan():
    from datetime import datetime

    state = State(config=Config())
    state.scan_count = 1
    state.last_scan_at = datetime.now(UTC)
    client = _client(state)
    resp = client.get("/health/ready")
    assert resp.status_code == 200


def test_metrics_returns_prometheus():
    resp = _client().get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


def test_status_returns_fields():
    resp = _client().get("/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "uptime_seconds",
        "scan_count",
        "connected_clients",
        "match_count",
        "opportunity_count",
    ):
        assert key in data


# ── Match routes ────────────────────────────────────────────


def test_matches_empty():
    assert _client().get("/matches").json() == []


def test_matches_populated():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1"), _pair("p2", "k2")]
    data = _client(state).get("/matches").json()
    assert len(data) == 2


def test_match_detail_valid_id():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    resp = _client(state).get("/matches/1")
    assert resp.status_code == 200
    assert "poly_market" in resp.json()


def test_match_detail_not_found():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    assert _client(state).get("/matches/99").status_code == 404


# ── Opportunity routes ──────────────────────────────────────


def test_opportunities_empty():
    assert _client().get("/opportunities").json() == []


def test_opportunities_populated():
    state = State(config=Config())
    state.opportunities = [_opp("c1"), _opp("c2")]
    data = _client(state).get("/opportunities").json()
    assert len(data) == 2


# ── Config routes ───────────────────────────────────────────


def test_get_config_returns_all_fields():
    data = _client().get("/config").json()
    assert "min_profit" in data
    assert "scan_interval" in data
    assert "match_final_threshold" in data


def test_post_config_updates():
    state = State(config=Config())
    client = _client(state)
    resp = client.post("/config", json={"min_profit": 0.02, "scan_interval": 3.0})
    assert resp.status_code == 200
    assert state.config.min_profit == 0.02
    assert state.config.scan_interval == 3.0


def test_post_config_rejects_unknown_fields():
    resp = _client().post("/config", json={"bogus_field": 42})
    assert resp.status_code == 400


def test_post_config_rejects_invalid_values():
    resp = _client().post("/config", json={"scan_interval": -1.0})
    assert resp.status_code == 400


def test_post_config_pydantic_error_structure():
    resp = _client().post("/config", json={"max_prob": 5.0})
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


# ── Execution routes ────────────────────────────────────────


def test_execute_returns_503():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    resp = _client(state).post("/execute/1")
    assert resp.status_code == 503
    assert "disabled" in resp.json()["error"].lower()


# ── WebSocket routes ────────────────────────────────────────


def test_ws_connect_disconnect():
    state = State(config=Config())
    client = _client(state)
    assert len(state.ws_clients) == 0
    with client.websocket_connect("/ws") as _ws:
        assert len(state.ws_clients) == 1
    assert len(state.ws_clients) == 0
