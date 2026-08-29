"""Integration tests against a REAL PostgreSQL (no SQLite fallback).

Covers the full state machine, token ownership, ordering, monotonic log merge,
idempotency and the parameter snapshot freeze.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

BASE = "postgresql+psycopg://app:app@localhost:5432/kapibara_test"


def _make_group(client, overrides=None):
    r = client.post(
        "/api/groups", json={"name": f"g-{uuid.uuid4().hex[:6]}", "parameter_overrides": overrides}
    )
    r.raise_for_status()
    return r.json()["id"]


def _make_task(client, group_id, steps, base=None):
    payload = {
        "group_id": group_id,
        "name": f"t-{uuid.uuid4().hex[:6]}",
        "base_parameters": base,
        "steps": steps,
    }
    r = client.post("/api/tasks", json=payload)
    r.raise_for_status()
    return r.json()["id"]


def _claim(client, worker="w-1"):
    return client.post(f"/api/workers/{worker}/claim-next")


def _full_flow(client, n_steps=2, base=None, group_overrides=None, step_overrides=None):
    """Helper: create -> claim -> start; returns (task_id, claim_token)."""
    gid = _make_group(client, group_overrides)
    steps = [
        {"name": f"step-{i+1}", "parameter_overrides": (step_overrides or {}).get(i)}
        for i in range(n_steps)
    ]
    tid = _make_task(client, gid, steps, base)
    r = _claim(client)
    assert r.status_code == 200
    assert r.json()["task_id"] == tid
    token = r.json()["claim_token"]
    r = client.post(f"/api/tasks/{tid}/start", json={"claim_token": token})
    assert r.status_code == 200
    return tid, token


# ------------------------------------------------------------- lifecycle ----


def test_full_state_flow_pending_claimed_running_done(client):
    gid = _make_group(client)
    tid = _make_task(client, gid, [{"name": "a"}, {"name": "b"}])
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "pending"

    r = _claim(client)
    assert r.status_code == 200
    token = r.json()["claim_token"]
    assert r.json()["status"] == "claimed"

    r = client.post(f"/api/tasks/{tid}/start", json={"claim_token": token})
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True}
    )
    assert r.json()["task_status"] == "running"

    r = client.post(
        f"/api/tasks/{tid}/steps/2/complete", json={"claim_token": token, "success": True}
    )
    assert r.json()["task_status"] == "done"
    assert client.get(f"/api/tasks/{tid}").json()["finished_at"] is not None


def test_claim_returns_204_when_exhausted(client):
    gid = _make_group(client)
    _make_task(client, gid, [{"name": "a"}])
    assert _claim(client).status_code == 200
    r = _claim(client, worker="w-2")
    assert r.status_code == 204


# ------------------------------------------------------------- ownership ----


def test_wrong_token_cannot_start(client):
    gid = _make_group(client)
    _make_task(client, gid, [{"name": "a"}])
    _claim(client)
    r = client.post("/api/tasks/999999/start", json={"claim_token": str(uuid.uuid4())})
    assert r.status_code == 404
    # real task, wrong token
    r2 = client.post("/api/tasks/1/start", json={"claim_token": str(uuid.uuid4())})
    assert r2.status_code == 403


def test_wrong_token_cannot_report_step(client):
    tid, token = _full_flow(client)
    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": str(uuid.uuid4()), "success": True}
    )
    assert r.status_code == 403


def test_cannot_start_twice(client):
    tid, token = _full_flow(client)
    r = client.post(f"/api/tasks/{tid}/start", json={"claim_token": token})
    assert r.status_code == 409


def test_unclaimed_task_cannot_report(client):
    gid = _make_group(client)
    tid = _make_task(client, gid, [{"name": "a"}])
    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": str(uuid.uuid4()), "success": True}
    )
    assert r.status_code == 409  # not started yet


# -------------------------------------------------------------- ordering ----


def test_out_of_order_step_rejected(client):
    tid, token = _full_flow(client, n_steps=3)
    r = client.post(
        f"/api/tasks/{tid}/steps/2/complete", json={"claim_token": token, "success": True}
    )
    assert r.status_code == 409
    r = client.post(
        f"/api/tasks/{tid}/steps/3/complete", json={"claim_token": token, "success": True}
    )
    assert r.status_code == 409


def test_unknown_step_rejected(client):
    tid, token = _full_flow(client)
    r = client.post(
        f"/api/tasks/{tid}/steps/9/complete", json={"claim_token": token, "success": True}
    )
    assert r.status_code == 404


# ----------------------------------------------------------- monotonicity --


def test_failure_marks_task_failed_then_success_upgrades(client):
    tid, token = _full_flow(client, n_steps=2)
    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": False}
    )
    assert r.json()["task_status"] == "failed"
    failed_at = client.get(f"/api/tasks/{tid}").json()["finished_at"]
    assert failed_at is not None

    # a later success for the SAME step monotonically upgrades the log
    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True}
    )
    assert r.json()["outcome"] == "upgraded"
    assert r.json()["task_status"] == "running"
    detail = client.get(f"/api/tasks/{tid}").json()
    assert detail["logs"][0]["success"] is True
    assert detail["finished_at"] is None

    # task continues and completes
    r = client.post(
        f"/api/tasks/{tid}/steps/2/complete", json={"claim_token": token, "success": True}
    )
    assert r.json()["task_status"] == "done"
    assert client.get(f"/api/tasks/{tid}").json()["finished_at"] > failed_at


@pytest.mark.parametrize(
    ("claimed_by", "claim_token"),
    [(None, uuid.uuid4()), ("worker-only", None)],
)
def test_database_rejects_partial_claim_ownership(client, claimed_by, claim_token):
    from app.database import SessionLocal

    gid = _make_group(client)
    with pytest.raises(IntegrityError), SessionLocal.begin() as session:
        session.execute(
            text(
                "INSERT INTO tasks (group_id, name, status, claimed_by, claim_token) "
                "VALUES (:group_id, 'invalid-owner', 'claimed', :claimed_by, :claim_token)"
            ),
            {"group_id": gid, "claimed_by": claimed_by, "claim_token": claim_token},
        )


def test_success_never_downgraded_by_late_failure(client):
    tid, token = _full_flow(client, n_steps=2)
    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True}
    )
    assert r.json()["outcome"] == "created"

    r = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": False}
    )
    assert r.json()["outcome"] == "duplicate_no_change"
    assert r.json()["log"]["success"] is True
    assert r.json()["task_status"] == "running"

    detail = client.get(f"/api/tasks/{tid}").json()
    assert [log["success"] for log in detail["logs"]] == [True]


def test_done_task_duplicate_reports_return_final_result(client):
    tid, token = _full_flow(client, n_steps=2)
    for seq in (1, 2):
        client.post(f"/api/tasks/{tid}/steps/{seq}/complete", json={"claim_token": token, "success": True})
    r = client.post(
        f"/api/tasks/{tid}/steps/2/complete", json={"claim_token": token, "success": False}
    )
    assert r.status_code == 200
    assert r.json()["task_status"] == "done"
    assert r.json()["log"]["success"] is True


# ------------------------------------------------------------ idempotency --


def test_duplicate_success_reports_single_log_row(client):
    tid, token = _full_flow(client, n_steps=2)
    outcomes = []
    for _ in range(5):
        r = client.post(
            f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True}
        )
        assert r.status_code == 200
        outcomes.append(r.json()["outcome"])
    assert outcomes[0] == "created"
    assert outcomes[1:] == ["duplicate_no_change"] * 4

    detail = client.get(f"/api/tasks/{tid}").json()
    assert len(detail["logs"]) == 1


def test_completed_at_keeps_earliest_timestamp(client):
    tid, token = _full_flow(client)
    first = client.post(
        f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True}
    ).json()["log"]["completed_at"]
    for _ in range(3):
        client.post(f"/api/tasks/{tid}/steps/1/complete", json={"claim_token": token, "success": True})
    again = client.get(f"/api/tasks/{tid}").json()["logs"][0]["completed_at"]
    assert first == again


# --------------------------------------------------------------- snapshot --


def test_parameters_resolve_with_all_layers(client):
    tid, token = _full_flow(
        client,
        n_steps=3,
        base={"region": "us", "timeout": 30, "extra": True},
        group_overrides={"region": "cn-north", "timeout": 60, "tag": ""},
        step_overrides={0: {"timeout": 90}, 1: {"timeout": "", "mode": "fast"}, 2: None},
    )
    detail = client.get(f"/api/tasks/{tid}").json()
    assert detail["group_parameters_snapshot"] == {"region": "cn-north", "timeout": 60, "tag": ""}
    assert detail["steps"][0]["resolved_parameters"] == {
        "region": "cn-north", "timeout": 90, "extra": True, "tag": "",
    }
    assert detail["steps"][1]["resolved_parameters"] == {
        "region": "cn-north", "timeout": 90, "extra": True, "tag": "", "mode": "fast",
    }
    assert detail["steps"][2]["resolved_parameters"] == detail["steps"][1]["resolved_parameters"]


def test_group_edit_after_start_does_not_affect_snapshot(client):
    gid = _make_group(client, {"region": "cn", "timeout": 60})
    tid = _make_task(
        client, gid,
        [{"name": "a", "parameter_overrides": None}, {"name": "b"}],
        base={"region": "us", "timeout": 30},
    )
    _claim(client)
    token = _claim_token_of(client, tid)
    r = client.post(f"/api/tasks/{tid}/start", json={"claim_token": token})
    assert r.status_code == 200

    # mutate the group AFTER the task started — directly in the DB
    import json

    from sqlalchemy import text

    from app.database import SessionLocal

    with SessionLocal() as session:
        session.execute(
            text("UPDATE groups SET parameter_overrides = CAST(:p AS jsonb) WHERE id = :gid"),
            {"p": json.dumps({"region": "MUTATED"}), "gid": gid},
        )
        session.commit()

    detail = client.get(f"/api/tasks/{tid}").json()
    assert detail["group_parameters_snapshot"] == {"region": "cn", "timeout": 60}


def _claim_token_of(client, tid):
    return client.get(f"/api/tasks/{tid}").json()["claim_token"]


def test_two_workers_claim_different_tasks(client):
    gid = _make_group(client)
    t1 = _make_task(client, gid, [{"name": "a"}])
    t2 = _make_task(client, gid, [{"name": "a"}])
    r1 = _claim(client, worker="w-1").json()
    r2 = _claim(client, worker="w-2").json()
    assert {r1["task_id"], r2["task_id"]} == {t1, t2}
    assert r1["claim_token"] != r2["claim_token"]
