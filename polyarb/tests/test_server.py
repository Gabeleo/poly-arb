"""Tests for polyarb.daemon.server Starlette app."""

from starlette.testclient import TestClient

from polyarb.config import Config
from polyarb.daemon.server import create_app
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


def _make_client(state: State | None = None, kalshi_client=None) -> TestClient:
    if state is None:
        state = State(config=Config())
    app = create_app(state, kalshi_client=kalshi_client)
    return TestClient(app)


# ── GET /status ────────────────────────────────────────────


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


# ── GET /matches ───────────────────────────────────────────


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


# ── GET /config ────────────────────────────────────────────


def test_get_config():
    client = _make_client()
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "min_profit" in data
    assert "scan_interval" in data


# ── POST /config ───────────────────────────────────────────


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


# ── POST /execute/{id} ────────────────────────────────────


def test_execute_no_kalshi_client():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]
    client = _make_client(state, kalshi_client=None)

    resp = client.post("/execute/1")
    assert resp.status_code == 409


def test_execute_invalid_id():
    state = State(config=Config())
    state.matches = [_pair("p1", "k1")]

    class FakeKalshi:
        pass

    client = _make_client(state, kalshi_client=FakeKalshi())
    resp = client.post("/execute/99")
    assert resp.status_code == 404
