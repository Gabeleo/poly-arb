"""Add match_snapshots table.

Records matched pairs per scan cycle with prices from both platforms,
enabling the validation sequence: Record -> Cost Model -> Lifetime -> Backtest.

Revision ID: 002
Revises: 001
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scan_ts", sa.Text, nullable=False),
        sa.Column("scan_id", sa.Text, nullable=False),
        sa.Column("poly_condition_id", sa.Text, nullable=False),
        sa.Column("kalshi_ticker", sa.Text, nullable=False),
        sa.Column("poly_question", sa.Text, nullable=False),
        sa.Column("kalshi_question", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("poly_yes_bid", sa.Float, nullable=False),
        sa.Column("poly_yes_ask", sa.Float, nullable=False),
        sa.Column("poly_no_bid", sa.Float, nullable=False),
        sa.Column("poly_no_ask", sa.Float, nullable=False),
        sa.Column("kalshi_yes_bid", sa.Float, nullable=False),
        sa.Column("kalshi_yes_ask", sa.Float, nullable=False),
        sa.Column("kalshi_no_bid", sa.Float, nullable=False),
        sa.Column("kalshi_no_ask", sa.Float, nullable=False),
        sa.Column("raw_delta", sa.Float, nullable=False),
        sa.UniqueConstraint(
            "scan_ts", "poly_condition_id", "kalshi_ticker",
            name="uq_match_snap",
        ),
    )
    op.create_index("idx_match_snap_scan", "match_snapshots", ["scan_ts"])
    op.create_index(
        "idx_match_snap_pair", "match_snapshots",
        ["poly_condition_id", "kalshi_ticker"],
    )


def downgrade() -> None:
    op.drop_index("idx_match_snap_pair", "match_snapshots")
    op.drop_index("idx_match_snap_scan", "match_snapshots")
    op.drop_table("match_snapshots")
