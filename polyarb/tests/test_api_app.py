"""Tests for polyarb.api.app — app factory."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from polyarb.api.app import create_app
from polyarb.config import Config
from polyarb.daemon.state import State


def _make_app(**kwargs) -> Starlette:
    state = State(config=Config())
    return create_app(state, **kwargs)


def test_create_app_returns_starlette():
    app = _make_app()
    assert isinstance(app, Starlette)


def test_app_state_populated():
    state = State(config=Config())
    app = create_app(state, kalshi_client="kc", audit_repo="ar")
    assert app.state.daemon_state is state
    assert app.state.kalshi_client == "kc"
    assert app.state.audit_repo == "ar"


def test_all_core_routes_accessible():
    client = TestClient(_make_app())
    for path in ["/health", "/status", "/matches", "/opportunities", "/config"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_openapi_route():
    client = TestClient(_make_app())
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["openapi"] == "3.0.0"


def test_backward_compat_make_client():
    """Existing _make_client pattern from test_server.py still works."""
    state = State(config=Config())
    app = create_app(state, kalshi_client=None, api_key=None)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
