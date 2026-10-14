"""What the team's own data says, computed without Gemini.

- struggles / signals: every time a new hire got stuck (or opened the live coach) on a plan step that uses a
  playbook. This is what makes playbooks self-improving: the questions go back to the playbook's recorder.
- knowledge_map: for each role, which core tasks are recorded, requested, or still only in someone's head
  (the "bus factor": if that person is away or leaves, nobody can teach it).
"""


def struggles(plans: dict) -> list[dict]:
    """Stuck, failed screenshot-check and live-coach events on plan steps that use a playbook, oldest first.
    Includes runs from before a plan was restarted (kept in plan["past_log"])."""
    out = []
    for plan_id, plan in plans.items():
        items = plan.get("items", [])
        log = plan.get("past_log", []) + (plan.get("progress") or {}).get("log", [])
        for k, e in enumerate(log):
            i = e.get("item_index", -1)
            if e.get("outcome") not in ("stuck", "coach", "proof") or not 0 <= i < len(items) or not items[i].get("playbook_id"):
                continue
            # a failed screenshot check counts as a question: what was missing is exactly what the playbook should say
            note = f"(screenshot check) {e.get('note', '')}" if e["outcome"] == "proof" else e.get("note", "")
            out.append({"playbook_id": items[i]["playbook_id"], "kind": "coach" if e["outcome"] == "coach" else "stuck", "note": note,
                        "hint": e.get("hint", ""), "step": items[i]["title"], "by": e.get("by", ""), "at": e.get("at", 0),
                        "spoken": bool(e.get("spoken")), "plan_id": plan_id,
                        # they pressed "Done" on this step later, so the help they got worked
                        "resolved": any(x.get("item_index") == i and x.get("outcome") == "done" for x in log[k + 1:])})
    out.sort(key=lambda q: q["at"])
    return out


def new_struggles(playbook_id: str, playbook: dict, plans: dict) -> list[dict]:
    """Struggles on this playbook since its recorder last reviewed them."""
    since = playbook.get("reviewed_at", 0)
    return [q for q in struggles(plans) if q["playbook_id"] == playbook_id and q["at"] > since]


def signals(playbooks: dict, plans: dict) -> dict[str, dict]:
    """playbook id -> counts of new-hire struggles since the last review (playbooks without any are left out)."""
    out: dict[str, dict] = {}
    for q in struggles(plans):
        pb = playbooks.get(q["playbook_id"])
        if not pb or q["at"] <= pb.get("reviewed_at", 0):
            continue
        s = out.setdefault(q["playbook_id"], {"questions": 0, "coach": 0, "people": set(), "last_note": "", "last_at": 0})
        s["questions" if q["kind"] == "stuck" else "coach"] += 1
        s["people"].add(q["by"] or q["plan_id"])
        if q["kind"] == "stuck" and q["note"]:
            s["last_note"], s["last_at"] = q["note"][:160], q["at"]
    for s in out.values():
        s["people"] = len(s["people"])
    return out


def _latest_plan_per_role(plans: dict) -> dict[str, tuple[str, dict, int]]:
    """role (lower case) -> (plan id, newest plan for that role, how many plans exist for it)."""
    out: dict[str, tuple[str, dict, int]] = {}
    for pid, p in plans.items():
        key = p.get("role", "").strip().lower()
        if not key or not p.get("role_tasks"):
            continue
        old = out.get(key)
        n = (old[2] if old else 0) + 1
        if not old or p.get("created_at", 0) > old[1].get("created_at", 0):
            out[key] = (pid, p, n)
        else:
            out[key] = (old[0], old[1], n)
    return out


def knowledge_map(plans: dict, playbooks: dict, requests: dict) -> dict:
    """The team's know-how, role by role: recorded, requested, or only in someone's head."""
    latest = _latest_plan_per_role(plans)
    reqs = {}   # (role, task) -> newest recording request for it
    for r in sorted(requests.values(), key=lambda r: r.get("created_at", 0)):
        reqs[(r.get("plan_role", "").strip().lower(), r["topic"].strip().lower())] = r

    roles, unique = [], {}   # unique: task text -> (importance, recorded?) across roles, for the team score
    for key, (pid, p, n_plans) in sorted(latest.items()):
        tasks = []
        for t in p["role_tasks"]:
            pb = playbooks.get(t.get("playbook_id") or "")
            r = reqs.get((key, t["task"].strip().lower()))
            task = {"task": t["task"], "importance": t["importance"], "needed_by_day": t.get("needed_by_day"),
                    "suggested_recorder": t.get("suggested_recorder", ""), "plan_id": pid}
            if pb:
                task.update(status="recorded", playbook_id=t["playbook_id"], playbook_title=pb["title"],
                            recorded_by=(pb.get("created_by") or {}).get("name") or (pb.get("created_by") or {}).get("email", ""))
            else:
                status = "requested" if r and r["status"] == "open" else "declined" if r and r["status"] == "declined" else "missing"
                person = r["expert_email"] if status == "requested" else (r.get("suggested") if status == "declined" else "")
                task.update(status=status, holder=person or t.get("suggested_recorder") or "Someone on the team",
                            holder_is_person=bool(person),
                            request={k: r.get(k) for k in ("expert_email", "status", "decline_reason", "suggested")} if r else None)
            tasks.append(task)
            k = t["task"].strip().lower()
            imp, rec = unique.get(k, (0, False))
            unique[k] = (max(imp, t["importance"]), rec or bool(pb))
        total = sum(t["importance"] for t in tasks) or 1
        roles.append({"role": p["role"], "plan_id": pid, "plans": n_plans, "assigned_to": p.get("assigned_to"),
                      "coverage_percent": round(100 * sum(t["importance"] for t in tasks if t["status"] == "recorded") / total),
                      "tasks": tasks})

    # Bus factor: unrecorded know-how grouped by who holds it today.
    holders: dict[str, dict] = {}
    for role in roles:
        for t in role["tasks"]:
            if t["status"] == "recorded":
                continue
            h = holders.setdefault(t["holder"].strip().lower(), {"holder": t["holder"], "is_person": t["holder_is_person"],
                                                                 "tasks": [], "critical": 0, "points": 0})
            h["tasks"].append({**t, "role": role["role"]})
            h["critical"] += t["importance"] == 3
            h["points"] += t["importance"]
    for h in holders.values():
        h["tasks"].sort(key=lambda t: (-t["importance"], t.get("needed_by_day") or 99))
    holder_list = sorted(holders.values(), key=lambda h: (-h["critical"], -h["points"]))

    # Who shares know-how: playbooks per person, and how many plans and new hires use them.
    used_in: dict[str, set] = {}
    learners: dict[str, set] = {}
    for pid, p in plans.items():
        for pb_id in {i.get("playbook_id") for i in p.get("items", [])} | {t.get("playbook_id") for t in p.get("role_tasks", [])}:
            if pb_id:
                used_in.setdefault(pb_id, set()).add(pid)
                if p.get("assigned_to"):
                    learners.setdefault(pb_id, set()).add(p["assigned_to"])
    people: dict[str, dict] = {}
    for pb_id, pb in playbooks.items():
        who = pb.get("created_by") or {}
        key = who.get("uid") or who.get("email") or "unknown"
        c = people.setdefault(key, {"name": who.get("name") or who.get("email") or "A teammate", "email": who.get("email", ""),
                                    "playbooks": 0, "improved": 0, "plans": set(), "new_hires": set()})
        c["playbooks"] += 1
        c["improved"] += pb.get("version", 1) > 1
        c["plans"] |= used_in.get(pb_id, set())
        c["new_hires"] |= learners.get(pb_id, set())
    contributors = sorted(({**c, "plans": len(c["plans"]), "new_hires": len(c["new_hires"])} for c in people.values()),
                          key=lambda c: (-c["new_hires"], -c["plans"], -c["playbooks"]))

    sig = signals(playbooks, plans)
    struggling = sorted(({"playbook_id": pid, "title": playbooks[pid]["title"], **s} for pid, s in sig.items() if s["questions"]),
                        key=lambda s: -(s["questions"] + s["coach"]))

    team_total = sum(imp for imp, _ in unique.values()) or 1
    return {
        "team": {"coverage_percent": round(100 * sum(imp for imp, rec in unique.values() if rec) / team_total) if unique else 0,
                 "tasks": len(unique), "recorded": sum(rec for _, rec in unique.values()),
                 "critical_missing": sum(1 for imp, rec in unique.values() if imp == 3 and not rec),
                 "holders": len(holder_list), "playbooks": len(playbooks), "roles": len(roles)},
        "roles": roles, "holders": holder_list, "contributors": contributors, "struggling": struggling,
    }


def briefing_facts(m: dict) -> str:
    """The knowledge map as short text facts for Gemini's risk briefing."""
    t = m["team"]
    lines = [f"Team: {t['coverage_percent']}% of the core know-how (importance-weighted) is recorded; "
             f"{t['recorded']} of {t['tasks']} core tasks; {t['critical_missing']} critical tasks not recorded."]
    lines += [f"Role {r['role']}: {r['coverage_percent']}% recorded." for r in m["roles"]]
    lines.append("Unrecorded know-how, by who holds it today:")
    for h in m["holders"][:6]:
        tasks = "; ".join(f"{x['task']} ({['', 'occasional', 'regular', 'critical'][x['importance']]}, role {x['role']}, "
                          f"needed by day {x.get('needed_by_day')}, {x['status'].replace('missing', 'nobody asked yet')})"
                          for x in h["tasks"][:5])
        lines.append(f"- {h['holder']}{' (a person)' if h['is_person'] else ' (a role)'}: {len(h['tasks'])} tasks, "
                     f"{h['critical']} critical: {tasks}")
    for s in m["struggling"][:3]:
        lines.append(f"Recorded but new hires struggle: '{s['title']}' ({s['questions']} questions from {s['people']} people).")
    for c in m["contributors"][:3]:
        lines.append(f"Contributor: {c['name']}, {c['playbooks']} playbooks, used in {c['plans']} plans, by {c['new_hires']} assigned new hires.")
    return "\n".join(lines)
