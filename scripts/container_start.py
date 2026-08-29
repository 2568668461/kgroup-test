"""Run database migrations, then replace this process with Uvicorn."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    print("[startup] applying Alembic migrations", flush=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    print("[startup] starting Uvicorn on 0.0.0.0:8000", flush=True)
    from uvicorn.main import main as uvicorn_main

    sys.argv = [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--log-level",
        "info",
    ]
    uvicorn_main()


if __name__ == "__main__":
    main()
