"""Multi-process concurrent claim test — REAL processes, REAL connections.

  10 worker processes (multiprocessing spawn) x 100 pending tasks x 20 rounds.

Each process opens its OWN psycopg connection and loops the atomic
FOR UPDATE SKIP LOCKED claim statement until the queue is empty.

Run (requires a running PostgreSQL):
    python scripts/concurrency_claim_test.py
    # env: KAPIBARA_DSN=postgresql://app:app@localhost:5432/kapibara

Result summary is printed and, when --evidence is given, appended to the file.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import uuid
from datetime import UTC, datetime

DSN = os.getenv("KAPIBARA_DSN", "postgresql://app:app@localhost:5432/kapibara")
ROUNDS = int(os.getenv("CLAIM_ROUNDS", "20"))
WORKERS = int(os.getenv("CLAIM_WORKERS", "10"))
TASKS_PER_ROUND = int(os.getenv("CLAIM_TASKS", "100"))

CLAIM_SQL = """
WITH candidate AS (
    SELECT id
    FROM tasks
    WHERE status = 'pending'
    ORDER BY created_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE tasks
SET status = 'claimed',
    claimed_by = %(worker_id)s,
    claim_token = %(claim_token)s,
    claimed_at = NOW()
FROM candidate
WHERE tasks.id = candidate.id
RETURNING tasks.id;
"""


def worker_main(worker_id: int, dsn: str) -> list[int]:
    """Runs in a SEPARATE spawned process with its OWN connection."""
    import psycopg

    claimed: list[int] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute(CLAIM_SQL, {"worker_id": f"proc-{worker_id}", "claim_token": uuid.uuid4()})
                row = cur.fetchone()
            if row is None:
                break
            claimed.append(row[0])
    return claimed


def seed_round(dsn: str, group_id: int, round_no: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (group_id, name, status) "
                "SELECT %s, %s || n, 'pending' FROM generate_series(1, %s) AS n",
                (group_id, f"stress-r{round_no}-t", TASKS_PER_ROUND),
            )
        conn.commit()


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
    per_round: list[str] = []

    for round_no in range(1, ROUNDS + 1):
        seed_round(dsn, group_id, round_no)

        with ctx.Pool(WORKERS) as pool:
            results = pool.starmap(worker_main, [(i, dsn) for i in range(WORKERS)])

        all_ids: list[int] = [tid for sub in results for tid in sub]
        total_claims += len(all_ids)
        duplicates = len(all_ids) - len(set(all_ids))
        duplicate_claims += duplicates
        missing = TASKS_PER_ROUND - len(set(all_ids))
        missing_tasks += missing

        # the claimed rows must be exactly this round's seeded tasks
        import psycopg

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'pending' AND name LIKE %s",
                    (f"stress-r{round_no}%",),
                )
                leftover = cur.fetchone()[0]
        missing += leftover
        missing_tasks += leftover

        per_round.append(
            f"round={round_no:02d} claimed={len(all_ids)} unique={len(set(all_ids))} "
            f"duplicates={duplicates} pending_left={leftover} "
            f"per_worker={[len(r) for r in results]}"
        )

    verdict = "PASS" if (duplicate_claims == 0 and missing_tasks == 0) else "FAIL"
    summary = (
        f"rounds={ROUNDS} workers={WORKERS} tasks_per_round={TASKS_PER_ROUND}\n"
        f"total_claims={total_claims}\n"
        f"duplicate_claims={duplicate_claims}\n"
        f"missing_tasks={missing_tasks}\n"
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
            fh.write(f"=== {timestamp} (spawn, direct psycopg connections) ===\n")
            fh.write("\n".join(per_round) + "\n")
            fh.write(summary + "\n")
        print(f"[evidence written to {os.path.abspath(path)}]", file=sys.stderr)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
