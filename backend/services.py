"""Gaps, recording requests and coverage: the rules, in one place.

The API routes and the gap-closing agent both call these functions, so the agent can never do anything a manager
couldn't do through the API, and every check lives here in Python rather than in a prompt.
Coverage is always computed by finish_plan from real library playbooks, never taken from a model.
Refusals raise ServiceError; the API turns it into the same {"detail": ...} reply an HTTPException gives.
"""
import hashlib
import time

import access
import gemini
import prompts
from schemas import Recheck
from store import store


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def who(user: dict) -> dict:
    return {"uid": user["uid"], "email": user.get("email", ""), "name": user.get("name", "")}


# ---------------- plans, gaps and coverage ----------------
def library_text(library: dict) -> str:
    if not library:
        return "(empty: no playbooks recorded yet)"
    return "\n".join(
        f"- id={pid} | {p['title']} | goal: {p['goal']} | role: {p.get('role_hint', '')} | "
        f"tools: {', '.join(p.get('tools', []))} | skills: {', '.join(p.get('skills', []))} | "
        f"steps: {'; '.join(s['title'] for s in p.get('steps', []))}"
        for pid, p in library.items())


def finish_plan(draft: dict, library: dict) -> dict:
    """Turn Gemini's draft into the final plan. Coverage and gaps are computed here, not guessed by the model:
    coverage = importance-weighted share of the role's core tasks that a real library playbook teaches."""
    for item in draft["items"]:
        if item["playbook_id"] not in library:  # drop IDs the model invented
            item["playbook_id"] = ""
    for t in draft["role_tasks"]:
        if t["playbook_id"] not in library:
            t["playbook_id"] = ""
        t["importance"] = min(max(int(t["importance"]), 1), 3)

    total = sum(t["importance"] for t in draft["role_tasks"]) or 1
    covered = sum(t["importance"] for t in draft["role_tasks"] if t["playbook_id"])
    gaps = sorted(({"topic": t["task"], "why_needed": t["why_needed"], "suggested_recorder": t["suggested_recorder"],
                    "importance": t["importance"], "needed_by_day": t["needed_by_day"]}
                   for t in draft["role_tasks"] if not t["playbook_id"]),
                  key=lambda g: (-g["importance"], g["needed_by_day"]))
    draft["items"].sort(key=lambda i: i["day"])
    return {**draft, "coverage_percent": round(100 * covered / total), "gaps": gaps}


def get_plan(plan_id: str) -> dict:
    plan = store.get("plans", plan_id)
    if not plan:
        raise ServiceError(404, "Plan not found")
    return plan


def recheck(plan_id: str) -> dict:
    """Re-match a plan's core tasks against the CURRENT library (e.g. after someone recorded a gap).
    Coverage and gaps are recomputed; the plan's items and the new hire's progress stay as they are."""
    plan = get_plan(plan_id)
    library = store.list("playbooks")
    tasks = plan["role_tasks"]
    before = {i: t["playbook_id"] for i, t in enumerate(tasks)}
    if not gemini.is_mock():
        _, resp = gemini.generate(prompts.RECHECK_USER.format(
            role=plan.get("role_description") or plan["role"],
            tasks="\n".join(f"{i}: {t['task']}" for i, t in enumerate(tasks)), library=library_text(library)),
            schema=Recheck, system=prompts.RECHECK_SYSTEM)
        for m in gemini.parsed(resp, Recheck)["matches"]:
            if 0 <= m["task_index"] < len(tasks):
                tasks[m["task_index"]]["playbook_id"] = m["playbook_id"]
    old_coverage = plan.get("coverage_percent", 0)
    plan.update(finish_plan(plan, library))
    plan["rechecked_at"] = time.time()
    store.put("plans", plan, doc_id=plan_id)
    newly = [t["task"] for i, t in enumerate(tasks) if t["playbook_id"] and not before.get(i)]
    return {"id": plan_id, "coverage_before": old_coverage, "coverage_after": plan["coverage_percent"],
            "newly_covered": newly, "gaps": len(plan["gaps"]), "plan": plan,
            "playbook_titles": {pid: p["title"] for pid, p in library.items()}}


def open_gap(plan_id: str, topic: str) -> dict:
    plan = get_plan(plan_id)
    if not any(t["task"] == topic and not t["playbook_id"] for t in plan["role_tasks"]):
        raise ServiceError(409, "That gap is already filled on this plan. Refresh the plan to see it.")
    return plan


def fill_gap(plan_id: str, topic: str, playbook_id: str, user: dict) -> dict:
    """The manager chose this playbook for this gap, so it is linked directly: no Gemini re-match that could
    miss it. Coverage and gaps are recomputed; an open recording request for the gap is closed by it."""
    plan, library = open_gap(plan_id, topic), store.list("playbooks")
    old_coverage = plan.get("coverage_percent", 0)
    for t in plan["role_tasks"]:
        if t["task"] == topic:
            t["playbook_id"] = playbook_id
    plan.update(finish_plan(plan, library))
    store.put("plans", plan, doc_id=plan_id)
    for rid, r in store.list("requests").items():
        if r["plan_id"] == plan_id and r["topic"] == topic and r["status"] == "open":
            store.put("requests", {**r, "status": "done", "playbook_id": playbook_id, "done_at": time.time(),
                                   "filled_by": who(user)}, doc_id=rid)
    return {"id": plan_id, "topic": topic, "coverage_before": old_coverage, "coverage_after": plan["coverage_percent"],
            "plan": plan, "playbook_titles": {pid: p["title"] for pid, p in library.items()}}


# ---------------- recording requests: a manager asks an expert to record a gap ----------------
def plan_requests(plan_id: str) -> list[dict]:
    return [{"id": rid, **r} for rid, r in store.list("requests").items() if r["plan_id"] == plan_id]


def request_id_for(plan_id: str, topic: str, attempt: int) -> str:
    """Same plan, gap and attempt -> same ID, so a retried or double-clicked request can't make a second one.
    attempt = how many requests this gap already had, so asking someone new after a decline gets a new ID."""
    return "req-" + hashlib.sha256(f"{plan_id}\n{topic}\n{attempt}".encode()).hexdigest()[:16]


def create_request(plan_id: str, topic: str, expert_email: str, member: dict) -> dict:
    plan = get_plan(plan_id)
    gap = next((g for g in plan.get("gaps", []) if g["topic"] == topic), None)
    if not gap:
        raise ServiceError(400, "That gap is no longer open on this plan. Re-check coverage and try again.")
    earlier = [r for r in plan_requests(plan_id) if r["topic"] == topic]
    if pending := next((r for r in earlier if r["status"] == "open"), None):
        raise ServiceError(409, f"Already requested from {pending['expert_email']}.")
    email = expert_email.strip().lower()
    doc = {"plan_id": plan_id, "plan_role": plan["role"], "assigned_to": plan.get("assigned_to"),
           "topic": gap["topic"], "why_needed": gap["why_needed"], "needed_by_day": gap["needed_by_day"],
           "importance": gap["importance"], "expert_email": email, "status": "open",
           "created_by": who(member), "created_at": time.time()}
    rid = request_id_for(plan_id, gap["topic"], len(earlier))
    if not store.create("requests", doc, rid):   # someone asked at the same moment
        raise ServiceError(409, "This gap was just requested. Refresh the plan to see who from.")
    access.invite(email, "expert")
    return {"id": rid, **doc}


def open_request_for(member: dict, request_id: str) -> dict:
    r = store.get("requests", request_id)
    if not r or (member["role"] != "manager" and r["expert_email"] != member["email"]):
        raise ServiceError(404, "Request not found")
    if r["status"] != "open":
        raise ServiceError(409, f"This request is already {r['status']}.")
    return r


def gaps_with_requests(plan_id: str) -> tuple[dict, list[dict]]:
    """The plan's open gaps (most important first), each with its recording requests and one state:
    requested (someone is asked now) | recorded_not_covering (a recording came in but re-check didn't link it)
    | declined (everyone asked said no) | not_requested."""
    plan = get_plan(plan_id)
    reqs = sorted(plan_requests(plan_id), key=lambda r: r.get("created_at", 0))
    out = []
    for g in plan.get("gaps", []):
        mine = [r for r in reqs if r["topic"] == g["topic"]]
        statuses = {r["status"] for r in mine}
        state = ("requested" if "open" in statuses else "recorded_not_covering" if "done" in statuses
                 else "declined" if "declined" in statuses else "not_requested")
        out.append({**g, "state": state, "requests": [{k: r.get(k) for k in ("id", "expert_email", "status", "created_at")}
                                                     for r in mine]})
    return plan, out


def expert_candidates(plan_id: str, asker: str = "") -> list[dict]:
    """Who could record this plan's gaps, from evidence in the team's own data: people who recorded a library
    playbook, people asked to record before, and people invited as experts. Holding the expert role alone isn't
    evidence (in demo mode every signed-in user holds every role). Never the plan's new hire or `asker` (the manager
    asking)."""
    plan = get_plan(plan_id)
    skip = {(plan.get("assigned_to") or "").lower(), asker.strip().lower(), ""}
    people: dict[str, dict] = {}

    def add(email: str | None) -> dict | None:
        email = (email or "").strip().lower()
        if email in skip:
            return None
        return people.setdefault(email, {"email": email, "recorded": [], "skills": set(), "steps": [], "open_requests": 0,
                                         "declined": []})

    for email, inv in store.list("invites").items():
        if "expert" in inv.get("roles", []):
            add(email)
    for pb in store.list("playbooks").values():
        if p := add((pb.get("created_by") or {}).get("email")):
            p["recorded"].append(pb["title"])
            p["skills"].update(pb.get("skills", []))
            p["steps"] += [s["title"] for s in pb.get("steps", [])]
    for r in store.list("requests").values():
        if p := add(r.get("expert_email")):
            p["open_requests"] += r["status"] == "open"
            if r["status"] == "declined" and r["plan_id"] == plan_id:
                p["declined"].append(r["topic"])
    return [{**p, "skills": sorted(p["skills"])} for _, p in sorted(people.items())]


def expert_refusal(plan_id: str, topic: str, email: str, asker: str = "") -> str | None:
    """Why this person shouldn't be asked to record this gap (None = they may be). Used by the agent, which may only
    ask people the team's data already knows; a manager picking someone by hand is not limited this way."""
    person = next((p for p in expert_candidates(plan_id, asker) if p["email"] == email), None)
    if not person:
        return "not a known expert on this team"
    if topic in person["declined"]:
        return "already declined this task"
    return None


def people_by_role() -> dict[str, list[str]]:
    """Known emails of experts and new hires: signed-in users plus invites."""
    out = {"expert": set(), "newhire": set()}
    for u in store.list("users").values():
        for role in out:
            if role in (u.get("roles") or []) and u.get("email"):
                out[role].add(u["email"])
    for email, inv in store.list("invites").items():
        for role in out:
            if role in inv.get("roles", []):
                out[role].add(email)
    return {role: sorted(emails) for role, emails in out.items()}
