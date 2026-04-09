"""Add positions and risk_events tables.

Tracks open/closed positions across platforms and logs risk limit
breaches for audit and automated incident response.

Revision ID: 004
Revises: 003
Create Date: 2026-04-09
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("avg_price", sa.Float, nullable=False),
        sa.Column("opened_at", sa.Text, nullable=False),
        sa.Column("closed_at", sa.Text, nullable=True),
        sa.Column("execution_id", sa.Text, nullable=True),
        sa.Column("realized_pnl", sa.Float, nullable=True),
        sa.UniqueConstraint("platform", "ticker", "side", name="uq_position"),
    )
    op.create_index("idx_positions_platform", "positions", ["platform", "ticker"])
    op.create_index("idx_positions_execution", "positions", ["execution_id"])

    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("timestamp", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("details", sa.Text, nullable=False),
        sa.Column("execution_id", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.Text, nullable=True),
    )
    op.create_index("idx_risk_events_type", "risk_events", ["event_type"])
    op.create_index("idx_risk_events_ts", "risk_events", ["timestamp"])


def downgrade() -> None:
    op.drop_index("idx_risk_events_ts", "risk_events")
    op.drop_index("idx_risk_events_type", "risk_events")
    op.drop_table("risk_events")
    op.drop_index("idx_positions_execution", "positions")
    op.drop_index("idx_positions_platform", "positions")
    op.drop_table("positions")
