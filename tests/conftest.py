"""Pytest fixtures. Integration tests need a REAL PostgreSQL.

Set TEST_DATABASE_URL (defaults to a local kapibara_test database). The whole
suite is skipped when the database is unreachable — we deliberately do NOT
fall back to SQLite, because SKIP LOCKED / JSONB / check constraints are part
of what is under test.
"""

from __future__ import annotations

import os

# Must run before app.database is imported anywhere.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/kapibara_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


def _pg_available() -> bool:
    try:
        eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available()

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def engine():
    if not PG_AVAILABLE:
        pytest.skip("TEST_DATABASE_URL is not reachable — start a real PostgreSQL to run these")
    from app import models  # noqa: F401
    from app.database import Base

    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def clean_db(engine):
    """Truncate all tables between tests, resetting identity sequences."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE execution_logs, steps, tasks, groups "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture()
def client(engine, clean_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
