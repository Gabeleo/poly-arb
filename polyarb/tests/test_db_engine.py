"""Tests for polyarb.db.engine — connection factory."""

from __future__ import annotations

import os

from sqlalchemy import text

from polyarb.db.engine import create_engine, get_database_url


def test_default_url_is_sqlite():
    """get_database_url returns SQLite URL when DATABASE_URL is not set."""
    env = os.environ.pop("DATABASE_URL", None)
    try:
        url = get_database_url()
        assert url.startswith("sqlite:///")
    finally:
        if env is not None:
            os.environ["DATABASE_URL"] = env


def test_env_override(monkeypatch):
    """DATABASE_URL environment variable overrides the default."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    assert get_database_url() == "postgresql://localhost/test"


def test_sqlite_engine_creation(tmp_path):
    """create_engine with SQLite returns a working engine."""
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
    assert result == 1
    engine.dispose()


def test_wal_mode_on_sqlite(tmp_path):
    """SQLite engine has WAL journal mode."""
    db_file = tmp_path / "wal_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"
    engine.dispose()


def test_foreign_keys_enabled(tmp_path):
    """SQLite engine has foreign_keys=ON."""
    db_file = tmp_path / "fk_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk == 1
    engine.dispose()


def test_memory_engine():
    """In-memory SQLite engine works."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 42")).scalar()
    assert result == 42
    engine.dispose()
