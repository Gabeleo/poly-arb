import sqlite3
from datetime import datetime, timezone

import pytest

from polyarb.models import Market, Side, Token
from polyarb.recorder.db import RecorderDB


def _poly_market(
    cid: str = "0xabc",
    question: str = "Will BTC hit 150k?",
    yes_bid: float = 0.42,
    yes_ask: float = 0.44,
    volume: float = 50_000,
    volume_24h: float = 1_000,
) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(f"{cid}_y", Side.YES, (yes_bid + yes_ask) / 2, yes_bid, yes_ask),
        no_token=Token(f"{cid}_n", Side.NO, 1 - (yes_bid + yes_ask) / 2, 1 - yes_ask, 1 - yes_bid),
        event_slug="btc-150k",
        volume=volume,
        volume_24h=volume_24h,
        end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        platform="polymarket",
    )


def _kalshi_market(
    ticker: str = "KXBTC-150K",
    question: str = "Bitcoin above 150k?",
    yes_bid: float = 0.39,
    yes_ask: float = 0.43,
    volume: float = 30_000,
    volume_24h: float = 500,
) -> Market:
    return Market(
        condition_id=ticker,
        question=question,
        yes_token=Token(f"{ticker}:yes", Side.YES, (yes_bid + yes_ask) / 2, yes_bid, yes_ask),
        no_token=Token(f"{ticker}:no", Side.NO, 1 - (yes_bid + yes_ask) / 2, 1 - yes_ask, 1 - yes_bid),
        event_slug="KXBTC",
        volume=volume,
        volume_24h=volume_24h,
        end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        platform="kalshi",
    )


def test_schema_creation(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    conn = sqlite3.connect(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "polymarket_snapshots" in tables
    assert "kalshi_snapshots" in tables
    conn.close()
    db.close()


def test_insert_polymarket(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    markets = [_poly_market(), _poly_market(cid="0xdef", question="ETH 10k?")]
    count = db.insert_polymarket("2026-04-06T12:00:00Z", markets)
    assert count == 2
    assert db.market_count()["polymarket"] == 2
    db.close()


def test_insert_kalshi(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    markets = [_kalshi_market(), _kalshi_market(ticker="KXETH-10K")]
    count = db.insert_kalshi("2026-04-06T12:00:00Z", markets)
    assert count == 2
    assert db.market_count()["kalshi"] == 2
    db.close()


def test_filter_low_volume(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    markets = [
        _poly_market(volume=50_000, volume_24h=100),   # passes
        _poly_market(cid="low", volume=5_000, volume_24h=100),  # below 10k floor
    ]
    count = db.insert_polymarket("2026-04-06T12:00:00Z", markets)
    assert count == 1
    db.close()


def test_filter_zero_24h_volume(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    markets = [
        _poly_market(volume=50_000, volume_24h=100),  # passes
        _poly_market(cid="stale", volume=50_000, volume_24h=0),  # no recent trades
    ]
    count = db.insert_polymarket("2026-04-06T12:00:00Z", markets)
    assert count == 1
    db.close()


def test_dedup_same_scan(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    m = _poly_market()
    count1 = db.insert_polymarket("2026-04-06T12:00:00Z", [m])
    count2 = db.insert_polymarket("2026-04-06T12:00:00Z", [m])
    assert count1 == 1
    assert count2 == 0  # duplicate ignored, return value reflects actual inserts
    assert db.market_count()["polymarket"] == 1
    db.close()


def test_different_scans_same_market(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    m = _poly_market()
    db.insert_polymarket("2026-04-06T12:00:00Z", [m])
    db.insert_polymarket("2026-04-06T12:00:30Z", [m])
    assert db.market_count()["polymarket"] == 2
    db.close()


def test_scan_count(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    db.insert_polymarket("2026-04-06T12:00:00Z", [_poly_market()])
    db.insert_polymarket("2026-04-06T12:00:30Z", [_poly_market()])
    db.insert_kalshi("2026-04-06T12:00:00Z", [_kalshi_market()])
    counts = db.scan_count()
    assert counts["polymarket"] == 2
    assert counts["kalshi"] == 1
    db.close()


def test_prices_stored_correctly(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    m = _poly_market(yes_bid=0.42, yes_ask=0.44)
    db.insert_polymarket("2026-04-06T12:00:00Z", [m])

    conn = sqlite3.connect(tmp_path / "test.db")
    row = conn.execute("SELECT yes_bid, yes_ask, no_bid, no_ask FROM polymarket_snapshots").fetchone()
    assert abs(row[0] - 0.42) < 1e-9   # yes_bid
    assert abs(row[1] - 0.44) < 1e-9   # yes_ask
    assert abs(row[2] - 0.56) < 1e-9   # no_bid = 1 - yes_ask
    assert abs(row[3] - 0.58) < 1e-9   # no_ask = 1 - yes_bid
    conn.close()
    db.close()


def test_partial_insert_one_platform_empty(tmp_path):
    db = RecorderDB(tmp_path / "test.db")
    ts = "2026-04-06T12:00:00Z"
    poly_count = db.insert_polymarket(ts, [_poly_market()])
    kalshi_count = db.insert_kalshi(ts, [])
    assert poly_count == 1
    assert kalshi_count == 0
    assert db.market_count() == {"polymarket": 1, "kalshi": 0}
    db.close()


# ── Async tests for record_once ────────────────────────────────────


class _MockProvider:
    """Minimal AsyncDataProvider for testing."""

    def __init__(self, markets: list[Market]) -> None:
        self._markets = markets

    async def get_active_markets(self) -> list[Market]:
        return self._markets

    async def get_events(self):
        return []

    async def search_markets(self, query: str, limit: int = 5):
        return []

    async def close(self) -> None:
        pass


class _FailingProvider:
    """Provider that always raises on fetch."""

    async def get_active_markets(self):
        raise ConnectionError("network down")

    async def get_events(self):
        return []

    async def search_markets(self, query: str, limit: int = 5):
        return []

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_record_once(tmp_path):
    from polyarb.recorder.recorder import record_once

    db = RecorderDB(tmp_path / "test.db")
    poly = _MockProvider([_poly_market()])
    kalshi = _MockProvider([_kalshi_market()])

    result = await record_once(poly, kalshi, db)
    assert result["polymarket"] == 1
    assert result["kalshi"] == 1
    assert "scan_ts" in result
    assert db.market_count() == {"polymarket": 1, "kalshi": 1}
    db.close()


@pytest.mark.asyncio
async def test_record_once_partial_failure(tmp_path):
    from polyarb.recorder.recorder import record_once

    db = RecorderDB(tmp_path / "test.db")
    poly = _MockProvider([_poly_market()])
    kalshi = _FailingProvider()

    result = await record_once(poly, kalshi, db)
    assert result["polymarket"] == 1
    assert result["kalshi"] == 0
    assert db.market_count() == {"polymarket": 1, "kalshi": 0}
    db.close()


@pytest.mark.asyncio
async def test_record_once_both_fail(tmp_path):
    from polyarb.recorder.recorder import record_once

    db = RecorderDB(tmp_path / "test.db")
    result = await record_once(_FailingProvider(), _FailingProvider(), db)
    assert result["polymarket"] == 0
    assert result["kalshi"] == 0
    assert db.market_count() == {"polymarket": 0, "kalshi": 0}
    db.close()
