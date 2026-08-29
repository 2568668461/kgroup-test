"""strengthen ownership and timestamp invariants

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tasks_claim_consistency", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_claim_consistency",
        "tasks",
        "(status = 'pending' AND claim_token IS NULL AND claimed_by IS NULL) OR "
        "(status <> 'pending' AND claim_token IS NOT NULL AND claimed_by IS NOT NULL)",
    )

    for table, column in (
        ("groups", "created_at"),
        ("tasks", "created_at"),
        ("execution_logs", "completed_at"),
        ("execution_logs", "created_at"),
    ):
        op.execute(sa.text(f"UPDATE {table} SET {column} = NOW() WHERE {column} IS NULL"))
        op.alter_column(table, column, existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    for table, column in (
        ("execution_logs", "created_at"),
        ("execution_logs", "completed_at"),
        ("tasks", "created_at"),
        ("groups", "created_at"),
    ):
        op.alter_column(table, column, existing_type=sa.DateTime(timezone=True), nullable=True)

    op.drop_constraint("ck_tasks_claim_consistency", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_claim_consistency",
        "tasks",
        "(status IN ('pending')) = (claim_token IS NULL AND claimed_by IS NULL)",
    )
