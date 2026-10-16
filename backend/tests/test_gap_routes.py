"""The gap -> request -> recheck routes, through the real API. They must behave the same before and after the agent."""
from conftest import ALEMBIC, PLAN, STAGING, as_role
from store import store


def test_sample_plan_coverage_and_gaps(client):
    r = client.get(f"/api/plans/{PLAN}").json()
    assert r["plan"]["coverage_percent"] == 53            # 8 of 15 importance points recorded
    assert [g["topic"] for g in r["plan"]["gaps"]] == [STAGING, ALEMBIC, "Debug an error from the production logs"]
    assert [q["id"] for q in r["requests"]] == ["sample-req-staging"]


def test_ask_expert_creates_one_open_request(client):
    r = client.post("/api/requests", json={"plan_id": PLAN, "topic": ALEMBIC, "expert_email": "Arjun@Example.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "open" and body["expert_email"] == "arjun@example.com" and body["importance"] == 2
    assert store.get("requests", body["id"])["topic"] == ALEMBIC
    assert "expert" in store.get("invites", "arjun@example.com")["roles"]

    again = client.post("/api/requests", json={"plan_id": PLAN, "topic": ALEMBIC, "expert_email": "priya@example.com"})
    assert again.status_code == 409 and "arjun@example.com" in again.json()["detail"]
    assert sum(r["topic"] == ALEMBIC for r in store.list("requests").values()) == 1


def test_ask_expert_refuses_pending_unknown_and_missing(client):
    pending = client.post("/api/requests", json={"plan_id": PLAN, "topic": STAGING, "expert_email": "x@example.com"})
    assert pending.status_code == 409 and pending.json()["detail"] == "Already requested from ravi@example.com."
    unknown = client.post("/api/requests", json={"plan_id": PLAN, "topic": "Make up a task", "expert_email": "x@example.com"})
    assert unknown.status_code == 400
    missing = client.post("/api/requests", json={"plan_id": "nope", "topic": ALEMBIC, "expert_email": "x@example.com"})
    assert missing.status_code == 404
    assert len(store.list("requests")) == 1


def test_only_managers_ask_experts(client):
    h = as_role(client, "john@example.com", "newhire")
    r = client.post("/api/requests", json={"plan_id": PLAN, "topic": ALEMBIC, "expert_email": "x@example.com"}, headers=h)
    assert r.status_code == 403
    assert len(store.list("requests")) == 1


def test_decline_then_ask_someone_else(client):
    ravi = as_role(client, "ravi@example.com", "expert")
    assert client.post("/api/requests/sample-req-staging/decline", json={"reason": "on leave", "suggest": "meera@"},
                       headers=ravi).json() == {"id": "sample-req-staging", "status": "declined"}
    assert client.post("/api/requests/sample-req-staging/decline", json={}, headers=ravi).status_code == 409
    other = as_role(client, "someone@example.com", "expert")
    assert client.post("/api/requests/sample-req-staging/decline", json={}, headers=other).status_code == 404

    r = client.post("/api/requests", json={"plan_id": PLAN, "topic": STAGING, "expert_email": "meera@example.com"})
    assert r.status_code == 200 and r.json()["id"] != "sample-req-staging"
    assert store.get("requests", "sample-req-staging")["status"] == "declined"   # the decline is kept


def test_recheck_without_new_playbooks_changes_nothing(client):
    r = client.post(f"/api/plans/{PLAN}/recheck").json()
    assert (r["coverage_before"], r["coverage_after"], r["newly_covered"], r["gaps"]) == (53, 53, [], 3)
    assert client.post("/api/plans/nope/recheck").status_code == 404


def test_fill_gap_raises_coverage_and_closes_its_request(client):
    r = client.post(f"/api/plans/{PLAN}/fill", json={"gap": STAGING, "playbook_id": "sample-pb-env"})
    assert r.status_code == 200, r.text
    assert (r.json()["coverage_before"], r.json()["coverage_after"]) == (53, 73)   # +3 points: 11 of 15
    assert store.get("requests", "sample-req-staging")["status"] == "done"
    assert client.post(f"/api/plans/{PLAN}/fill", json={"gap": STAGING, "playbook_id": "sample-pb-env"}).status_code == 409
    assert client.post(f"/api/plans/{PLAN}/fill", json={"gap": ALEMBIC, "playbook_id": "nope"}).status_code == 404


def test_recording_answers_the_request(client):
    ravi = as_role(client, "ravi@example.com", "expert")
    r = client.post("/api/capture", headers=ravi, data={"request_id": "sample-req-staging"},
                    files={"file": ("deploy.webm", b"not really a video", "video/webm")})
    assert r.status_code == 200, r.text
    out = r.json()["request"]
    assert out["id"] == "sample-req-staging" and out["coverage_before"] == 53   # mock Gemini matches nothing new
    assert store.get("requests", "sample-req-staging")["status"] == "done"
    again = client.post("/api/capture", headers=ravi, data={"request_id": "sample-req-staging"},
                        files={"file": ("deploy.webm", b"x", "video/webm")})
    assert again.status_code == 409


def test_people_lists_invited_experts(client):
    client.post("/api/requests", json={"plan_id": PLAN, "topic": ALEMBIC, "expert_email": "arjun@example.com"})
    assert "arjun@example.com" in client.get("/api/people").json()["expert"]
