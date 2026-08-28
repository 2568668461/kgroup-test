"""ORM models. State machine and uniqueness are enforced BOTH here (Python enums
/ unique constraints) and at the database layer (CHECK / UNIQUE in migrations).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.CLAIMED},
    TaskStatus.CLAIMED: {TaskStatus.RUNNING},
    TaskStatus.RUNNING: {TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.DONE},  # monotonic retry upgrade
}


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    parameter_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # claim ownership: whoever sets claimed must also set both columns
        CheckConstraint(
            "(status IN ('pending')) = (claim_token IS NULL AND claimed_by IS NULL)",
            name="ck_tasks_claim_consistency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            TaskStatus,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            create_constraint=True,
            length=20,
        ),
        default=TaskStatus.PENDING,
        nullable=False,
        index=True,
    )
    base_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    group_parameters_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    group: Mapped[Group] = relationship()
    steps: Mapped[list[Step]] = relationship(
        order_by="Step.sequence", cascade="all, delete-orphan"
    )
    logs: Mapped[list[ExecutionLog]] = relationship(
        order_by="ExecutionLog.step_sequence", cascade="all, delete-orphan"
    )


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_steps_task_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    parameter_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ExecutionLog(Base):
    __tablename__ = "execution_logs"
    __table_args__ = (UniqueConstraint("task_id", "step_sequence", name="uq_logs_task_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    step_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
