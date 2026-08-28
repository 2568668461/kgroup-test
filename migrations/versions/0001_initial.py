"""initial schema: groups, tasks, steps, execution_logs

Revision ID: 0001
Revises:
Create Date: 2026-08-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parameter_overrides", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("base_parameters", postgresql.JSONB(), nullable=True),
        sa.Column("group_parameters_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'running', 'done', 'failed')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "(status IN ('pending')) = (claim_token IS NULL AND claimed_by IS NULL)",
            name="ck_tasks_claim_consistency",
        ),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    # covering index for the SKIP LOCKED claim scan
    op.create_index("ix_tasks_claim_order", "tasks", ["status", "created_at", "id"])

    op.create_table(
        "steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parameter_overrides", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_parameters", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("task_id", "sequence", name="uq_steps_task_sequence"),
    )
    op.create_index("ix_steps_task_id", "steps", ["task_id"])

    op.create_table(
        "execution_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey("steps.id"), nullable=True),
        sa.Column("step_sequence", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("task_id", "step_sequence", name="uq_logs_task_sequence"),
    )
    op.create_index("ix_execution_logs_task_id", "execution_logs", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_execution_logs_task_id", table_name="execution_logs")
    op.drop_table("execution_logs")
    op.drop_index("ix_steps_task_id", table_name="steps")
    op.drop_table("steps")
    op.drop_index("ix_tasks_claim_order", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("groups")
