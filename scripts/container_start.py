"""Run database migrations, then replace this process with Uvicorn."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    print("[startup] applying Alembic migrations", flush=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    print("[startup] starting Uvicorn on 0.0.0.0:8000", flush=True)
    os.execvp(
        "uvicorn",
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
    )


if __name__ == "__main__":
    main()
