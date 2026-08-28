"""Pydantic request/response models for the REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parameter_overrides: dict[str, Any] | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    parameter_overrides: dict[str, Any] | None
    created_at: datetime


class StepCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parameter_overrides: dict[str, Any] | None = None


class TaskCreate(BaseModel):
    group_id: int
    name: str = Field(min_length=1, max_length=200)
    base_parameters: dict[str, Any] | None = None
    steps: list[StepCreate] = Field(min_length=1)


class StepOut(BaseModel):
    sequence: int
    name: str
    parameter_overrides: dict[str, Any] | None
    resolved_parameters: dict[str, Any] | None

    model_config = {"from_attributes": True}


class ExecutionLogOut(BaseModel):
    step_sequence: int
    success: bool
    completed_at: datetime
    created_at: datetime


class TaskSummary(BaseModel):
    id: int
    name: str
    group_id: int
    group_name: str
    status: str
    claimed_by: str | None
    current_step: int | None
    completed_steps: int
    total_steps: int
    updated_at: datetime | None


class TaskDetail(TaskSummary):
    claim_token: uuid.UUID | None
    base_parameters: dict[str, Any] | None
    group_parameters_snapshot: dict[str, Any] | None
    created_at: datetime
    claimed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[StepOut]
    logs: list[ExecutionLogOut]


class ClaimResponse(BaseModel):
    task_id: int
    name: str
    claim_token: uuid.UUID
    status: str


class StartRequest(BaseModel):
    claim_token: uuid.UUID


class StartResponse(BaseModel):
    task_id: int
    status: str
    group_parameters_snapshot: dict[str, Any] | None
    steps: list[StepOut]


class CompleteRequest(BaseModel):
    claim_token: uuid.UUID
    success: bool


class CompleteResponse(BaseModel):
    task_id: int
    step_sequence: int
    outcome: Literal["created", "upgraded", "duplicate_no_change"]
    log: ExecutionLogOut
    task_status: str


class Message(BaseModel):
    detail: str
