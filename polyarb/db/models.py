"""SQLAlchemy Core table definitions — single source of truth for all schemas."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Table as SATable

metadata = MetaData()

polymarket_snapshots = SATable(
    "polymarket_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_ts", Text, nullable=False),
    Column("condition_id", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("event_slug", Text, nullable=False, server_default=""),
    Column("yes_bid", Float, nullable=False),
    Column("yes_ask", Float, nullable=False),
    Column("no_bid", Float, nullable=False),
    Column("no_ask", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("volume_24h", Float, nullable=False, server_default="0"),
    Column("end_date", Text, nullable=True),
    UniqueConstraint("scan_ts", "condition_id", name="uq_poly_scan_cid"),
)

kalshi_snapshots = SATable(
    "kalshi_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_ts", Text, nullable=False),
    Column("ticker", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("event_ticker", Text, nullable=False, server_default=""),
    Column("yes_bid", Float, nullable=False),
    Column("yes_ask", Float, nullable=False),
    Column("no_bid", Float, nullable=False),
    Column("no_ask", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("volume_24h", Float, nullable=False, server_default="0"),
    Column("close_time", Text, nullable=True),
    UniqueConstraint("scan_ts", "ticker", name="uq_kalshi_scan_ticker"),
)

executions = SATable(
    "executions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("execution_id", Text, nullable=False, unique=True),
    Column("created_at", Text, nullable=False),
    Column("match_key", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("leg_count", Integer, nullable=False),
    Column("profit", Float, nullable=True),
    Column("completed_at", Text, nullable=True),
)

execution_legs = SATable(
    "execution_legs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("execution_id", Text, nullable=False),
    Column("leg_index", Integer, nullable=False),
    Column("platform", Text, nullable=False),
    Column("ticker", Text, nullable=False),
    Column("side", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("price", Float, nullable=False),
    Column("size", Float, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("order_id", Text, nullable=True),
    Column("fill_qty", Float, nullable=True),
    Column("error", Text, nullable=True),
    Column("sent_at", Text, nullable=True),
    Column("completed_at", Text, nullable=True),
    UniqueConstraint("execution_id", "leg_index", name="uq_exec_leg"),
)

audit_log = SATable(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("timestamp", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("details", Text, nullable=False),
    Column("request_id", Text, nullable=True),
)

match_snapshots = SATable(
    "match_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_ts", Text, nullable=False),
    Column("scan_id", Text, nullable=False),
    Column("poly_condition_id", Text, nullable=False),
    Column("kalshi_ticker", Text, nullable=False),
    Column("poly_question", Text, nullable=False),
    Column("kalshi_question", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("poly_yes_bid", Float, nullable=False),
    Column("poly_yes_ask", Float, nullable=False),
    Column("poly_no_bid", Float, nullable=False),
    Column("poly_no_ask", Float, nullable=False),
    Column("kalshi_yes_bid", Float, nullable=False),
    Column("kalshi_yes_ask", Float, nullable=False),
    Column("kalshi_no_bid", Float, nullable=False),
    Column("kalshi_no_ask", Float, nullable=False),
    Column("raw_delta", Float, nullable=False),
    UniqueConstraint(
        "scan_ts", "poly_condition_id", "kalshi_ticker",
        name="uq_match_snap",
    ),
)

# Indexes
Index("idx_poly_condition", polymarket_snapshots.c.condition_id, polymarket_snapshots.c.scan_ts)
Index("idx_kalshi_ticker", kalshi_snapshots.c.ticker, kalshi_snapshots.c.scan_ts)
Index("idx_poly_scan", polymarket_snapshots.c.scan_ts)
Index("idx_kalshi_scan", kalshi_snapshots.c.scan_ts)
Index("idx_legs_execution", execution_legs.c.execution_id)
Index("idx_legs_status", execution_legs.c.status)
Index("idx_exec_status", executions.c.status)
Index("idx_match_snap_scan", match_snapshots.c.scan_ts)
Index("idx_match_snap_pair", match_snapshots.c.poly_condition_id, match_snapshots.c.kalshi_ticker)
Index("idx_audit_action", audit_log.c.action)
Index("idx_audit_ts", audit_log.c.timestamp)
