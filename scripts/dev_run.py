"""Local dev launcher — run the whole stack WITHOUT Docker.

Spawns the portable PostgreSQL 16 under tools/ (if not already running),
applies Alembic migrations, then starts the API server in the foreground.

Usage:
    python scripts/dev_run.py                 # http://127.0.0.1:8000
    python scripts/dev_run.py --port 9000     # custom port
    python scripts/dev_run.py --reset         # rebuild demo data after boot
    python scripts/dev_run.py --no-pg         # PostgreSQL already running outside

Requirements: Python 3.12 with project deps (vendor/ or a venv on PYTHONPATH).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
PG_BIN = ROOT / "tools" / "pgsql" / "bin"
PG_DATA = ROOT / "tools" / "pgdata"
PG_PORT = 5434  # fixed: .env / tests / scripts all point here
PG_DSN = f"postgresql://app:app@127.0.0.1:{PG_PORT}/kapibara"
DB_URL = f"postgresql+psycopg://app:app@127.0.0.1:{PG_PORT}/kapibara"


def env_with_pythonpath() -> dict:
    env = dict(os.environ)
    if str(VENDOR) not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = str(VENDOR) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("DATABASE_URL", DB_URL)
    return env


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=env_with_pythonpath(), **kw)


def pg_is_ready() -> bool:
    try:
        r = subprocess.run(
            [str(PG_BIN / "pg_isready.exe"), "-h", "127.0.0.1", "-p", str(PG_PORT)],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure_postgres() -> None:
    if pg_is_ready():
        print(f"[pg] already running on 127.0.0.1:{PG_PORT}")
        return
    if not (PG_BIN / "postgres.exe").exists():
        sys.exit(
            "ERROR: portable PostgreSQL not found under tools/. "
            "Either install Docker and use `docker compose up --build`, "
            "or provide a PostgreSQL on 127.0.0.1:5434 and use --no-pg."
        )
    if (PG_DATA / "postmaster.pid").exists():
        try:
            (PG_DATA / "postmaster.pid").unlink()
        except OSError:
            pass
    print("[pg] starting portable PostgreSQL ...")
    subprocess.Popen(
        [str(PG_BIN / "postgres.exe"), "-D", str(PG_DATA), "-p", str(PG_PORT)],
        cwd=ROOT / "tools",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        if pg_is_ready():
            print("[pg] ready")
            return
        time.sleep(1)
    sys.exit("ERROR: PostgreSQL did not become ready in 30s. Check tools/pg.log.")


def ensure_databases() -> None:
    for db in ("kapibara", "kapibara_test"):
        r = run(
            [str(PG_BIN / "psql.exe"), "-h", "127.0.0.1", "-p", str(PG_PORT),
             "-U", "app", "-d", "postgres", "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{db}'"],
            capture_output=True, text=True,
        )
        if r.stdout.strip() != "1":
            run([str(PG_BIN / "createdb.exe"), "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "app", db])
            print(f"[db] created {db}")


def migrate() -> None:
    print("[migrate] alembic upgrade head ...")
    r = run([sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: alembic failed:\n{r.stderr or r.stdout}")
    print("[migrate] done")


def wait_api(port: int, timeout: int = 30) -> None:
    url = f"http://127.0.0.1:{port}/api/tasks"
    for _ in range(timeout):
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(1)
    sys.exit("ERROR: API server did not become ready.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reset", action="store_true", help="rebuild demo data after boot")
    ap.add_argument("--no-pg", action="store_true", help="do not manage PostgreSQL")
    args = ap.parse_args()

    if not args.no_pg:
        ensure_postgres()
        ensure_databases()
    migrate()

    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", args.host, "--port", str(args.port)]
    if args.reset:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env_with_pythonpath())
        try:
            wait_api(args.port)
            reset = urllib.request.Request(
                f"http://127.0.0.1:{args.port}/api/demo/reset",
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(reset, timeout=10) as resp:
                print(f"[demo] reset -> HTTP {resp.status}")
        except Exception as e:
            print(f"[demo] reset failed: {e}")
        print(f"\nKapibala dashboard: http://{args.host}:{args.port}  (Ctrl+C to stop)")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
    else:
        print(f"\nKapibala dashboard: http://{args.host}:{args.port}  (Ctrl+C to stop)")
        run(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
