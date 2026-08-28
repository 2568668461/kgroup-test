"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes, web
from .services.errors import DomainError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.getLogger("kapibara").info("kapibara service started")
    yield


app = FastAPI(title="Kapibara Task Orchestrator", version="1.0.0", lifespan=lifespan)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})


app.include_router(routes.router)
app.include_router(web.router)

STATIC_DIR = web.TEMPLATES_DIR.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
