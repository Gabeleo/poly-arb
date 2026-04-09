"""Initial schema — 4 existing tables.

No-op on databases that already have tables from RecorderDB and ExecutionJournal.
Uses op.execute with IF NOT EXISTS for SQLite compatibility.

Revision ID: 001
Revises: None
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Check which tables already exist
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if "polymarket_snapshots" not in existing:
        op.create_table(
            "polymarket_snapshots",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("scan_ts", sa.Text, nullable=False),
            sa.Column("condition_id", sa.Text, nullable=False),
            sa.Column("question", sa.Text, nullable=False),
            sa.Column("event_slug", sa.Text, nullable=False, server_default=""),
            sa.Column("yes_bid", sa.Float, nullable=False),
            sa.Column("yes_ask", sa.Float, nullable=False),
            sa.Column("no_bid", sa.Float, nullable=False),
            sa.Column("no_ask", sa.Float, nullable=False),
            sa.Column("volume", sa.Float, nullable=False),
            sa.Column("volume_24h", sa.Float, nullable=False, server_default="0"),
            sa.Column("end_date", sa.Text, nullable=True),
            sa.UniqueConstraint("scan_ts", "condition_id", name="uq_poly_scan_cid"),
        )

    if "kalshi_snapshots" not in existing:
        op.create_table(
            "kalshi_snapshots",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("scan_ts", sa.Text, nullable=False),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("question", sa.Text, nullable=False),
            sa.Column("event_ticker", sa.Text, nullable=False, server_default=""),
            sa.Column("yes_bid", sa.Float, nullable=False),
            sa.Column("yes_ask", sa.Float, nullable=False),
            sa.Column("no_bid", sa.Float, nullable=False),
            sa.Column("no_ask", sa.Float, nullable=False),
            sa.Column("volume", sa.Float, nullable=False),
            sa.Column("volume_24h", sa.Float, nullable=False, server_default="0"),
            sa.Column("close_time", sa.Text, nullable=True),
            sa.UniqueConstraint("scan_ts", "ticker", name="uq_kalshi_scan_ticker"),
        )

    if "executions" not in existing:
        op.create_table(
            "executions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("execution_id", sa.Text, nullable=False, unique=True),
            sa.Column("created_at", sa.Text, nullable=False),
            sa.Column("match_key", sa.Text, nullable=False),
            sa.Column("status", sa.Text, nullable=False, server_default="pending"),
            sa.Column("leg_count", sa.Integer, nullable=False),
            sa.Column("profit", sa.Float, nullable=True),
            sa.Column("completed_at", sa.Text, nullable=True),
        )

    if "execution_legs" not in existing:
        op.create_table(
            "execution_legs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("execution_id", sa.Text, nullable=False),
            sa.Column("leg_index", sa.Integer, nullable=False),
            sa.Column("platform", sa.Text, nullable=False),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("side", sa.Text, nullable=False),
            sa.Column("action", sa.Text, nullable=False),
            sa.Column("price", sa.Float, nullable=False),
            sa.Column("size", sa.Float, nullable=False),
            sa.Column("status", sa.Text, nullable=False, server_default="pending"),
            sa.Column("order_id", sa.Text, nullable=True),
            sa.Column("fill_qty", sa.Float, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("sent_at", sa.Text, nullable=True),
            sa.Column("completed_at", sa.Text, nullable=True),
            sa.UniqueConstraint("execution_id", "leg_index", name="uq_exec_leg"),
        )

    # Create indexes (IF NOT EXISTS is implicit — SQLite ignores dupes)
    _safe_create_index(
        "idx_poly_condition", "polymarket_snapshots", ["condition_id", "scan_ts"], conn
    )
    _safe_create_index("idx_kalshi_ticker", "kalshi_snapshots", ["ticker", "scan_ts"], conn)
    _safe_create_index("idx_poly_scan", "polymarket_snapshots", ["scan_ts"], conn)
    _safe_create_index("idx_kalshi_scan", "kalshi_snapshots", ["scan_ts"], conn)
    _safe_create_index("idx_legs_execution", "execution_legs", ["execution_id"], conn)
    _safe_create_index("idx_legs_status", "execution_legs", ["status"], conn)
    _safe_create_index("idx_exec_status", "executions", ["status"], conn)


def _safe_create_index(name, table, columns, conn):
    """Create index only if it doesn't already exist."""
    inspector = sa.inspect(conn)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(table)}
    if name not in existing_indexes:
        op.create_index(name, table, columns)


def downgrade() -> None:
    op.drop_index("idx_exec_status", "executions")
    op.drop_index("idx_legs_status", "execution_legs")
    op.drop_index("idx_legs_execution", "execution_legs")
    op.drop_index("idx_kalshi_scan", "kalshi_snapshots")
    op.drop_index("idx_poly_scan", "polymarket_snapshots")
    op.drop_index("idx_kalshi_ticker", "kalshi_snapshots")
    op.drop_index("idx_poly_condition", "polymarket_snapshots")
    op.drop_table("execution_legs")
    op.drop_table("executions")
    op.drop_table("kalshi_snapshots")
    op.drop_table("polymarket_snapshots")
