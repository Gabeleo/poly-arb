"""Tests for positions repository, position tracker, and reconciliation."""

from __future__ import annotations

import os
import tempfile

import pytest

from polyarb.db.engine import create_engine
from polyarb.db.models import metadata
from polyarb.db.repositories.positions import SqlitePositionRepository
from polyarb.execution.positions import PositionTracker
from polyarb.execution.reconciliation import (
    Discrepancy,
    ReconciliationResult,
    reconcile,
)

# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    metadata.create_all(eng)
    yield eng
    eng.dispose()
    os.unlink(path)


@pytest.fixture
def repo(engine):
    return SqlitePositionRepository(engine)


@pytest.fixture
def tracker(repo):
    return PositionTracker(store=repo)


# ── SqlitePositionRepository ─────────────────────────────────


class TestPositionRepository:
    def test_open_position_returns_id(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        assert isinstance(pos_id, int)
        assert pos_id > 0

    def test_get_open_positions(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.open_position("polymarket", "C-1", "no", 5.0, 0.35)
        positions = repo.get_open_positions()
        assert len(positions) == 2
        assert positions[0]["platform"] == "kalshi"
        assert positions[1]["platform"] == "polymarket"

    def test_close_position(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.close_position(pos_id, realized_pnl=0.50)
        open_pos = repo.get_open_positions()
        assert len(open_pos) == 0
        all_pos = repo.get_all_positions()
        assert len(all_pos) == 1
        assert all_pos[0]["closed_at"] is not None
        assert all_pos[0]["realized_pnl"] == 0.50

    def test_close_position_without_pnl(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.close_position(pos_id)
        all_pos = repo.get_all_positions()
        assert all_pos[0]["realized_pnl"] is None

    def test_get_position_by_market(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        pos = repo.get_position_by_market("kalshi", "T-1", "yes")
        assert pos is not None
        assert pos["quantity"] == 10.0

    def test_get_position_by_market_returns_none_when_closed(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.close_position(pos_id)
        assert repo.get_position_by_market("kalshi", "T-1", "yes") is None

    def test_get_position_by_market_returns_none_when_missing(self, repo):
        assert repo.get_position_by_market("kalshi", "T-1", "yes") is None

    def test_get_position_size(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        assert repo.get_position_size("kalshi", "T-1") == 10.0

    def test_get_position_size_zero_when_no_position(self, repo):
        assert repo.get_position_size("kalshi", "T-1") == 0.0

    def test_get_total_exposure(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.40)  # 4.0
        repo.open_position("polymarket", "C-1", "no", 5.0, 0.30)  # 1.5
        assert repo.get_total_exposure() == pytest.approx(5.5)

    def test_get_total_exposure_zero_when_empty(self, repo):
        assert repo.get_total_exposure() == 0.0

    def test_get_total_exposure_excludes_closed(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.40)
        repo.close_position(pos_id)
        repo.open_position("polymarket", "C-1", "no", 5.0, 0.30)
        assert repo.get_total_exposure() == pytest.approx(1.5)

    def test_execution_id_stored(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42, execution_id="exec-1")
        pos = repo.get_position_by_market("kalshi", "T-1", "yes")
        assert pos is not None
        assert pos["execution_id"] == "exec-1"

    def test_get_all_positions_includes_closed(self, repo):
        pos_id = repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.close_position(pos_id)
        repo.open_position("polymarket", "C-1", "no", 5.0, 0.35)
        all_pos = repo.get_all_positions()
        assert len(all_pos) == 2


# ── PositionTracker ──────────────────────────────────────────


class TestPositionTracker:
    def test_record_fill_opens_position(self, tracker, repo):
        pos_id = tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.42, "exec-1")
        assert pos_id > 0
        open_pos = repo.get_open_positions()
        assert len(open_pos) == 1
        assert open_pos[0]["quantity"] == 10.0

    def test_record_fill_averages_in(self, tracker, repo):
        """Second fill on same market averages price and adds quantity."""
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.40)
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.50)
        open_pos = repo.get_open_positions()
        assert len(open_pos) == 1
        assert open_pos[0]["quantity"] == 20.0
        assert open_pos[0]["avg_price"] == pytest.approx(0.45)

    def test_record_fill_different_markets(self, tracker, repo):
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.40)
        tracker.record_fill("polymarket", "C-1", "no", 5.0, 0.35)
        assert len(repo.get_open_positions()) == 2

    def test_close_returns_realized_pnl(self, tracker, repo):
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.40)
        pnl = tracker.close("kalshi", "T-1", "yes", settlement_price=0.60)
        assert pnl == pytest.approx(2.0)  # (0.60 - 0.40) * 10
        assert len(repo.get_open_positions()) == 0

    def test_close_without_price(self, tracker, repo):
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.40)
        pnl = tracker.close("kalshi", "T-1", "yes")
        assert pnl is None
        assert len(repo.get_open_positions()) == 0

    def test_close_missing_position_returns_none(self, tracker):
        pnl = tracker.close("kalshi", "T-1", "yes", settlement_price=0.60)
        assert pnl is None

    def test_close_negative_pnl(self, tracker):
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.60)
        pnl = tracker.close("kalshi", "T-1", "yes", settlement_price=0.40)
        assert pnl == pytest.approx(-2.0)

    def test_get_open(self, tracker):
        tracker.record_fill("kalshi", "T-1", "yes", 10.0, 0.42)
        assert len(tracker.get_open()) == 1


# ── Reconciliation ───────────────────────────────────────────


class FakeKalshiPositions:
    """Fake client returning configured position data."""

    def __init__(self, positions_by_ticker: dict[str, list[dict]] | None = None):
        self._positions = positions_by_ticker or {}

    async def get_positions(self, ticker: str = "") -> list[dict]:
        return self._positions.get(ticker, [])


class FakeKalshiError:
    """Fake client that raises on get_positions."""

    async def get_positions(self, ticker: str = "") -> list[dict]:
        raise RuntimeError("Connection refused")


class FakeRiskRecorder:
    """Captures recorded risk events."""

    def __init__(self):
        self.events: list[dict] = []

    def record_risk_event(
        self,
        event_type: str,
        severity: str,
        details: str,
        execution_id: str | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "severity": severity,
                "details": details,
                "execution_id": execution_id,
            }
        )


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_clean_reconciliation(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiPositions({"T-1": [{"ticker": "T-1", "quantity": 10}]})
        result = await reconcile(repo, kalshi_client=client)
        assert result.clean
        assert result.positions_checked == 1
        assert len(result.discrepancies) == 0

    @pytest.mark.asyncio
    async def test_missing_on_exchange(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiPositions({})  # exchange returns nothing
        result = await reconcile(repo, kalshi_client=client)
        assert not result.clean
        assert len(result.discrepancies) == 1
        assert result.discrepancies[0].discrepancy_type == "missing_on_exchange"
        assert result.discrepancies[0].internal_qty == 10.0
        assert result.discrepancies[0].exchange_qty == 0.0

    @pytest.mark.asyncio
    async def test_quantity_mismatch(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiPositions({"T-1": [{"ticker": "T-1", "quantity": 7}]})
        result = await reconcile(repo, kalshi_client=client)
        assert not result.clean
        assert result.discrepancies[0].discrepancy_type == "quantity_mismatch"
        assert result.discrepancies[0].exchange_qty == 7.0

    @pytest.mark.asyncio
    async def test_matching_quantity_is_clean(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiPositions({"T-1": [{"ticker": "T-1", "quantity": 10}]})
        result = await reconcile(repo, kalshi_client=client)
        assert result.clean

    @pytest.mark.asyncio
    async def test_skips_polymarket_positions(self, repo):
        """Polymarket positions are not reconciled (no API support)."""
        repo.open_position("polymarket", "C-1", "no", 5.0, 0.35)
        client = FakeKalshiPositions({})
        result = await reconcile(repo, kalshi_client=client)
        assert result.clean  # poly position not checked
        assert result.positions_checked == 1

    @pytest.mark.asyncio
    async def test_no_client_skips(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        result = await reconcile(repo, kalshi_client=None)
        assert result.clean
        assert result.positions_checked == 1

    @pytest.mark.asyncio
    async def test_exchange_error_continues(self, repo):
        """API failure for one ticker doesn't stop reconciliation."""
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiError()
        result = await reconcile(repo, kalshi_client=client)
        # Error is logged, not a discrepancy
        assert result.clean

    @pytest.mark.asyncio
    async def test_discrepancies_recorded_as_risk_events(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        client = FakeKalshiPositions({})
        recorder = FakeRiskRecorder()
        await reconcile(repo, kalshi_client=client, risk_recorder=recorder)
        assert len(recorder.events) == 1
        assert recorder.events[0]["event_type"] == "position_discrepancy"
        assert recorder.events[0]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_multiple_positions(self, repo):
        repo.open_position("kalshi", "T-1", "yes", 10.0, 0.42)
        repo.open_position("kalshi", "T-2", "no", 5.0, 0.60)
        client = FakeKalshiPositions(
            {
                "T-1": [{"ticker": "T-1", "quantity": 10}],
                "T-2": [{"ticker": "T-2", "quantity": 5}],
            }
        )
        result = await reconcile(repo, kalshi_client=client)
        assert result.clean
        assert result.positions_checked == 2


# ── Discrepancy dataclass ────────────────────────────────────


class TestDiscrepancy:
    def test_to_dict(self):
        d = Discrepancy(
            platform="kalshi",
            ticker="T-1",
            side="yes",
            internal_qty=10.0,
            exchange_qty=7.0,
            discrepancy_type="quantity_mismatch",
        )
        result = d.to_dict()
        assert result["platform"] == "kalshi"
        assert result["type"] == "quantity_mismatch"

    def test_frozen(self):
        d = Discrepancy("k", "T", "y", 1.0, 0.0, "missing_on_exchange")
        with pytest.raises(AttributeError):
            d.platform = "poly"  # type: ignore[misc]


class TestReconciliationResult:
    def test_clean_when_no_discrepancies(self):
        r = ReconciliationResult()
        assert r.clean

    def test_not_clean_with_discrepancy(self):
        r = ReconciliationResult()
        r.discrepancies.append(Discrepancy("k", "T", "y", 1.0, 0.0, "missing_on_exchange"))
        assert not r.clean
