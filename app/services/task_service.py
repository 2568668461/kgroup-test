"""Task orchestration service: claim / start / complete, with the state machine

    pending -> claimed -> running -> done
                                \\-> failed

Invariants enforced here (on top of the DB constraints):
- only the worker holding the correct claim_token can start a task or report
  a step (worker ID alone is NOT enough);
- a task may only complete steps in order — the earliest step that has not
  succeeded yet;
- execution logs merge monotonically: success can be upgraded from failure,
  never downgraded; completed_at keeps the earliest timestamp;
- a started task freezes its group parameter snapshot; later group edits
  never affect it.

Every public function runs inside a transaction opened by the caller.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.parameters import resolve_step_parameters
from ..models import ExecutionLog, Group, Step, Task, TaskStatus
from ..repositories import task_repository as repo
from .errors import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger("kapibara")

SHORT = 8  # truncated identifiers in logs — never log full parameter values


def _short(token) -> str:
    return str(token)[:SHORT] if token else "-"


# ---------------------------------------------------------------- create ----


def create_group(session: Session, name: str, parameter_overrides: dict | None) -> Group:
    group = Group(name=name, parameter_overrides=parameter_overrides or None)
    session.add(group)
    session.flush()
    return group


def create_task(
    session: Session,
    group_id: int,
    name: str,
    base_parameters: dict | None,
    steps: list[dict],
) -> Task:
    group = session.get(Group, group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    task = Task(
        group_id=group_id,
        name=name,
        base_parameters=base_parameters or None,
        status=TaskStatus.PENDING,
        steps=[
            Step(sequence=i + 1, name=s["name"], parameter_overrides=s.get("parameter_overrides"))
            for i, s in enumerate(steps)
        ],
    )
    session.add(task)
    session.flush()
    return task


# ----------------------------------------------------------------- claim ----


def claim_next(session: Session, worker_id: str) -> Task | None:
    """Atomically claim the oldest pending task for a worker.

    Uses FOR UPDATE SKIP LOCKED so concurrent workers never block each other
    and never claim the same row. Returns None when no task is pending.
    """
    token = uuid.uuid4()
    task = repo.claim_next_task(session, worker_id, token)
    if task is None:
        return None
    logger.info(
        "claim task=%s worker=%s token=%s...",
        task.id, worker_id, _short(task.claim_token),
    )
    return task


# ----------------------------------------------------------------- start ----


def start_task(session: Session, task_id: int, claim_token) -> Task:
    task = repo.lock_task(session, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    if task.claim_token is None or task.claim_token != claim_token:
        raise ForbiddenError("claim_token does not match the task owner")
    if task.status is not TaskStatus.CLAIMED:
        raise ConflictError(f"cannot start task in status '{task.status.value}'")

    group = repo.lock_group(session, task.group_id)
    assert group is not None

    task.group_parameters_snapshot = dict(group.parameter_overrides) if group.parameter_overrides else {}
    snapshots = resolve_step_parameters(
        task.base_parameters,
        group.parameter_overrides,
        [s.parameter_overrides for s in task.steps],
    )
    for step, snapshot in zip(task.steps, snapshots, strict=True):
        step.resolved_parameters = snapshot

    task.status = TaskStatus.RUNNING
    task.started_at = datetime.now(UTC)
    logger.info("start task=%s token=%s...", task_id, _short(claim_token))
    return task


# ------------------------------------------------------------- complete -----


def complete_step(
    session: Session, task_id: int, sequence: int, claim_token, success: bool
) -> dict:
    """Idempotent step completion report.

    Returns {outcome, log, task_status} where outcome is one of
    'created' | 'upgraded' | 'duplicate_no_change'.
    """
    task = repo.lock_task(session, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    if task.status in (TaskStatus.PENDING, TaskStatus.CLAIMED):
        raise ConflictError("task has not been started yet")
    if task.claim_token is None or task.claim_token != claim_token:
        raise ForbiddenError("claim_token does not match the task owner")
    if not any(s.sequence == sequence for s in task.steps):
        raise NotFoundError(f"task {task_id} has no step {sequence}")

    logs_before = repo.get_logs(session, task_id)
    existing = logs_before.get(sequence)
    # Capture BEFORE the upsert: expire_all() below would lazily refresh
    # `existing` from the DB and hide whether an upgrade happened.
    existing_success_before = existing.success if existing is not None else None

    if existing is None:
        # A brand-new report may only target the earliest not-yet-successful step.
        frontier = _frontier(task.steps, logs_before)
        if frontier is None:
            raise ConflictError("all steps already succeeded; task is complete")
        if task.status is TaskStatus.DONE:
            raise ConflictError("task is done; no new step reports accepted")
        if sequence != frontier.sequence:
            raise ConflictError(
                f"out-of-order report: expected step {frontier.sequence}, got {sequence}"
            )

    step = next(s for s in task.steps if s.sequence == sequence)
    merged = repo.upsert_execution_log(session, task_id, step.id, sequence, success)

    # raw SQL upsert bypassed the identity map — force reload before recomputing
    session.expire_all()
    task = repo.lock_task(session, task_id)
    logs_after = repo.get_logs(session, task_id)

    new_status = _recompute_status(task.steps, logs_after)
    if new_status is not task.status:
        if new_status in (TaskStatus.DONE, TaskStatus.FAILED):
            task.finished_at = datetime.now(UTC)
        else:
            # A successful retry reopens a failed multi-step task.
            task.finished_at = None
        task.status = new_status
    session.flush()

    if existing is None:
        outcome = "created"
    elif existing_success_before is False and merged["success"]:
        outcome = "upgraded"
    else:
        outcome = "duplicate_no_change"

    log = logs_after[sequence]
    result = {
        "outcome": outcome,
        "log": {
            "step_sequence": log.step_sequence,
            "success": log.success,
            "completed_at": log.completed_at,
            "created_at": log.created_at,
        },
        "task_status": task.status.value,
    }
    logger.info(
        "complete task=%s step=%s success=%s outcome=%s token=%s...",
        task_id, sequence, success, outcome, _short(claim_token),
    )
    return result


def _frontier(steps: list[Step], logs: dict[int, ExecutionLog]) -> Step | None:
    """The earliest step that has not succeeded yet (None => all succeeded)."""
    for step in steps:
        log = logs.get(step.sequence)
        if log is None or not log.success:
            return step
    return None


def _recompute_status(steps: list[Step], logs: dict[int, ExecutionLog]) -> TaskStatus:
    frontier = _frontier(steps, logs)
    if frontier is None:
        return TaskStatus.DONE
    log = logs.get(frontier.sequence)
    if log is not None and log.success is False:
        return TaskStatus.FAILED
    return TaskStatus.RUNNING


# ------------------------------------------------------------- queries -----


def list_tasks(session: Session) -> list[dict]:
    tasks = session.scalars(select(Task).order_by(Task.id)).all()
    result = []
    for task in tasks:
        steps = task.steps
        logs = {log.step_sequence: log for log in task.logs}
        frontier = _frontier(steps, logs)
        result.append(
            {
                "id": task.id,
                "name": task.name,
                "group_id": task.group_id,
                "group_name": task.group.name,
                "status": task.status.value,
                "claimed_by": task.claimed_by,
                # Used only by the server-rendered local demo. TaskSummary's
                # response model excludes it from GET /api/tasks.
                "claim_token": task.claim_token,
                "current_step": frontier.sequence if frontier else None,
                "completed_steps": sum(1 for log in logs.values() if log.success),
                "total_steps": len(steps),
                "updated_at": task.finished_at or task.started_at or task.claimed_at or task.created_at,
            }
        )
    return result


def get_task_detail(session: Session, task_id: int) -> dict | None:
    task = session.get(Task, task_id)
    if task is None:
        return None
    return {
        "id": task.id,
        "name": task.name,
        "group_id": task.group_id,
        "group_name": task.group.name,
        "status": task.status.value,
        "claimed_by": task.claimed_by,
        "claim_token": task.claim_token,
        "base_parameters": task.base_parameters,
        "group_parameters_snapshot": task.group_parameters_snapshot,
        "created_at": task.created_at,
        "claimed_at": task.claimed_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "updated_at": task.finished_at or task.started_at or task.claimed_at or task.created_at,
        "current_step": (
            frontier.sequence
            if (frontier := _frontier(task.steps, {log.step_sequence: log for log in task.logs}))
            else None
        ),
        "completed_steps": sum(1 for log in task.logs if log.success),
        "total_steps": len(task.steps),
        "steps": [
            {
                "sequence": s.sequence,
                "name": s.name,
                "parameter_overrides": s.parameter_overrides,
                "resolved_parameters": s.resolved_parameters,
            }
            for s in task.steps
        ],
        "logs": [
            {
                "step_sequence": log.step_sequence,
                "success": log.success,
                "completed_at": log.completed_at,
                "created_at": log.created_at,
            }
            for log in task.logs
        ],
    }


# ----------------------------------------------------------------- demo -----


def reset_demo_data(session: Session) -> dict:
    """Rebuild a fixed demo dataset covering all five task statuses."""
    session.execute(delete(ExecutionLog))
    session.execute(delete(Step))
    session.execute(delete(Task))
    session.execute(delete(Group))
    session.flush()

    # L2 overrides: mixed types, "" is a real value, new keys allowed
    g1 = create_group(session, "web-group", {"region": "cn-north", "timeout": 60, "tag": ""})
    g2 = create_group(session, "default", None)

    # NOTE: claim_next() always takes the OLDEST pending task, so each task is
    # created (and claimed) in order; the pending one is created last.

    # 1. claimed
    create_task(
        session, g2.id, "demo-claimed",
        base_parameters={"region": "us"},
        steps=[
            {"name": "prepare", "parameter_overrides": {"region": "eu"}},
            {"name": "build", "parameter_overrides": {}},
        ],
    )
    task = claim_next(session, "worker-demo-1")
    assert task is not None and task.name == "demo-claimed"

    # 2. running with step 1 done
    create_task(
        session, g1.id, "demo-running",
        base_parameters={"region": "us", "timeout": 30},
        steps=[
            {"name": "prepare", "parameter_overrides": {"timeout": 45}},
            {"name": "build", "parameter_overrides": {"timeout": ""}},
            {"name": "verify", "parameter_overrides": {"region": ""}},
        ],
    )
    task = claim_next(session, "worker-demo-2")
    assert task is not None and task.name == "demo-running"
    start_task(session, task.id, task.claim_token)
    complete_step(session, task.id, 1, task.claim_token, True)

    # 3. done
    create_task(
        session, g2.id, "demo-done",
        base_parameters={"k": "v"},
        steps=[{"name": "only-step", "parameter_overrides": {"k": "final"}}],
    )
    task = claim_next(session, "worker-demo-3")
    assert task is not None and task.name == "demo-done"
    start_task(session, task.id, task.claim_token)
    complete_step(session, task.id, 1, task.claim_token, True)

    # 4. failed at step 2
    create_task(
        session, g1.id, "demo-failed",
        base_parameters={"region": "us"},
        steps=[
            {"name": "prepare", "parameter_overrides": None},
            {"name": "build", "parameter_overrides": None},
        ],
    )
    task = claim_next(session, "worker-demo-4")
    assert task is not None and task.name == "demo-failed"
    start_task(session, task.id, task.claim_token)
    complete_step(session, task.id, 1, task.claim_token, True)
    complete_step(session, task.id, 2, task.claim_token, False)

    # 5. pending — created last so nothing claims it
    create_task(
        session, g1.id, "demo-pending",
        base_parameters={"region": "us", "timeout": 30, "extra": True},
        steps=[
            {"name": "prepare", "parameter_overrides": {"timeout": 90}},
            {"name": "build", "parameter_overrides": {"timeout": "", "mode": "fast"}},
            {"name": "verify", "parameter_overrides": None},
        ],
    )
    return {"detail": "demo data rebuilt"}
