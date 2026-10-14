"""Who may do what: roles on top of the login check in auth.py.

Roles
  manager   builds plans, assigns them to new hires, asks experts to record gaps, sees everything
  expert    records or imports playbooks, answers (or declines) recording requests
  newhire   sees only the plans assigned to them

How people get roles (stored in users/{uid}.roles; one person may hold several)
  - The first person ever to sign in becomes a manager.
  - Assigning a plan to an email invites it as a new hire; asking an expert invites that email as an expert
    (invites/{email}). The roles are picked up when that person signs in with Google.
  - DEMO_MODE=1 (the hackathon default): every signed-in user holds all three roles and switches between them
    with "View as", so judges can try the whole loop with one Google account.

Every request acts as ONE role, the user's active_role. The API enforces it; the web app only mirrors it.
"""
import os
import time

from fastapi import Depends, HTTPException

import auth
from store import store

ROLES = ("manager", "expert", "newhire")
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"


def _email(user: dict) -> str:
    return (user.get("email") or "").strip().lower()


def invite(email: str, role: str) -> None:
    """Grant a role to an email; it takes effect when that person next signs in."""
    email = email.strip().lower()
    doc = store.get("invites", email) or {"roles": []}
    if role not in doc["roles"]:
        store.put("invites", {"roles": doc["roles"] + [role], "updated_at": time.time()}, doc_id=email)


def _any_manager() -> bool:
    return any("manager" in (u.get("roles") or []) for u in store.list("users").values())


def current_member(user: dict = Depends(auth.current_user)) -> dict:
    """The signed-in user with their roles and the role they are acting as. 403 if they have no role yet."""
    doc = store.get("users", user["uid"]) or {}
    roles = set(doc.get("roles") or [])
    if email := _email(user):
        roles |= set((store.get("invites", email) or {}).get("roles", []))
    if not roles and not _any_manager():
        roles = {"manager"}   # bootstrap: the first person in becomes the manager
    if DEMO_MODE:
        roles = set(ROLES)
    if not roles:
        raise HTTPException(403, "You don't have access yet. Ask your manager to assign you a plan or a recording request.")
    active = doc.get("active_role")
    if active not in roles:
        active = next(r for r in ROLES if r in roles)
    roles = [r for r in ROLES if r in roles]
    if roles != doc.get("roles") or active != doc.get("active_role") or _email(user) != doc.get("email"):
        store.put("users", {**doc, **user, "email": _email(user), "roles": roles, "active_role": active,
                            "last_seen": time.time()}, doc_id=user["uid"])
    return {**user, "email": _email(user), "roles": roles, "role": active, "demo": DEMO_MODE}


Member = Depends(current_member)


def need(*roles: str):
    """Route dependency: the user must be acting as one of these roles."""
    def check(member: dict = Member) -> dict:
        if member["role"] not in roles:
            names = " or ".join(r.replace("newhire", "new hire") for r in roles)
            raise HTTPException(403, f"This needs the {names} role. You are viewing as {member['role'].replace('newhire', 'new hire')}.")
        return member
    return check


def can_see_plan(member: dict, plan: dict) -> bool:
    return member["role"] == "manager" or (member["role"] == "newhire" and plan.get("assigned_to") == member["email"])


def plan_for(member: dict, plan_id: str) -> dict:
    """The plan, if this member may see it (managers: all; new hires: only plans assigned to them)."""
    plan = store.get("plans", plan_id)
    if not plan or not can_see_plan(member, plan):
        raise HTTPException(404, "Plan not found")   # 404, not 403: don't reveal other people's plans
    return plan
