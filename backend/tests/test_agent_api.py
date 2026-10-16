"""POST /api/agent/close-gaps and GET /api/agent/runs through the real API (mock model, in-memory store)."""
import json
import time

from agent import audit
from conftest import PLAN, as_role
from store import store


def test_manager_runs_the_agent_and_the_run_is_recorded(client):
    r = client.post("/api/agent/close-gaps", json={"plan_id": PLAN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and len(body["requests_created"]) == 2
    assert len([q for q in store.list("requests").values() if q["plan_id"] == PLAN]) == 3

    saved = store.get("agent_runs", body["run_id"])
    assert saved["status"] == "completed" and saved["by"]["email"] == "dev@example.com" and saved["finished_at"]
    assert saved["requests_created"] == body["requests_created"]
    stored = json.dumps(saved)
    assert "EXPERT-" not in stored and "GAP-" not in stored        # labels never reach the record

    runs = client.get("/api/agent/runs", params={"plan_id": PLAN}).json()["runs"]
    assert [x["run_id"] for x in runs] == [body["run_id"]]


def test_refused_runs_record_nothing(client):
    h = as_role(client, "john@example.com", "newhire")
    assert client.post("/api/agent/close-gaps", json={"plan_id": PLAN}, headers=h).status_code == 403
    assert client.get("/api/agent/runs", params={"plan_id": PLAN}, headers=h).status_code == 403
    assert client.post("/api/agent/close-gaps", json={"plan_id": "nope"}).status_code == 404
    assert client.post("/api/agent/close-gaps", json={"plan_id": ""}).status_code == 422
    assert store.list("agent_runs") == {}


def test_runs_per_hour_limit(client, monkeypatch):
    monkeypatch.setattr(audit, "RUNS_PER_HOUR", 2)
    for _ in range(2):
        assert client.post("/api/agent/close-gaps", json={"plan_id": PLAN}).status_code == 200
    third = client.post("/api/agent/close-gaps", json={"plan_id": PLAN})
    assert third.status_code == 429 and "2 times an hour" in third.json()["detail"]
    other = as_role(client, "other@example.com", "manager")     # the limit is per person
    assert client.post("/api/agent/close-gaps", json={"plan_id": PLAN}, headers=other).status_code == 200


def test_one_run_at_a_time_per_plan(client):
    store.put("agent_runs", {"plan_id": PLAN, "by": {"uid": "x"}, "status": "running", "started_at": time.time()}, doc_id="busy")
    assert client.post("/api/agent/close-gaps", json={"plan_id": PLAN}).status_code == 409
    store.put("agent_runs", {**store.get("agent_runs", "busy"), "started_at": time.time() - 600}, doc_id="busy")  # crashed long ago
    assert client.post("/api/agent/close-gaps", json={"plan_id": PLAN}).status_code == 200


def test_a_crash_inside_the_run_is_recorded_as_failed(client, monkeypatch):
    import agent

    async def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(agent, "run_close_gaps", boom)
    try:
        client.post("/api/agent/close-gaps", json={"plan_id": PLAN})
    except RuntimeError:
        pass   # TestClient re-raises server errors; a real client gets a 500
    (saved,) = store.list("agent_runs").values()
    assert saved["status"] == "failed" and saved["finished_at"]


def test_reset_clears_agent_runs(client):
    client.post("/api/agent/close-gaps", json={"plan_id": PLAN})
    assert client.post("/api/demo/reset").status_code == 200
    assert store.list("agent_runs") == {}
