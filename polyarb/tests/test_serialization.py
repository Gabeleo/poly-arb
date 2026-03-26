"""Tests for to_dict() serialization on all model classes and MatchedPair."""

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


def _make_token(side: Side = Side.YES, mid: float = 0.60) -> Token:
    return Token(
        token_id="tok_abc",
        side=side,
        midpoint=mid,
        best_bid=mid - 0.01,
        best_ask=mid + 0.01,
    )


def _make_market(
    condition_id: str = "cond_1",
    question: str = "Will it rain?",
    end_date: datetime | None = None,
    platform: str = "polymarket",
) -> Market:
    return Market(
        condition_id=condition_id,
        question=question,
        yes_token=_make_token(Side.YES, 0.60),
        no_token=_make_token(Side.NO, 0.40),
        neg_risk=False,
        event_slug="rain-event",
        slug="will-it-rain",
        volume=1234.5,
        end_date=end_date,
        platform=platform,
    )


# ── Token ────────────────────────────────────────────────────


def test_token_to_dict():
    t = _make_token(Side.YES, 0.60)
    d = t.to_dict()
    assert d == {
        "token_id": "tok_abc",
        "side": "YES",
        "midpoint": 0.60,
        "best_bid": 0.59,
        "best_ask": 0.61,
    }


def test_token_to_dict_no_side():
    t = _make_token(Side.NO, 0.40)
    d = t.to_dict()
    assert d["side"] == "NO"
    assert d["midpoint"] == 0.40


# ── Market ───────────────────────────────────────────────────


def test_market_to_dict_with_end_date():
    dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    m = _make_market(end_date=dt)
    d = m.to_dict()
    assert d["condition_id"] == "cond_1"
    assert d["question"] == "Will it rain?"
    assert d["neg_risk"] is False
    assert d["event_slug"] == "rain-event"
    assert d["slug"] == "will-it-rain"
    assert d["volume"] == 1234.5
    assert d["end_date"] == "2025-06-15T12:00:00+00:00"
    assert d["platform"] == "polymarket"
    # Nested tokens
    assert d["yes_token"]["side"] == "YES"
    assert d["no_token"]["side"] == "NO"


def test_market_to_dict_without_end_date():
    m = _make_market(end_date=None)
    d = m.to_dict()
    assert d["end_date"] is None


# ── Event ────────────────────────────────────────────────────


def test_event_to_dict():
    m1 = _make_market(condition_id="c1", question="Q1")
    m2 = _make_market(condition_id="c2", question="Q2")
    e = Event(slug="evt-slug", title="My Event", markets=(m1, m2))
    d = e.to_dict()
    assert d["slug"] == "evt-slug"
    assert d["title"] == "My Event"
    assert len(d["markets"]) == 2
    assert d["markets"][0]["condition_id"] == "c1"
    assert d["markets"][1]["condition_id"] == "c2"


# ── Opportunity ──────────────────────────────────────────────


def test_opportunity_to_dict_with_event():
    m = _make_market()
    e = Event(slug="evt", title="Evt", markets=(m,))
    opp = Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(m,),
        event=e,
        expected_profit_per_share=0.05,
    )
    d = opp.to_dict()
    assert d["arb_type"] == "SINGLE_UNDERPRICE"
    assert len(d["markets"]) == 1
    assert d["event"]["slug"] == "evt"
    assert d["expected_profit_per_share"] == 0.05
    assert isinstance(d["key"], str) and len(d["key"]) == 12


def test_opportunity_to_dict_no_event():
    m = _make_market()
    opp = Opportunity(
        arb_type=ArbType.SINGLE_OVERPRICE,
        markets=(m,),
        event=None,
        expected_profit_per_share=0.03,
    )
    d = opp.to_dict()
    assert d["event"] is None


# ── Order ────────────────────────────────────────────────────


def test_order_to_dict():
    o = Order(
        token_id="tok_1",
        side=Side.YES,
        action=Action.BUY,
        price=0.55,
        size=10.0,
    )
    d = o.to_dict()
    assert d == {
        "token_id": "tok_1",
        "side": "YES",
        "action": "BUY",
        "price": 0.55,
        "size": 10.0,
    }


# ── OrderSet ─────────────────────────────────────────────────


def test_orderset_to_dict():
    m = _make_market()
    opp = Opportunity(
        arb_type=ArbType.SINGLE_UNDERPRICE,
        markets=(m,),
        expected_profit_per_share=0.05,
    )
    o1 = Order(token_id="t1", side=Side.YES, action=Action.BUY, price=0.55, size=10)
    o2 = Order(token_id="t2", side=Side.NO, action=Action.BUY, price=0.40, size=10)
    os_ = OrderSet(
        opportunity=opp,
        orders=[o1, o2],
        total_cost=9.50,
        expected_payout=10.0,
    )
    d = os_.to_dict()
    assert d["total_cost"] == 9.50
    assert d["expected_payout"] == 10.0
    assert d["expected_profit"] == 0.5
    assert len(d["orders"]) == 2
    assert d["orders"][0]["token_id"] == "t1"
    assert d["opportunity"]["arb_type"] == "SINGLE_UNDERPRICE"


# ── MatchedPair ──────────────────────────────────────────────


def test_matchedpair_to_dict():
    poly = _make_market(condition_id="poly_1", question="Will X happen?", platform="polymarket")
    kalshi = Market(
        condition_id="kalshi_1",
        question="Will X happen?",
        yes_token=Token(token_id="k_yes", side=Side.YES, midpoint=0.65, best_bid=0.64, best_ask=0.66),
        no_token=Token(token_id="k_no", side=Side.NO, midpoint=0.35, best_bid=0.34, best_ask=0.36),
        platform="kalshi",
    )
    mp = MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.85)
    d = mp.to_dict()
    assert d["poly_market"]["condition_id"] == "poly_1"
    assert d["kalshi_market"]["condition_id"] == "kalshi_1"
    assert d["confidence"] == 0.85
    assert isinstance(d["yes_spread"], float)
    assert isinstance(d["profit_buy_kalshi_yes"], float)
    assert isinstance(d["profit_buy_poly_yes"], float)
    # best_arb is a dict
    ba = d["best_arb"]
    assert "profit" in ba
    assert "kalshi_side" in ba
    assert "kalshi_desc" in ba
    assert "poly_desc" in ba
    assert "kalshi_price" in ba
