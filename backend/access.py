"""Who may do what: roles on top of the login check in auth.py.

Roles
  manager   builds plans, assigns them to new hires, asks experts to record gaps, sees everything
  expert    records or imports playbooks, answers (or declines) recording requests
  newhire   sees only the plans assigned to them
Admin is a permission, not a role you "view as": an admin can do everything a manager can, and also decides who is
a manager or an admin. Managers can give and take away the expert and new hire roles (not their own, and not those
of managers or admins).

Where roles live: invites/{email} holds what was granted to an email ({roles, admin, granted: {role: {by, at}}}).
It works the same before and after that person first signs in.
  - ADMIN_EMAILS (a comma-separated server setting) are always admins, and can't be removed from the app.
  - In a completely empty workspace the first person to sign in becomes admin and manager.
  - Assigning a plan to an email grants it the new hire role; asking an expert grants the expert role.
  - The Team page (/api/team) grants and removes roles directly.
  - DEMO_MODE=1 (the hackathon default): every signed-in user holds all three roles and is an admin, and switches
    with "View as", so judges can try the whole loop with one Google account. Grants are still recorded, and take
    effect once demo mode is off.

Every request acts as ONE role, the user's active_role. The API enforces it; the web app only mirrors it.
"""
import os
import re
import time

from fastapi import Depends, HTTPException

import auth
from store import store

ROLES = ("manager", "expert", "newhire")
GRANTABLE = ("admin",) + ROLES
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(user: dict) -> str:
    return (user.get("email") or "").strip().lower()


def grants(email: str) -> dict:
    return store.get("invites", email.strip().lower()) or {}


def _set_grant(email: str, role: str, on: bool, by: str) -> None:
    email = email.strip().lower()
    doc = grants(email)
    if role == "admin":
        doc["admin"] = on
    else:
        held = set(doc.get("roles", [])) - {role} | ({role} if on else set())
        doc["roles"] = [r for r in ROLES if r in held]
    granted = dict(doc.get("granted") or {})
    if on:
        granted[role] = {"by": by, "at": time.time()}
    else:
        granted.pop(role, None)
    store.put("invites", {**doc, "roles": doc.get("roles", []), "granted": granted, "updated_at": time.time()}, doc_id=email)


def invite(email: str, role: str, by: str = "") -> None:
    """Grant a role to an email (if it doesn't hold it yet); it takes effect when that person next signs in."""
    if role not in grants(email).get("roles", []):
        _set_grant(email, role, True, by)


def _admins() -> set[str]:
    return ADMIN_EMAILS | {e for e, g in store.list("invites").items() if g.get("admin")}


def _workspace_empty() -> bool:
    return not ADMIN_EMAILS and not any(g.get("roles") or g.get("admin") for g in store.list("invites").values())


def current_member(user: dict = Depends(auth.current_user)) -> dict:
    """The signed-in user with their roles and the role they are acting as. 403 if they have no role yet."""
    doc = store.get("users", user["uid"]) or {}
    email = _email(user)
    # users/{uid}.roles is only a cache of what demo mode or the grants gave last time, never a source of roles:
    # in demo mode it says "all three" for everyone, which must not survive turning demo mode off.
    g = grants(email) if email else {}
    roles = set(g.get("roles") or [])
    admin = bool(email) and (email in ADMIN_EMAILS or bool(g.get("admin")))
    if email and not roles and not admin and _workspace_empty():   # bootstrap: the first person in runs the team
        _set_grant(email, "admin", True, "first sign-in")
        _set_grant(email, "manager", True, "first sign-in")
        roles, admin = {"manager"}, True
    if admin:
        roles.add("manager")   # an admin can do everything a manager can
    if DEMO_MODE:
        roles, admin = set(ROLES), True
    if not roles:
        raise HTTPException(403, "You don't have access yet. Ask your manager to assign you a plan or a recording request.")
    active = doc.get("active_role")
    if active not in roles:
        active = next(r for r in ROLES if r in roles)
    roles = [r for r in ROLES if r in roles]
    if roles != doc.get("roles") or active != doc.get("active_role") or email != doc.get("email") or admin != doc.get("admin"):
        store.put("users", {**doc, **user, "email": email, "roles": roles, "admin": admin, "active_role": active,
                            "last_seen": time.time()}, doc_id=user["uid"])
    return {**user, "email": email, "roles": roles, "role": active, "admin": admin,
            "can_manage_team": admin or "manager" in roles, "demo": DEMO_MODE}


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


# ---------------- the Team page: who holds which role, and changing it ----------------
def _person(email: str, g: dict, user: dict, plans: int) -> dict:
    return {"email": email, "name": user.get("name") or "", "signed_in": bool(user), "last_seen": user.get("last_seen"),
            "roles": [r for r in ROLES if r in (g.get("roles") or [])],
            "admin": email in ADMIN_EMAILS or bool(g.get("admin")), "admin_fixed": email in ADMIN_EMAILS,
            "granted": g.get("granted") or {}, "plans": plans}


def team() -> list[dict]:
    """Everyone who signed in or was granted a role: admins first, then managers, experts, new hires."""
    users = {u["email"]: u for u in store.list("users").values() if u.get("email")}
    invites = store.list("invites")
    plans: dict[str, int] = {}
    for p in store.list("plans").values():
        if a := (p.get("assigned_to") or "").lower():
            plans[a] = plans.get(a, 0) + 1
    people = [_person(e, invites.get(e, {}), users.get(e, {}), plans.get(e, 0)) for e in set(users) | set(invites) | ADMIN_EMAILS]
    people = [p for p in people if p["signed_in"] or p["admin"] or p["roles"]]   # removed and never signed in: gone
    rank = lambda p: (not p["admin"], *[r not in p["roles"] for r in ROLES], (p["name"] or p["email"]).lower())
    return sorted(people, key=rank)


def _check(actor: dict, email: str, role: str, on: bool) -> None:
    """Raise if `actor` may not grant (on) or remove `role` for `email`."""
    if not actor.get("can_manage_team"):
        raise HTTPException(403, "Only managers and admins can change roles.")
    g = grants(email)
    target_admin = email in ADMIN_EMAILS or bool(g.get("admin"))
    if role in ("admin", "manager") and not actor["admin"]:
        raise HTTPException(403, "Only an admin can change who is a manager or an admin.")
    if not actor["admin"]:
        if email == actor["email"]:
            raise HTTPException(403, "You can't change your own roles. Ask an admin.")
        if target_admin or "manager" in (g.get("roles") or []):
            raise HTTPException(403, "Only an admin can change the roles of a manager or an admin.")
    if role == "admin" and not on and target_admin:
        if email in ADMIN_EMAILS:
            raise HTTPException(400, f"{email} is an admin through the server's ADMIN_EMAILS setting, so it can't be removed here.")
        if len(_admins()) <= 1:
            raise HTTPException(400, "Tasklore needs at least one admin. Make someone else an admin first.")


def change_role(actor: dict, email: str, role: str, on: bool) -> dict:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "Enter a work email, like john@company.com")
    if role not in GRANTABLE:
        raise HTTPException(400, f"Unknown role {role!r}.")
    _check(actor, email, role, on)
    _set_grant(email, role, on, actor["email"])
    return next(p for p in team() if p["email"] == email)


def remove_from_team(actor: dict, email: str) -> None:
    """Take away every role this email was granted (their plans and playbooks stay)."""
    email = email.strip().lower()
    g = grants(email)
    for role in (["admin"] if email in ADMIN_EMAILS or g.get("admin") else []) + list(g.get("roles") or []):
        _check(actor, email, role, False)
    store.put("invites", {"roles": [], "admin": False, "granted": {}, "removed_by": actor["email"], "updated_at": time.time()},
              doc_id=email)
