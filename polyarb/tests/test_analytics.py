"""Tests for polyarb.analytics — P&L, performance, signals, reports."""

from __future__ import annotations

import pytest
from sqlalchemy import insert

from polyarb.analytics.performance import SqlitePerformanceProvider
from polyarb.analytics.pnl import PnLSummary, SqlitePnLProvider
from polyarb.analytics.reports import ReportGenerator
from polyarb.analytics.signals import SqliteSignalProvider, _pearson
from polyarb.db.engine import create_engine
from polyarb.db.models import (
    execution_legs,
    executions,
    kalshi_snapshots,
    match_snapshots,
    metadata,
    polymarket_snapshots,
    positions,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    yield eng
    eng.dispose()


# ── Test data helpers ──────────────────────────────────────


def _insert_position(
    engine,
    platform,
    ticker,
    side,
    qty,
    avg_price,
    closed_at=None,
    realized_pnl=None,
    execution_id=None,
):
    with engine.begin() as conn:
        conn.execute(
            insert(positions).values(
                platform=platform,
                ticker=ticker,
                side=side,
                quantity=qty,
                avg_price=avg_price,
                opened_at="2026-04-01T00:00:00",
                closed_at=closed_at,
                realized_pnl=realized_pnl,
                execution_id=execution_id,
            )
        )


def _insert_poly_snapshot(engine, cid, scan_ts, yes_bid, no_bid):
    with engine.begin() as conn:
        conn.execute(
            insert(polymarket_snapshots).values(
                scan_ts=scan_ts,
                condition_id=cid,
                question="Test?",
                event_slug="",
                yes_bid=yes_bid,
                yes_ask=yes_bid + 0.02,
                no_bid=no_bid,
                no_ask=no_bid + 0.02,
                volume=1000,
                volume_24h=100,
            )
        )


def _insert_kalshi_snapshot(engine, ticker, scan_ts, yes_bid, no_bid):
    with engine.begin() as conn:
        conn.execute(
            insert(kalshi_snapshots).values(
                scan_ts=scan_ts,
                ticker=ticker,
                question="Test?",
                event_ticker="",
                yes_bid=yes_bid,
                yes_ask=yes_bid + 0.02,
                no_bid=no_bid,
                no_ask=no_bid + 0.02,
                volume=1000,
                volume_24h=100,
            )
        )


def _insert_execution(engine, execution_id, match_key, status, profit=None, completed_at=None):
    with engine.begin() as conn:
        conn.execute(
            insert(executions).values(
                execution_id=execution_id,
                created_at="2026-04-01T00:00:00",
                match_key=match_key,
                status=status,
                leg_count=2,
                profit=profit,
                completed_at=completed_at,
            )
        )


def _insert_leg(
    engine, execution_id, leg_index, platform, ticker, side, price, size, status="filled"
):
    with engine.begin() as conn:
        conn.execute(
            insert(execution_legs).values(
                execution_id=execution_id,
                leg_index=leg_index,
                platform=platform,
                ticker=ticker,
                side=side,
                action="buy",
                price=price,
                size=size,
                status=status,
            )
        )


def _insert_match_snapshot(
    engine, poly_cid, kalshi_ticker, confidence, scan_ts="2026-04-01T00:00:00"
):
    with engine.begin() as conn:
        conn.execute(
            insert(match_snapshots).values(
                scan_ts=scan_ts,
                scan_id="s1",
                poly_condition_id=poly_cid,
                kalshi_ticker=kalshi_ticker,
                poly_question="Test?",
                kalshi_question="Test?",
                confidence=confidence,
                poly_yes_bid=0.40,
                poly_yes_ask=0.42,
                poly_no_bid=0.58,
                poly_no_ask=0.60,
                kalshi_yes_bid=0.41,
                kalshi_yes_ask=0.43,
                kalshi_no_bid=0.57,
                kalshi_no_ask=0.59,
                raw_delta=0.02,
            )
        )


# ── PnL tests ─────────────────────────────────────────────


class TestPnLSummary:
    def test_empty_db(self, engine):
        pnl = SqlitePnLProvider(engine)
        s = pnl.summary()
        assert s.total_realized == 0.0
        assert s.total_unrealized == 0.0
        assert s.open_positions == 0
        assert s.closed_positions == 0

    def test_realized_from_closed_positions(self, engine):
        _insert_position(
            engine, "kalshi", "K1", "yes", 10, 0.40, closed_at="2026-04-02", realized_pnl=5.0
        )
        _insert_position(
            engine, "kalshi", "K2", "no", 5, 0.60, closed_at="2026-04-02", realized_pnl=-2.0
        )
        pnl = SqlitePnLProvider(engine)
        s = pnl.summary()
        assert s.total_realized == 3.0
        assert s.closed_positions == 2
        assert s.open_positions == 0

    def test_unrealized_from_open_positions(self, engine):
        _insert_position(engine, "polymarket", "P1", "yes", 10, 0.40)
        _insert_poly_snapshot(engine, "P1", "2026-04-01T00:00:00", 0.50, 0.50)
        pnl = SqlitePnLProvider(engine)
        s = pnl.summary()
        assert s.total_unrealized == pytest.approx(1.0)  # (0.50 - 0.40) * 10
        assert s.open_positions == 1

    def test_unrealized_kalshi(self, engine):
        _insert_position(engine, "kalshi", "K1", "no", 20, 0.55)
        _insert_kalshi_snapshot(engine, "K1", "2026-04-01T00:00:00", 0.40, 0.60)
        pnl = SqlitePnLProvider(engine)
        s = pnl.summary()
        assert s.total_unrealized == pytest.approx(1.0)  # (0.60 - 0.55) * 20

    def test_no_snapshot_means_zero_unrealized(self, engine):
        _insert_position(engine, "polymarket", "P1", "yes", 10, 0.40)
        pnl = SqlitePnLProvider(engine)
        s = pnl.summary()
        assert s.total_unrealized == 0.0

    def test_to_dict(self, engine):
        s = PnLSummary(
            total_realized=5.0, total_unrealized=2.5, open_positions=1, closed_positions=2
        )
        d = s.to_dict()
        assert d["total"] == 7.5
        assert d["open_positions"] == 1

    def test_total_property(self):
        s = PnLSummary(
            total_realized=10.0, total_unrealized=-3.0, open_positions=0, closed_positions=0
        )
        assert s.total == 7.0


class TestDailyPnL:
    def test_daily_from_executions(self, engine):
        _insert_execution(
            engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01T12:00:00"
        )
        _insert_execution(
            engine, "e2", "P1:K1", "completed", profit=-2.0, completed_at="2026-04-01T18:00:00"
        )
        _insert_execution(
            engine, "e3", "P2:K2", "completed", profit=3.0, completed_at="2026-04-02T06:00:00"
        )
        pnl = SqlitePnLProvider(engine)
        daily = pnl.daily(lookback_days=365)
        assert len(daily) == 2
        assert daily[0].date == "2026-04-01"
        assert daily[0].realized == pytest.approx(3.0)
        assert daily[0].trade_count == 2
        assert daily[1].date == "2026-04-02"
        assert daily[1].realized == pytest.approx(3.0)

    def test_daily_excludes_failed(self, engine):
        _insert_execution(
            engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01T12:00:00"
        )
        _insert_execution(
            engine, "e2", "P1:K1", "failed", profit=None, completed_at="2026-04-01T13:00:00"
        )
        pnl = SqlitePnLProvider(engine)
        daily = pnl.daily(lookback_days=365)
        assert len(daily) == 1
        assert daily[0].trade_count == 1

    def test_daily_empty(self, engine):
        pnl = SqlitePnLProvider(engine)
        assert pnl.daily() == []


class TestPerPairPnL:
    def test_open_with_snapshot(self, engine):
        _insert_position(engine, "polymarket", "P1", "yes", 10, 0.40)
        _insert_poly_snapshot(engine, "P1", "2026-04-01T00:00:00", 0.45, 0.55)
        _insert_poly_snapshot(engine, "P1", "2026-04-02T00:00:00", 0.50, 0.50)  # latest
        pnl = SqlitePnLProvider(engine)
        pairs = pnl.per_pair()
        assert len(pairs) == 1
        assert pairs[0].current_price == pytest.approx(0.50)
        assert pairs[0].unrealized == pytest.approx(1.0)
        assert pairs[0].is_open is True

    def test_closed_position(self, engine):
        _insert_position(
            engine, "kalshi", "K1", "yes", 5, 0.30, closed_at="2026-04-02", realized_pnl=2.5
        )
        pnl = SqlitePnLProvider(engine)
        pairs = pnl.per_pair()
        assert len(pairs) == 1
        assert pairs[0].is_open is False
        assert pairs[0].realized == pytest.approx(2.5)
        assert pairs[0].current_price is None
        assert pairs[0].unrealized == 0.0


# ── Performance tests ──────────────────────────────────────


class TestPerformance:
    def test_empty_db(self, engine):
        perf = SqlitePerformanceProvider(engine)
        s = perf.summary()
        assert s.total_trades == 0
        assert s.total_profit == 0.0
        assert s.by_pair == []
        assert s.by_platform == []

    def test_by_pair(self, engine):
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01")
        _insert_execution(
            engine, "e2", "P1:K1", "completed", profit=-1.0, completed_at="2026-04-01"
        )
        _insert_execution(engine, "e3", "P2:K2", "completed", profit=3.0, completed_at="2026-04-01")
        perf = SqlitePerformanceProvider(engine)
        s = perf.summary()
        assert s.total_trades == 3
        assert s.total_profit == pytest.approx(7.0)
        assert s.win_count == 2
        assert s.loss_count == 1
        assert len(s.by_pair) == 2
        # Sorted by total_profit desc
        assert s.by_pair[0].match_key == "P1:K1"
        assert s.by_pair[0].total_profit == pytest.approx(4.0)
        assert s.by_pair[0].win_count == 1
        assert s.by_pair[0].loss_count == 1

    def test_by_platform(self, engine):
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=4.0, completed_at="2026-04-01")
        _insert_leg(engine, "e1", 0, "kalshi", "K1", "yes", 0.40, 10)
        _insert_leg(engine, "e1", 1, "polymarket", "P1", "no", 0.55, 10)
        perf = SqlitePerformanceProvider(engine)
        s = perf.summary()
        assert len(s.by_platform) == 2
        # Each leg gets half the profit
        for p in s.by_platform:
            assert p.total_profit == pytest.approx(2.0)
            assert p.trade_count == 1

    def test_win_rate(self, engine):
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01")
        _insert_execution(engine, "e2", "P1:K1", "completed", profit=3.0, completed_at="2026-04-01")
        _insert_execution(
            engine, "e3", "P1:K1", "completed", profit=-1.0, completed_at="2026-04-01"
        )
        perf = SqlitePerformanceProvider(engine)
        s = perf.summary()
        assert s.win_rate == pytest.approx(2 / 3)

    def test_excludes_failed(self, engine):
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01")
        _insert_execution(engine, "e2", "P1:K1", "failed", profit=None, completed_at="2026-04-01")
        perf = SqlitePerformanceProvider(engine)
        s = perf.summary()
        assert s.total_trades == 1

    def test_to_dict(self, engine):
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01")
        perf = SqlitePerformanceProvider(engine)
        d = perf.summary().to_dict()
        assert "total_trades" in d
        assert "by_pair" in d
        assert "by_platform" in d
        assert "win_rate" in d


# ── Signals tests ──────────────────────────────────────────


class TestSignals:
    def test_empty_db(self, engine):
        sig = SqliteSignalProvider(engine)
        report = sig.analyze()
        assert report.total_matches == 0
        assert report.total_traded == 0
        assert report.correlation is None

    def test_buckets_populated(self, engine):
        _insert_match_snapshot(engine, "P1", "K1", confidence=0.55)
        _insert_match_snapshot(engine, "P2", "K2", confidence=0.75, scan_ts="2026-04-01T00:01:00")
        _insert_match_snapshot(engine, "P3", "K3", confidence=0.92, scan_ts="2026-04-01T00:02:00")
        sig = SqliteSignalProvider(engine)
        report = sig.analyze()
        assert report.total_matches == 3
        # P1:K1 in [0.5,0.6), P2:K2 in [0.7,0.8), P3:K3 in [0.9,1.01)
        bucket_counts = {b.bucket_min: b.match_count for b in report.buckets}
        assert bucket_counts[0.5] == 1
        assert bucket_counts[0.7] == 1
        assert bucket_counts[0.9] == 1

    def test_correlates_with_profit(self, engine):
        # High confidence → high profit
        _insert_match_snapshot(engine, "P1", "K1", confidence=0.55)
        _insert_match_snapshot(engine, "P2", "K2", confidence=0.85, scan_ts="2026-04-01T00:01:00")
        _insert_execution(engine, "e1", "P1:K1", "completed", profit=1.0, completed_at="2026-04-01")
        _insert_execution(engine, "e2", "P2:K2", "completed", profit=5.0, completed_at="2026-04-01")
        sig = SqliteSignalProvider(engine)
        report = sig.analyze()
        assert report.total_traded == 2
        # Positive correlation: higher confidence → higher profit
        assert report.correlation is not None
        assert report.correlation > 0

    def test_to_dict(self, engine):
        sig = SqliteSignalProvider(engine)
        d = sig.analyze().to_dict()
        assert "total_matches" in d
        assert "buckets" in d
        assert "correlation" in d


class TestPearson:
    def test_perfect_positive(self):
        assert _pearson([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert _pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)

    def test_no_correlation(self):
        # orthogonal
        r = _pearson([1, 0, -1, 0], [0, 1, 0, -1])
        assert r == pytest.approx(0.0)

    def test_too_few_points(self):
        assert _pearson([1], [1]) is None
        assert _pearson([], []) is None

    def test_constant_returns_none(self):
        assert _pearson([1, 1, 1], [1, 2, 3]) is None


# ── Reports tests ──────────────────────────────────────────


class TestReports:
    def test_daily_report(self, engine):
        _insert_execution(
            engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01T12:00:00"
        )
        gen = ReportGenerator(engine)
        report = gen.daily()
        assert report.period == "daily"
        d = report.to_dict()
        assert "pnl" in d
        assert "performance" in d
        assert "signal_quality" in d

    def test_weekly_report(self, engine):
        gen = ReportGenerator(engine)
        report = gen.weekly()
        assert report.period == "weekly"

    def test_format_text(self, engine):
        _insert_execution(
            engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01T12:00:00"
        )
        gen = ReportGenerator(engine)
        report = gen.daily()
        text = gen.format_text(report)
        assert "DAILY REPORT" in text
        assert "P&L" in text
        assert "Trades:" in text

    def test_empty_db_report(self, engine):
        gen = ReportGenerator(engine)
        report = gen.daily()
        text = gen.format_text(report)
        assert "P&L: $+0.00" in text


# ── API route tests ────────────────────────────────────────


class TestAnalyticsRoutes:
    @pytest.fixture
    def shared_engine(self):
        """File-backed temp DB so Starlette test client (separate thread) can access it."""
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        eng = create_engine(f"sqlite:///{path}")
        metadata.create_all(eng)
        yield eng
        eng.dispose()
        os.unlink(path)

    def _make_client(self, engine):
        from starlette.testclient import TestClient

        from polyarb.analytics.performance import SqlitePerformanceProvider
        from polyarb.analytics.pnl import SqlitePnLProvider
        from polyarb.api.app import create_app
        from polyarb.config import Config
        from polyarb.daemon.state import State

        state = State(config=Config())
        app = create_app(
            state,
            pnl_provider=SqlitePnLProvider(engine),
            performance_provider=SqlitePerformanceProvider(engine),
        )
        return TestClient(app)

    def test_pnl_endpoint(self, shared_engine):
        _insert_position(
            shared_engine, "kalshi", "K1", "yes", 10, 0.40, closed_at="2026-04-02", realized_pnl=3.0
        )
        client = self._make_client(shared_engine)
        resp = client.get("/analytics/pnl")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_realized"] == 3.0
        assert "daily_breakdown" in data
        assert "positions" in data

    def test_pnl_with_days_param(self, shared_engine):
        client = self._make_client(shared_engine)
        resp = client.get("/analytics/pnl?days=7")
        assert resp.status_code == 200

    def test_performance_endpoint(self, shared_engine):
        _insert_execution(
            shared_engine, "e1", "P1:K1", "completed", profit=5.0, completed_at="2026-04-01"
        )
        client = self._make_client(shared_engine)
        resp = client.get("/analytics/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 1
        assert data["total_profit"] == 5.0

    def test_analytics_not_configured(self):
        from starlette.testclient import TestClient

        from polyarb.api.app import create_app
        from polyarb.config import Config
        from polyarb.daemon.state import State

        state = State(config=Config())
        app = create_app(state)
        client = TestClient(app)
        assert client.get("/analytics/pnl").status_code == 503
        assert client.get("/analytics/performance").status_code == 503
