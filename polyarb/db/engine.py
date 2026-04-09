"""Connection factory: creates SQLAlchemy engines from DATABASE_URL."""

from __future__ import annotations

import os

from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine

DEFAULT_URL = "sqlite:///polyarb.db"


def get_database_url() -> str:
    """Read DATABASE_URL from environment, defaulting to SQLite."""
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine(url: str | None = None) -> Engine:
    """Create a synchronous SQLAlchemy engine.

    Used by CLI tools, analysis scripts, and tests.
    For SQLite: enables WAL mode and foreign keys.
    """
    url = url or get_database_url()
    engine = _sa_create_engine(url)

    if _is_sqlite(url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine
