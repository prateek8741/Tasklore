"""services.py rules the agent will rely on: no duplicate requests, even when retried or sent at the same moment."""
import threading

import pytest

import services
from conftest import ALEMBIC, PLAN, STAGING
from store import store

MANAGER = {"uid": "m1", "email": "boss@example.com", "name": "Boss", "role": "manager"}


def test_request_id_is_stable_per_gap_and_attempt(client):
    assert services.request_id_for(PLAN, ALEMBIC, 0) == services.request_id_for(PLAN, ALEMBIC, 0)
    assert services.request_id_for(PLAN, ALEMBIC, 0) != services.request_id_for(PLAN, ALEMBIC, 1)
    assert services.request_id_for(PLAN, ALEMBIC, 0) != services.request_id_for(PLAN, STAGING, 0)
    r = services.create_request(PLAN, ALEMBIC, "arjun@example.com", MANAGER)
    assert r["id"] == services.request_id_for(PLAN, ALEMBIC, 0)


def test_store_create_never_overwrites(client):
    assert store.create("requests", {"a": 1}, "x1") is True
    assert store.create("requests", {"a": 2}, "x1") is False
    assert store.get("requests", "x1") == {"a": 1}


def test_simultaneous_requests_make_exactly_one(client):
    results = []

    def ask(email):
        try:
            results.append(services.create_request(PLAN, ALEMBIC, email, MANAGER)["expert_email"])
        except services.ServiceError as e:
            results.append(e.status)

    threads = [threading.Thread(target=ask, args=(f"e{i}@example.com",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str).count(409) == 7
    assert sum(r["topic"] == ALEMBIC for r in store.list("requests").values()) == 1


def test_refused_request_invites_nobody(client):
    with pytest.raises(services.ServiceError) as e:
        services.create_request(PLAN, STAGING, "intruder@example.com", MANAGER)
    assert e.value.status == 409
    assert store.get("invites", "intruder@example.com") is None


def test_after_a_decline_the_next_ask_gets_a_new_id(client):
    store.put("requests", {**store.get("requests", "sample-req-staging"), "status": "declined"}, doc_id="sample-req-staging")
    r = services.create_request(PLAN, STAGING, "meera@example.com", MANAGER)
    assert r["id"] == services.request_id_for(PLAN, STAGING, 1)
    assert store.get("requests", "sample-req-staging")["status"] == "declined"


def test_unknown_plan_and_gap_are_refused(client):
    for plan_id, topic, status in (("nope", ALEMBIC, 404), (PLAN, "Invented task", 400)):
        with pytest.raises(services.ServiceError) as e:
            services.create_request(plan_id, topic, "a@example.com", MANAGER)
        assert e.value.status == status
