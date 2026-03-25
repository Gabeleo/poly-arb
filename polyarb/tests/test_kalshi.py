"""Tests for Kalshi data provider parsing logic."""

from polyarb.data.kalshi import _parse_market, _parse_price
from polyarb.models import Side


# ── _parse_price ────────────────────────────────────────────


def test_parse_price_valid():
    assert _parse_price("0.42") == 0.42
    assert _parse_price("0.01") == 0.01
    assert _parse_price("0.99") == 0.99


def test_parse_price_none_and_empty():
    assert _parse_price(None) is None
    assert _parse_price("") is None


def test_parse_price_zero_is_none():
    assert _parse_price("0.00") is None
    assert _parse_price("0") is None


def test_parse_price_garbage():
    assert _parse_price("abc") is None


def test_parse_price_rejects_at_or_above_one():
    """Kalshi prices are probabilities in (0, 1.0) — 1.0 is not valid."""
    assert _parse_price("1.0") is None
    assert _parse_price("42") is None
    assert _parse_price("1.50") is None
    assert _parse_price("100") is None


# ── _parse_market: happy path ───────────────────────────────


def _raw_market(**overrides) -> dict:
    """Build a valid Kalshi market dict with sensible defaults."""
    base = {
        "ticker": "TEST-MKT",
        "event_ticker": "TEST-EVT",
        "market_type": "binary",
        "status": "active",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.44",
        "no_bid_dollars": "0.56",
        "no_ask_dollars": "0.60",
        "volume_24h_fp": "1000.00",
        "close_time": "2025-12-31T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_basic_parse():
    m = _parse_market(_raw_market(), event_title="Test Event")
    assert m is not None
    assert m.condition_id == "TEST-MKT"
    assert m.event_slug == "TEST-EVT"
    assert m.question == "Test Event"
    assert m.platform == "kalshi"
    assert m.yes_token.side == Side.YES
    assert m.yes_token.midpoint == 0.42  # (0.40 + 0.44) / 2
    assert m.yes_token.best_bid == 0.40
    assert m.yes_token.best_ask == 0.44
    assert m.no_token.side == Side.NO
    assert m.no_token.midpoint == 0.58  # (0.56 + 0.60) / 2
    assert m.no_token.best_bid == 0.56
    assert m.no_token.best_ask == 0.60
    assert m.volume == 1000.0
    assert m.end_date is not None


def test_token_ids_use_ticker():
    m = _parse_market(_raw_market(ticker="MY-T"), event_title="X")
    assert m.yes_token.token_id == "MY-T:yes"
    assert m.no_token.token_id == "MY-T:no"


def test_url_points_to_kalshi():
    m = _parse_market(_raw_market(), event_title="X")
    assert "kalshi.com" in m.url
    assert "TEST-EVT" in m.url


# ── Question construction ───────────────────────────────────


def test_subtitle_appended_when_informative():
    raw = _raw_market(yes_sub_title="Above $100k")
    m = _parse_market(raw, event_title="Bitcoin Price")
    assert m.question == "Bitcoin Price — Above $100k"


def test_generic_subtitle_ignored():
    for sub in ("Yes", "yes", "No", "no", ""):
        raw = _raw_market(yes_sub_title=sub)
        m = _parse_market(raw, event_title="Will it rain?")
        assert m.question == "Will it rain?"


def test_no_event_title_uses_subtitle():
    raw = _raw_market(yes_sub_title="Some outcome")
    m = _parse_market(raw, event_title="")
    assert m.question == "Some outcome"


def test_no_title_or_subtitle_uses_ticker():
    raw = _raw_market()
    m = _parse_market(raw, event_title="")
    assert m.question == "TEST-MKT"


# ── Filtering ───────────────────────────────────────────────


def test_non_binary_skipped():
    assert _parse_market(_raw_market(market_type="scalar")) is None


def test_inactive_skipped():
    assert _parse_market(_raw_market(status="closed")) is None
    assert _parse_market(_raw_market(status="settled")) is None
    assert _parse_market(_raw_market(status="determined")) is None


def test_missing_ticker_skipped():
    assert _parse_market(_raw_market(ticker="")) is None


def test_no_price_data_skipped():
    raw = _raw_market()
    del raw["yes_bid_dollars"]
    del raw["yes_ask_dollars"]
    del raw["no_bid_dollars"]
    del raw["no_ask_dollars"]
    assert _parse_market(raw) is None


# ── Price fallbacks ─────────────────────────────────────────


def test_fallback_to_last_price():
    raw = _raw_market(last_price_dollars="0.60")
    del raw["yes_bid_dollars"]
    del raw["yes_ask_dollars"]
    del raw["no_bid_dollars"]
    del raw["no_ask_dollars"]
    m = _parse_market(raw, event_title="Test")
    assert m is not None
    assert m.yes_token.midpoint == 0.60
    assert m.no_token.midpoint == 0.40


def test_fallback_to_bid_only():
    raw = _raw_market(yes_bid_dollars="0.55")
    del raw["yes_ask_dollars"]
    del raw["no_bid_dollars"]
    del raw["no_ask_dollars"]
    m = _parse_market(raw, event_title="Test")
    assert m is not None
    assert m.yes_token.midpoint == 0.55


def test_default_bid_ask_when_missing():
    """When only last_price is available, bid/ask default to ±0.01."""
    raw = _raw_market(last_price_dollars="0.50")
    del raw["yes_bid_dollars"]
    del raw["yes_ask_dollars"]
    del raw["no_bid_dollars"]
    del raw["no_ask_dollars"]
    m = _parse_market(raw, event_title="T")
    assert m.yes_token.best_bid == 0.49
    assert m.yes_token.best_ask == 0.51
    assert m.no_token.best_bid == 0.49
    assert m.no_token.best_ask == 0.51


# ── neg_risk flag ───────────────────────────────────────────


def test_neg_risk_propagated():
    m = _parse_market(_raw_market(), event_title="E", neg_risk=True)
    assert m.neg_risk is True


def test_neg_risk_default_false():
    m = _parse_market(_raw_market(), event_title="E")
    assert m.neg_risk is False
