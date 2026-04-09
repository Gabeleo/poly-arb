"""Tests for OpenAPI spec generation."""

from __future__ import annotations

from starlette.testclient import TestClient

from polyarb.api.app import create_app
from polyarb.config import Config
from polyarb.daemon.state import State


def _client() -> TestClient:
    return TestClient(create_app(State(config=Config())))


def test_openapi_returns_valid_spec():
    resp = _client().get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["openapi"] == "3.0.0"
    assert "info" in data
    assert "paths" in data


def test_openapi_contains_all_paths():
    data = _client().get("/openapi.json").json()
    expected = [
        "/health",
        "/health/live",
        "/health/ready",
        "/health/deep",
        "/status",
        "/matches",
        "/matches/{id}",
        "/opportunities",
        "/config",
        "/execute/{id}",
        "/metrics",
    ]
    for path in expected:
        assert path in data["paths"], f"Missing path: {path}"


def test_openapi_contains_config_update_schema():
    data = _client().get("/openapi.json").json()
    schemas = data["components"]["schemas"]
    assert "ConfigUpdate" in schemas
    props = schemas["ConfigUpdate"]["properties"]
    assert "min_profit" in props
    assert "scan_interval" in props


def test_openapi_contains_response_schemas():
    data = _client().get("/openapi.json").json()
    schemas = data["components"]["schemas"]
    for name in ("HealthResponse", "StatusResponse", "ConfigResponse", "ErrorResponse"):
        assert name in schemas, f"Missing schema: {name}"
