"""REST API endpoints. All request/response bodies use Pydantic models;
domain errors map to 400/403/404/409 with explicit messages.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from fastapi import APIRouter, Response

from ..database import SessionLocal
from ..schemas import (
    ClaimResponse,
    CompleteRequest,
    CompleteResponse,
    GroupCreate,
    GroupOut,
    Message,
    StartRequest,
    StartResponse,
    StepOut,
    TaskCreate,
    TaskDetail,
    TaskSummary,
)
from ..services import task_service
from ..services.errors import ForbiddenError, NotFoundError

router = APIRouter(prefix="/api")


@contextmanager
def transaction():
    """One explicit transaction per request: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


@router.post("/groups", status_code=201, response_model=GroupOut)
def create_group(payload: GroupCreate):
    with transaction() as session:
        group = task_service.create_group(session, payload.name, payload.parameter_overrides)
        return GroupOut(
            id=group.id,
            name=group.name,
            parameter_overrides=group.parameter_overrides,
            created_at=group.created_at,
        )


@router.post("/tasks", status_code=201, response_model=TaskDetail)
def create_task(payload: TaskCreate):
    with transaction() as session:
        task = task_service.create_task(
            session,
            payload.group_id,
            payload.name,
            payload.base_parameters,
            [s.model_dump() for s in payload.steps],
        )
        return TaskDetail(**task_service.get_task_detail(session, task.id))


@router.post("/workers/{worker_id}/claim-next", response_model=ClaimResponse)
def claim_next(worker_id: str):
    """Atomically claim the next pending task. 204 No Content when exhausted."""
    with transaction() as session:
        task = task_service.claim_next(session, worker_id)
        if task is None:
            return Response(status_code=204)
        return ClaimResponse(
            task_id=task.id, name=task.name, claim_token=task.claim_token, status=task.status.value
        )


@router.post("/tasks/{task_id}/start", response_model=StartResponse)
def start_task(task_id: int, payload: StartRequest):
    with transaction() as session:
        task = task_service.start_task(session, task_id, payload.claim_token)
        return StartResponse(
            task_id=task.id,
            status=task.status.value,
            group_parameters_snapshot=task.group_parameters_snapshot,
            steps=[StepOut.model_validate(s) for s in task.steps],
        )


@router.post("/tasks/{task_id}/steps/{sequence}/complete", response_model=CompleteResponse)
def complete_step(task_id: int, sequence: int, payload: CompleteRequest):
    with transaction() as session:
        result = task_service.complete_step(
            session, task_id, sequence, payload.claim_token, payload.success
        )
        return CompleteResponse(
            task_id=task_id,
            step_sequence=sequence,
            outcome=result["outcome"],
            log=result["log"],
            task_status=result["task_status"],
        )


@router.get("/tasks", response_model=list[TaskSummary])
def list_tasks():
    with transaction() as session:
        return [TaskSummary(**t) for t in task_service.list_tasks(session)]


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: int):
    with transaction() as session:
        detail = task_service.get_task_detail(session, task_id)
        if detail is None:
            raise NotFoundError(f"task {task_id} not found")
        return TaskDetail(**detail)


@router.post("/demo/reset", response_model=Message)
def reset_demo():
    if os.getenv("APP_ENV", "development") == "production":
        raise ForbiddenError("demo reset is disabled outside development")
    with transaction() as session:
        return Message(**task_service.reset_demo_data(session))
