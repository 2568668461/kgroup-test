"""Multi-process idempotency test — 5 REAL processes, 5 REAL connections.

Scenario A: a running task's current step receives 5 concurrent SUCCESS
reports. All must answer without a server error, and exactly ONE execution
log row must exist afterwards.

Scenario B: 5 concurrent MIXED reports (success/failure) for the same step.
The final log must be success (monotonic merge — success is never downgraded).

Run against a running API server:
    python scripts/idempotency_test.py
    # env: KAPIBARA_BASE_URL=http://localhost:8000
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import uuid
from datetime import UTC, datetime

BASE_URL = os.getenv("KAPIBARA_BASE_URL", "http://localhost:8000")
N_PROCESSES = 5


def report_once(task_id: int, sequence: int, token: str, success: bool) -> tuple[int, dict | None]:
    """Runs in a SEPARATE spawned process with its own HTTP connection."""
    import urllib.request

    payload = json.dumps({"claim_token": token, "success": success}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/api/tasks/{task_id}/steps/{sequence}/complete",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def fetch_json(path: str) -> dict:
    import urllib.request

    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=30) as resp:
        return json.loads(resp.read().decode())


def post_json(path: str, payload: dict) -> tuple[int, dict | None]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def setup_running_task() -> tuple[int, int, str]:
    gid = post_json(
        "/api/groups", {"name": f"idem-{uuid.uuid4().hex[:6]}", "parameter_overrides": None}
    )[1]["id"]
    tid = post_json(
        "/api/tasks",
        {
            "group_id": gid,
            "name": f"idem-task-{uuid.uuid4().hex[:6]}",
            "base_parameters": {"k": "v"},
            "steps": [{"name": "s1"}, {"name": "s2"}],
        },
    )[1]["id"]
    token = post_json("/api/workers/idem-w/claim-next", {})[1]["claim_token"]
    status, _ = post_json(f"/api/tasks/{tid}/start", {"claim_token": token})
    assert status == 200, "setup failed"
    return tid, 1, token


def run_concurrent(task_id: int, sequence: int, token: str, successes: list[bool]):
    ctx = mp.get_context("spawn")
    args = [(task_id, sequence, token, s) for s in successes]
    with ctx.Pool(N_PROCESSES) as pool:
        results = pool.starmap(report_once, args)
    return results


def main() -> int:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    lines: list[str] = []

    # Scenario A: 5x success
    tid, seq, token = setup_running_task()
    results = run_concurrent(tid, seq, token, [True] * N_PROCESSES)
    detail = fetch_json(f"/api/tasks/{tid}")
    log_count = len(detail["logs"])
    outcomes = [r[1].get("outcome") if r[1] else None for r in results]
    server_errors = sum(1 for r in results if r[0] >= 500)

    lines.append(f"[A] task={tid} step={seq} 5x concurrent success")
    for i, (status, body) in enumerate(results):
        lines.append(f"    req#{i+1}: HTTP {status} outcome={body.get('outcome') if body else '-'}")
    lines.append(
        f"    server_errors={server_errors} log_rows={log_count} (expected 1) "
        f"final_task_status={detail['status']}"
    )
    a_pass = server_errors == 0 and log_count == 1 and "created" in outcomes

    # Scenario B: mixed success/failure
    tid_b, seq_b, token_b = setup_running_task()
    mixed = [True, False, True, False, False]
    results_b = run_concurrent(tid_b, seq_b, token_b, mixed)
    detail_b = fetch_json(f"/api/tasks/{tid_b}")
    final_success = detail_b["logs"][0]["success"] if detail_b["logs"] else None
    server_errors_b = sum(1 for r in results_b if r[0] >= 500)
    lines.append(f"[B] task={tid_b} step={seq_b} mixed reports={mixed}")
    for i, (status, body) in enumerate(results_b):
        lines.append(f"    req#{i+1}: HTTP {status} outcome={body.get('outcome') if body else '-'}")
    lines.append(
        f"    server_errors={server_errors_b} log_rows={len(detail_b['logs'])} "
        f"final_success={final_success} (expected True) task_status={detail_b['status']}"
    )
    b_pass = server_errors_b == 0 and len(detail_b["logs"]) == 1 and final_success is True

    verdict = "PASS" if (a_pass and b_pass) else "FAIL"
    summary = (
        f"processes={N_PROCESSES} scenarioA={'PASS' if a_pass else 'FAIL'} "
        f"scenarioB={'PASS' if b_pass else 'FAIL'}\nRESULT={verdict}\n"
    )
    print("\n".join(lines))
    print("-" * 60)
    print(summary, end="")

    if "--evidence" in sys.argv:
        evidence_dir = os.path.join(os.path.dirname(__file__), "..", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        path = os.path.join(evidence_dir, "idempotency.txt")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"=== {timestamp} (spawn processes, independent HTTP connections) ===\n")
            fh.write("\n".join(lines) + "\n")
            fh.write(summary + "\n")
        print(f"[evidence written to {os.path.abspath(path)}]", file=sys.stderr)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
