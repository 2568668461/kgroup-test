"""Run database migrations, then replace this process with Uvicorn."""

from __future__ import annotations

import subprocess
import sys

import uvicorn


def main() -> None:
    print("[startup] applying Alembic migrations", flush=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    print("[startup] starting Uvicorn on 0.0.0.0:8000", flush=True)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
