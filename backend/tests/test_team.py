"""Team page: admins pick managers and admins, managers pick experts and new hires, and the API enforces it."""
import pytest

import access
from store import store

BOSS = "boss@acme.com"   # admin through ADMIN_EMAILS


def h(email: str) -> dict:
    return {"X-Dev-User": email}


def grant(client, by: str, email: str, role: str, on: bool = True):
    return client.post("/api/team/roles", json={"email": email, "role": role, "on": on}, headers=h(by))


@pytest.fixture
def real(monkeypatch):
    """A real team: demo mode off, one admin set on the server."""
    monkeypatch.setattr(access, "DEMO_MODE", False)
    monkeypatch.setattr(access, "ADMIN_EMAILS", {BOSS})


def test_demo_mode_everyone_is_admin_and_sees_the_sample_team(client):
    me = client.get("/api/me", headers=h("judge@gmail.com")).json()
    assert me["admin"] and me["can_manage_team"]
    team = client.get("/api/team", headers=h("judge@gmail.com")).json()
    roles = {p["email"]: p["roles"] for p in team["people"]}
    assert roles["priya@example.com"] == ["expert"] and roles["john@example.com"] == ["newhire"]
    assert team["demo"] and "manager" in team["grantable"]


def test_admin_grants_manager_and_the_manager_grants_experts(client, real):
    assert client.get("/api/me", headers=h(BOSS)).json()["admin"]
    assert grant(client, BOSS, "Mgr@Acme.com", "manager").json()["roles"] == ["manager"]
    me = client.get("/api/me", headers=h("mgr@acme.com")).json()
    assert me["roles"] == ["manager"] and not me["admin"]
    assert grant(client, "mgr@acme.com", "dev@acme.com", "expert").status_code == 200
    assert client.get("/api/me", headers=h("dev@acme.com")).json()["roles"] == ["expert"]
    assert client.get("/api/team", headers=h("mgr@acme.com")).json()["grantable"] == ["expert", "newhire"]


def test_managers_cant_make_managers_or_touch_managers_admins_or_themselves(client, real):
    grant(client, BOSS, "mgr@acme.com", "manager")
    grant(client, BOSS, "mgr2@acme.com", "manager")
    assert grant(client, "mgr@acme.com", "x@acme.com", "manager").status_code == 403
    assert grant(client, "mgr@acme.com", "x@acme.com", "admin").status_code == 403
    assert grant(client, "mgr@acme.com", "mgr2@acme.com", "expert").status_code == 403
    assert grant(client, "mgr@acme.com", BOSS, "newhire").status_code == 403
    assert grant(client, "mgr@acme.com", "mgr@acme.com", "expert").status_code == 403
    assert client.delete("/api/team/mgr2@acme.com", headers=h("mgr@acme.com")).status_code == 403


def test_experts_and_new_hires_cant_see_or_change_the_team(client, real):
    grant(client, BOSS, "dev@acme.com", "expert")
    assert client.get("/api/team", headers=h("dev@acme.com")).status_code == 403
    assert grant(client, "dev@acme.com", "x@acme.com", "expert").status_code == 403


def test_removing_a_role_takes_away_access(client, real):
    grant(client, BOSS, "dev@acme.com", "expert")
    assert client.get("/api/me", headers=h("dev@acme.com")).status_code == 200
    grant(client, BOSS, "dev@acme.com", "expert", on=False)
    assert client.get("/api/me", headers=h("dev@acme.com")).status_code == 403


def test_remove_from_team_sticks(client, real):
    grant(client, BOSS, "dev@acme.com", "expert")
    grant(client, BOSS, "dev@acme.com", "newhire")
    client.get("/api/me", headers=h("dev@acme.com"))   # signs in, so the user record caches the roles
    assert client.delete("/api/team/dev@acme.com", headers=h(BOSS)).status_code == 200
    assert client.get("/api/me", headers=h("dev@acme.com")).status_code == 403
    person = next(p for p in client.get("/api/team", headers=h(BOSS)).json()["people"] if p["email"] == "dev@acme.com")
    assert person["roles"] == [] and person["signed_in"]


def test_server_admins_cant_be_removed_and_the_last_admin_stays(client, real, monkeypatch):
    assert grant(client, BOSS, BOSS, "admin", on=False).status_code == 400
    monkeypatch.setattr(access, "ADMIN_EMAILS", set())
    access._set_grant("solo@acme.com", "admin", True, "test")
    assert grant(client, "solo@acme.com", "solo@acme.com", "admin", on=False).status_code == 400
    assert grant(client, "solo@acme.com", "two@acme.com", "admin").status_code == 200
    assert grant(client, "solo@acme.com", "solo@acme.com", "admin", on=False).status_code == 200


def test_first_person_in_an_empty_workspace_becomes_admin(client, real, monkeypatch):
    monkeypatch.setattr(access, "ADMIN_EMAILS", set())
    for doc_id in store.list("invites"):
        store.delete("invites", doc_id)
    me = client.get("/api/me", headers=h("first@acme.com")).json()
    assert me["admin"] and me["roles"] == ["manager"]
    assert client.get("/api/me", headers=h("second@acme.com")).status_code == 403


def test_turning_demo_mode_off_takes_away_demo_access(client, monkeypatch):
    client.get("/api/me", headers=h("judge@gmail.com"))   # demo mode: the user record caches all three roles
    monkeypatch.setattr(access, "DEMO_MODE", False)
    monkeypatch.setattr(access, "ADMIN_EMAILS", {BOSS})
    assert client.get("/api/me", headers=h("judge@gmail.com")).status_code == 403


def test_bad_email_is_refused(client):
    assert grant(client, "judge@gmail.com", "not-an-email", "expert").status_code == 400
