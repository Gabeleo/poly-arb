"""Tests for Pydantic API schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polyarb.api.schemas.requests import ConfigUpdate
from polyarb.api.schemas.responses import (
    ConfigResponse,
    ErrorResponse,
    HealthResponse,
    StatusResponse,
)


# ── ConfigUpdate ────────────────────────────────────────────


def test_config_update_valid_partial():
    m = ConfigUpdate(min_profit=0.02, scan_interval=3.0)
    assert m.min_profit == 0.02
    assert m.scan_interval == 3.0
    assert m.max_prob is None


def test_config_update_all_none():
    m = ConfigUpdate()
    dumped = m.model_dump(exclude_none=True)
    assert dumped == {}


def test_config_update_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ConfigUpdate(bogus_key=42)


def test_config_update_rejects_negative_min_profit():
    with pytest.raises(ValidationError):
        ConfigUpdate(min_profit=-0.01)


def test_config_update_allows_zero_min_profit():
    m = ConfigUpdate(min_profit=0.0)
    assert m.min_profit == 0.0


def test_config_update_rejects_max_prob_over_1():
    with pytest.raises(ValidationError):
        ConfigUpdate(max_prob=1.5)


def test_config_update_rejects_zero_scan_interval():
    with pytest.raises(ValidationError):
        ConfigUpdate(scan_interval=0)


def test_config_update_rejects_negative_bankroll():
    with pytest.raises(ValidationError):
        ConfigUpdate(bankroll=-1.0)


def test_config_update_coerces_string():
    """Pydantic v2 coerces compatible types."""
    m = ConfigUpdate(min_profit=0.02)
    assert isinstance(m.min_profit, float)


def test_config_update_model_dump():
    m = ConfigUpdate(min_profit=0.02, order_size=5.0)
    d = m.model_dump(exclude_none=True)
    assert d == {"min_profit": 0.02, "order_size": 5.0}


# ── Response models ─────────────────────────────────────────


def test_health_response():
    r = HealthResponse(healthy=True, checks={"scan_loop": "ok"})
    assert r.healthy is True


def test_status_response():
    r = StatusResponse(
        uptime_seconds=100.0, scan_count=5, connected_clients=2,
        match_count=10, opportunity_count=3, kelly_enabled=True,
        bankroll=1000.0, kelly_fraction=0.5, biencoder_enabled=False,
    )
    assert r.scan_count == 5


def test_config_response():
    r = ConfigResponse(
        min_profit=0.005, max_prob=0.95, scan_interval=10.0,
        order_size=10.0, kelly_fraction=0.5, max_position=100.0,
        bankroll=0.0, dedup_window=60, approval_timeout=120.0,
        digest_interval=3600.0, match_candidate_threshold=0.15,
        match_final_threshold=0.5,
    )
    assert r.min_profit == 0.005


def test_error_response():
    r = ErrorResponse(error="bad request")
    assert r.error == "bad request"
