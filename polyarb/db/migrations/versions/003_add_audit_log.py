"""Add audit_log table.

Records state-mutating API actions (config changes, execution approvals)
for operator review and compliance.

Revision ID: 003
Revises: 002
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("timestamp", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("details", sa.Text, nullable=False),
        sa.Column("request_id", sa.Text, nullable=True),
    )
    op.create_index("idx_audit_action", "audit_log", ["action"])
    op.create_index("idx_audit_ts", "audit_log", ["timestamp"])


def downgrade() -> None:
    op.drop_index("idx_audit_ts", "audit_log")
    op.drop_index("idx_audit_action", "audit_log")
    op.drop_table("audit_log")
