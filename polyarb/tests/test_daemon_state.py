"""Tests for polyarb.daemon.state.State."""

from datetime import datetime

from polyarb.config import Config
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


# ── Creation defaults ──────────────────────────────────────


def test_state_defaults():
    state = State(config=Config())
    assert state.scan_count == 0
    assert state.matches == []
    assert state.opportunities == []
    assert state.last_scan_at is None
    assert isinstance(state.started_at, datetime)


# ── update_matches ─────────────────────────────────────────


def test_update_matches_returns_new_only():
    state = State(config=Config())
    p1 = _pair("poly1", "kalshi1")
    p2 = _pair("poly2", "kalshi2")

    new = state.update_matches([p1, p2])
    assert len(new) == 2

    # Same matches again → nothing new
    new = state.update_matches([p1, p2])
    assert len(new) == 0


def test_update_matches_replaces_full_list():
    state = State(config=Config())
    p1 = _pair("poly1", "kalshi1")
    p2 = _pair("poly2", "kalshi2")

    state.update_matches([p1])
    assert len(state.matches) == 1

    # Replace with different set, detects new addition
    new = state.update_matches([p1, p2])
    assert len(state.matches) == 2
    assert len(new) == 1  # only p2 is new


def test_update_matches_increments_scan_count():
    state = State(config=Config())
    assert state.scan_count == 0

    state.update_matches([])
    assert state.scan_count == 1

    state.update_matches([])
    assert state.scan_count == 2


def test_update_matches_sets_last_scan_at():
    state = State(config=Config())
    assert state.last_scan_at is None

    state.update_matches([])
    assert state.last_scan_at is not None
    assert isinstance(state.last_scan_at, datetime)


# ── update_opportunities ───────────────────────────────────


def test_update_opportunities_dedup():
    state = State(config=Config())
    o1 = _opp("cid1")
    o2 = _opp("cid2")

    new = state.update_opportunities([o1, o2])
    assert len(new) == 2

    # Same opps again → nothing new
    new = state.update_opportunities([o1, o2])
    assert len(new) == 0


def test_update_opportunities_replaces_list():
    state = State(config=Config())
    o1 = _opp("cid1")
    o2 = _opp("cid2")

    state.update_opportunities([o1])
    assert len(state.opportunities) == 1

    state.update_opportunities([o1, o2])
    assert len(state.opportunities) == 2


# ── broadcast ──────────────────────────────────────────────


class FakeWS:
    """Minimal WebSocket fake for broadcast tests."""

    def __init__(self, *, fail: bool = False):
        self.messages: list[dict] = []
        self._fail = fail

    async def send_json(self, data: dict) -> None:
        if self._fail:
            raise Exception("connection closed")
        self.messages.append(data)


async def test_broadcast_sends_to_all_clients():
    state = State(config=Config())
    ws1 = FakeWS()
    ws2 = FakeWS()
    state.ws_clients.add(ws1)
    state.ws_clients.add(ws2)

    await state.broadcast({"type": "test", "data": 42})

    assert len(ws1.messages) == 1
    assert ws1.messages[0] == {"type": "test", "data": 42}
    assert len(ws2.messages) == 1


async def test_broadcast_removes_disconnected_clients():
    state = State(config=Config())
    good = FakeWS()
    bad = FakeWS(fail=True)
    state.ws_clients.add(good)
    state.ws_clients.add(bad)

    await state.broadcast({"type": "ping"})

    assert good in state.ws_clients
    assert bad not in state.ws_clients
    assert len(good.messages) == 1


# ── status_dict ────────────────────────────────────────────


def test_status_dict():
    state = State(config=Config())
    state.ws_clients.add(FakeWS())
    state.update_matches([_pair("p1", "k1")])

    d = state.status_dict()
    assert "uptime_seconds" in d
    assert d["uptime_seconds"] >= 0
    assert d["scan_count"] == 1
    assert d["connected_clients"] == 1
    assert d["match_count"] == 1
    assert d["opportunity_count"] == 0
