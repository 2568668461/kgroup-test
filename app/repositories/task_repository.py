"""Raw SQL and row-locking primitives.

The claim statement is a single-statement transaction body: SELECT ... FOR
UPDATE SKIP LOCKED picks one pending row while excluding it from every other
concurrent transaction, and the UPDATE in the same statement flips ownership.

The log upsert relies on the UNIQUE(task_id, step_sequence) constraint and
merges monotonically: success only ever goes false -> true, completed_at keeps
the earliest value. The DB constraint — not a check-then-insert — is the final
line of defense against concurrent duplicate reports.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import ExecutionLog, Group, Step, Task

CLAIM_NEXT_SQL = text(
    """
    WITH candidate AS (
        SELECT id
        FROM tasks
        WHERE status = 'pending'
        ORDER BY created_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE tasks
    SET status = 'claimed',
        claimed_by = :worker_id,
        claim_token = :claim_token,
        claimed_at = NOW()
    FROM candidate
    WHERE tasks.id = candidate.id
    RETURNING tasks.id
    """
)

LOG_UPSERT_SQL = text(
    """
    INSERT INTO execution_logs (task_id, step_id, step_sequence, success, completed_at, created_at)
    VALUES (:task_id, :step_id, :step_sequence, :success, NOW(), NOW())
    ON CONFLICT (task_id, step_sequence)
    DO UPDATE SET
        success = execution_logs.success OR EXCLUDED.success,
        completed_at = LEAST(execution_logs.completed_at, EXCLUDED.completed_at)
    RETURNING (xmax = 0) AS inserted, id, success, completed_at, created_at
    """
)


def claim_next_task(session: Session, worker_id: str, claim_token: UUID) -> Task | None:
    """Atomically claim the oldest pending task. Returns None when exhausted."""
    row = session.execute(CLAIM_NEXT_SQL, {"worker_id": worker_id, "claim_token": claim_token}).first()
    if row is None:
        return None
    task = session.get(Task, row[0])
    assert task is not None
    return task


def lock_task(session: Session, task_id: int) -> Task | None:
    """SELECT ... FOR UPDATE — serialize all state transitions of one task."""
    return session.scalars(select(Task).where(Task.id == task_id).with_for_update()).first()


def lock_group(session: Session, group_id: int) -> Group | None:
    return session.scalars(select(Group).where(Group.id == group_id).with_for_update()).first()


def get_ordered_steps(session: Session, task_id: int) -> list[Step]:
    return list(session.scalars(select(Step).where(Step.task_id == task_id).order_by(Step.sequence)))


def get_logs(session: Session, task_id: int) -> dict[int, ExecutionLog]:
    rows = session.scalars(
        select(ExecutionLog).where(ExecutionLog.task_id == task_id).order_by(ExecutionLog.step_sequence)
    )
    return {log.step_sequence: log for log in rows}


def upsert_execution_log(
    session: Session, task_id: int, step_id: int, step_sequence: int, success: bool
) -> dict:
    """Atomic monotonic merge; returns {inserted, id, success, completed_at, created_at}."""
    return dict(
        session.execute(
            LOG_UPSERT_SQL,
            {
                "task_id": task_id,
                "step_id": step_id,
                "step_sequence": step_sequence,
                "success": success,
            },
        ).mappings().one()
    )
