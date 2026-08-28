"""Server-rendered pages: HTMX dashboard with 2-second polling."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from ..database import SessionLocal
from ..services import task_service

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render_context(request: Request, tasks: list[dict]) -> dict:
    return {"request": request, "tasks": tasks}


@router.get("/")
def index(request: Request):
    with SessionLocal() as session:
        tasks = task_service.list_tasks(session)
    return templates.TemplateResponse(request, "index.html", _render_context(request, tasks))


@router.get("/partials/task-table")
def task_table(request: Request):
    with SessionLocal() as session:
        tasks = task_service.list_tasks(session)
    return templates.TemplateResponse(request, "_task_table.html", _render_context(request, tasks))


@router.get("/tasks/{task_id}")
def task_detail_page(request: Request, task_id: int):
    with SessionLocal() as session:
        task = task_service.get_task_detail(session, task_id)
    if task is None:
        from ..services.errors import NotFoundError

        raise NotFoundError(f"task {task_id} not found")
    return templates.TemplateResponse(request, "detail.html", {"request": request, "task": task})


@router.get("/partials/task-detail/{task_id}")
def task_detail_partial(request: Request, task_id: int):
    with SessionLocal() as session:
        task = task_service.get_task_detail(session, task_id)
    if task is None:
        from ..services.errors import NotFoundError

        raise NotFoundError(f"task {task_id} not found")
    return templates.TemplateResponse(request, "_task_detail.html", {"request": request, "task": task})
