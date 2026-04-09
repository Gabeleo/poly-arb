"""Tests for polyarb API Starlette app."""

from starlette.testclient import TestClient

from polyarb.config import Config
from polyarb.api.app import create_app
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import ArbType, Market, Opportunity, Side, Token


# ── Helpers ─────────────────────────────────────────────────


def _mkt(cid: str, question: str = "Q", platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y", Side.YES, 0.50, 0.49, 0.51),
        no_token=Token("n", Side.NO, 0.50, 0.49, 0.51),
        platform=platform,
    )


def _pair(poly_cid: str, kalshi_cid: str) -> MatchedPair:
    return MatchedPair(
        poly_market=_mkt(poly_cid, platform="polymarket"),
        kalshi_market=_mkt(kalshi_cid, platform="kalshi"),
        confidence=0.8,
    )


def _opp(cid: str) -> Opportunity:
    m = _mkt(cid)
    return Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(m,),
        expected_profit_per_share=0.05,
    )


def _make_state() -> State:
    return State(config=Config())


def _make_client(
    state: State | None = None,
    kalshi_client=None,
    api_key: str | None = None,
) -> TestClient:
    if state is None:
        state = _make_state()
    app = create_app(state, kalshi_client=kalshi_client, api_key=api_key)
    return TestClient(app)


# ── GET /health ───────────────────────────────────────────


def test_health_starting():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["scan_loop"] == "starting"


# ── GET /status ────��───────────────────────────────────────


def test_status_returns_fields():
    client = _make_client()
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "uptime_seconds" in data
    assert "scan_count" in data
    assert "connected_clients" in data
    assert "match_count" in data
    assert "opportunity_count" in data


# ── GET /matches ─────────────────��─────────────────────────


def test_matches_empty():
    client = _make_client()
    resp = client.get("/matches")
    assert resp.status_code == 200
    assert resp.json() == []


def test_matches_populated():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1"), _pair("p2", "k2")]
    client = _make_client(state)

    resp = client.get("/matches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


# ── GET /matches/{id} ─────────────────────────────────────


def test_match_detail_valid_id():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    client = _make_client(state)

    resp = client.get("/matches/1")
    assert resp.status_code == 200
    data = resp.json()
    assert "poly_market" in data
    assert "kalshi_market" in data


def test_match_detail_invalid_id():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    client = _make_client(state)

    resp = client.get("/matches/99")
    assert resp.status_code == 404


def test_match_detail_zero_id():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    client = _make_client(state)

    resp = client.get("/matches/0")
    assert resp.status_code == 404


# ── GET /opportunities ─────────────────────────────────────


def test_opportunities_empty():
    client = _make_client()
    resp = client.get("/opportunities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_opportunities_populated():
    state = State(config=Config())
    state.opportunities = [_opp("c1"), _opp("c2")]
    client = _make_client(state)

    resp = client.get("/opportunities")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


# ── GET /config ────��───────────────────────────────────────


def test_get_config():
    client = _make_client()
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "min_profit" in data
    assert "scan_interval" in data


# ── POST /config ──────���────────────────────────────────────


def test_post_config_updates():
    state = State(config=Config())
    client = _make_client(state)

    resp = client.post("/config", json={"min_profit": 0.02, "scan_interval": 3.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["min_profit"] == 0.02
    assert data["scan_interval"] == 3.0
    # Verify state was actually updated
    assert state.config.min_profit == 0.02


def test_post_config_rejects_unknown_keys():
    client = _make_client()
    resp = client.post("/config", json={"bogus_key": 42})
    assert resp.status_code == 400


def test_post_config_rejects_negative_values():
    state = _make_state()
    app = create_app(state)
    client = TestClient(app)
    resp = client.post("/config", json={"scan_interval": -1.0})
    assert resp.status_code == 400


def test_post_config_rejects_zero_order_size():
    client = _make_client()
    resp = client.post("/config", json={"order_size": 0})
    assert resp.status_code == 400


def test_post_config_rejects_negative_min_profit():
    client = _make_client()
    resp = client.post("/config", json={"min_profit": -0.01})
    assert resp.status_code == 400


def test_post_config_allows_zero_min_profit():
    client = _make_client()
    resp = client.post("/config", json={"min_profit": 0.0})
    assert resp.status_code == 200


def test_post_config_rejects_zero_dedup_window():
    client = _make_client()
    resp = client.post("/config", json={"dedup_window": 0})
    assert resp.status_code == 400


# ── POST /execute/{id} ────────────────────────────────────


def test_execute_returns_503():
    """Execution is blocked until both platform legs are implemented."""
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    client = _make_client(state)

    resp = client.post("/execute/1")
    assert resp.status_code == 503
    assert "disabled" in resp.json()["error"].lower()


# ── API key auth ──────────────────────────────────────────


def test_auth_blocks_unauthenticated_config_post():
    client = _make_client(api_key="secret123")
    resp = client.post("/config", json={"min_profit": 0.01})
    assert resp.status_code == 401


def test_auth_allows_authenticated_config_post():
    client = _make_client(api_key="secret123")
    resp = client.post(
        "/config",
        json={"min_profit": 0.01},
        headers={"X-API-Key": "secret123"},
    )
    assert resp.status_code == 200


def test_auth_allows_public_reads():
    """GET endpoints are public even with auth enabled."""
    client = _make_client(api_key="secret123")
    assert client.get("/status").status_code == 200
    assert client.get("/matches").status_code == 200
    assert client.get("/opportunities").status_code == 200
    assert client.get("/config").status_code == 200
    assert client.get("/health").status_code == 200


# ── WebSocket /ws ───────��────────────────────────────────


def test_ws_connect_and_disconnect():
    """WS client is added to state.ws_clients on connect, removed on disconnect."""
    state = State(config=Config())
    client = _make_client(state)

    assert len(state.ws_clients) == 0

    with client.websocket_connect("/ws") as ws:
        # After connect, the client should be tracked
        assert len(state.ws_clients) == 1

    # After disconnect, the client should be removed
    assert len(state.ws_clients) == 0
