"""Multi-process concurrent claim test — REAL processes, REAL connections.

  10 worker processes (multiprocessing spawn) x 100 pending tasks x 20 rounds.

Each process creates its OWN SQLAlchemy session/connection and calls the
production claim_next service until the queue is empty.

Run (requires a running PostgreSQL):
    python scripts/concurrency_claim_test.py
    # env: KAPIBARA_DSN=postgresql://app:app@localhost:5432/kapibara

Result summary is printed and, when --evidence is given, appended to the file.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSN = os.getenv("KAPIBARA_DSN", "postgresql://app:app@localhost:5432/kapibara")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    DSN.replace("postgresql://", "postgresql+psycopg://", 1),
)
ROUNDS = int(os.getenv("CLAIM_ROUNDS", "20"))
WORKERS = int(os.getenv("CLAIM_WORKERS", "10"))
TASKS_PER_ROUND = int(os.getenv("CLAIM_TASKS", "100"))

def worker_main(worker_id: int, database_url: str, project_root: str) -> list[int]:
    """Runs in a SEPARATE spawned process with its OWN connection."""
    os.environ["DATABASE_URL"] = database_url
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.database import SessionLocal
    from app.services.task_service import claim_next

    claimed: list[int] = []
    while True:
        with SessionLocal.begin() as session:
            task = claim_next(session, f"proc-{worker_id}")
            if task is None:
                break
            claimed.append(task.id)
    return claimed


def seed_round(dsn: str, group_id: int, round_no: int) -> set[int]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (group_id, name, status) "
                "SELECT %s, %s || n, 'pending' FROM generate_series(1, %s) AS n "
                "RETURNING id",
                (group_id, f"stress-r{round_no}-t", TASKS_PER_ROUND),
            )
            seeded_ids = {row[0] for row in cur.fetchall()}
        conn.commit()
    return seeded_ids


def ensure_group(dsn: str) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO groups (name, parameter_overrides) VALUES ('stress', NULL) RETURNING id"
            )
            gid = cur.fetchone()[0]
        conn.commit()
    return gid


def main() -> int:
    ctx = mp.get_context("spawn")
    dsn = DSN
    group_id = ensure_group(dsn)

    total_claims = 0
    duplicate_claims = 0
    missing_tasks = 0
    foreign_claims = 0
    per_round: list[str] = []

    for round_no in range(1, ROUNDS + 1):
        seeded_ids = seed_round(dsn, group_id, round_no)

        with ctx.Pool(WORKERS) as pool:
            results = pool.starmap(
                worker_main,
                [(i, DATABASE_URL, str(ROOT)) for i in range(WORKERS)],
            )

        all_ids: list[int] = [tid for sub in results for tid in sub]
        target_ids = [tid for tid in all_ids if tid in seeded_ids]
        foreign = len(all_ids) - len(target_ids)
        total_claims += len(target_ids)
        foreign_claims += foreign
        duplicates = len(target_ids) - len(set(target_ids))
        duplicate_claims += duplicates
        missing = len(seeded_ids - set(target_ids))
        missing_tasks += missing

        # the claimed rows must be exactly this round's seeded tasks
        import psycopg

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'pending' AND id = ANY(%s)",
                    (list(seeded_ids),),
                )
                leftover = cur.fetchone()[0]
        per_round.append(
            f"round={round_no:02d} claimed={len(target_ids)} unique={len(set(target_ids))} "
            f"duplicates={duplicates} missing={missing} pending_left={leftover} "
            f"foreign_claims={foreign} "
            f"per_worker={[len(r) for r in results]}"
        )

    verdict = "PASS" if (duplicate_claims == 0 and missing_tasks == 0) else "FAIL"
    summary = (
        f"rounds={ROUNDS} workers={WORKERS} tasks_per_round={TASKS_PER_ROUND}\n"
        f"total_claims={total_claims}\n"
        f"duplicate_claims={duplicate_claims}\n"
        f"missing_tasks={missing_tasks}\n"
        f"foreign_claims={foreign_claims}\n"
        f"RESULT={verdict}\n"
    )

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    print("\n".join(per_round))
    print("-" * 60)
    print(summary, end="")

    if "--evidence" in sys.argv:
        evidence_dir = os.path.join(os.path.dirname(__file__), "..", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        path = os.path.join(evidence_dir, "claim_concurrency.txt")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                f"=== {timestamp} (spawn, production claim service, independent sessions) ===\n"
            )
            fh.write("\n".join(per_round) + "\n")
            fh.write(summary + "\n")
        print(f"[evidence written to {os.path.abspath(path)}]", file=sys.stderr)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
