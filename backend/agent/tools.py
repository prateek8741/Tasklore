"""The gap-closing agent's four tools, and the per-run context they share.

The model never sees an email address or a database ID. Tools hand it labels (GAP-1, EXPERT-2) and keep the real
values in the run's context, so a label the model invents, or an address injected into some text, resolves to
nothing. Who is running the agent and on which plan comes from that context too: tools take no plan or user argument.

Every rule is enforced in services.py; the tools only translate between labels and real values.
"""
import asyncio
import os
import time
from dataclasses import dataclass, field

from google.adk.tools import ToolContext

import services
from redact import Redactor


@dataclass(frozen=True)
class Limits:
    max_llm_calls: int = int(os.getenv("AGENT_MAX_LLM_CALLS", "8"))
    max_tool_calls: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "10"))
    max_requests: int = int(os.getenv("AGENT_MAX_REQUESTS", "3"))    # recording requests one run may create
    timeout_s: float = float(os.getenv("AGENT_TIMEOUT_S", "90"))     # the web app calls Cloud Run directly (no 60 s Hosting cap)
    max_arg_chars: int = 40


LIMITS = Limits()


@dataclass
class Run:
    id: str
    plan_id: str
    member: dict
    coverage_start: int
    gaps: dict[str, str] = field(default_factory=dict)      # GAP-n -> topic
    experts: dict[str, str] = field(default_factory=dict)   # EXPERT-n -> email
    model_calls: int = 0
    tool_calls: int = 0
    request_slots: int = 0      # requests started; reserved before the tool runs, so parallel calls can't overshoot
    rechecks: int = 0
    created: list[dict] = field(default_factory=list)       # requests this run created (from services, not the model)
    recheck: dict | None = None
    trace: list[dict] = field(default_factory=list)
    stopped: str | None = None
    redactor: Redactor = field(default_factory=Redactor)

    def gap_label(self, topic: str) -> str:
        for label, t in self.gaps.items():
            if t == topic:
                return label
        label = f"GAP-{len(self.gaps) + 1}"
        self.gaps[label] = topic
        return label

    def expert_label(self, email: str) -> str:
        for label, e in self.experts.items():
            if e == email:
                return label
        label = f"EXPERT-{len(self.experts) + 1}"
        self.experts[label] = email
        return label

    def note(self, tool: str, ok: bool, note: str, t0: float) -> None:
        """One trace line, written with GAP-n / EXPERT-n labels; runner.display() turns them into names later."""
        self.trace.append({"tool": tool, "ok": ok, "note": note[:300], "ms": int((time.time() - t0) * 1000)})


RUNS: dict[str, Run] = {}   # live runs by id; the session state holds only the id, set by the server


def current(tool_context: ToolContext) -> Run:
    return RUNS[tool_context.state["run_id"]]


def _refuse(run: Run, tool: str, reason: str, t0: float) -> dict:
    run.note(tool, False, reason, t0)
    return {"ok": False, "error": reason}


# ---------------- the tools ----------------
async def get_knowledge_gaps(tool_context: ToolContext) -> dict:
    """Lists this plan's unrecorded core tasks (gaps), most important first, with the current coverage and whether
    someone has already been asked to record each one."""
    run, t0 = current(tool_context), time.time()
    plan, gaps = await asyncio.to_thread(services.gaps_with_requests, run.plan_id)
    out = []
    for g in gaps:
        item = {"gap": run.gap_label(g["topic"]), "task": g["topic"], "importance": g["importance"],
                "needed_by_day": g["needed_by_day"], "usually_known_by": g["suggested_recorder"], "state": g["state"]}
        if asked := next((r for r in g["requests"] if r["status"] == "open"), None):
            item["asked"] = run.expert_label(asked["expert_email"])
        out.append(item)
    run.note("get_knowledge_gaps", True, f"{len(gaps)} gaps, coverage {plan.get('coverage_percent', 0)}%", t0)
    return {"ok": True, "role": plan["role"], "coverage_percent": plan.get("coverage_percent", 0), "gaps": out}


async def get_task_experts(gaps: list[str], tool_context: ToolContext) -> dict:
    """Lists the people on the team who could record these gaps (GAP-n labels): the playbooks they recorded, their
    skills and the steps they have shown, how many open requests they already have, and who must not be asked for
    which gap."""
    run, t0 = current(tool_context), time.time()
    unknown = [g for g in gaps if g not in run.gaps]
    if unknown:
        return _refuse(run, "get_task_experts", f"Unknown gap labels {unknown}. Use GAP-n labels from get_knowledge_gaps.", t0)
    people = await asyncio.to_thread(services.expert_candidates, run.plan_id, run.member.get("email", ""))
    experts = [{"expert": run.expert_label(p["email"]), "recorded": p["recorded"][:5], "skills": p["skills"][:8],
                "steps_shown": p["steps"][:12], "open_requests": p["open_requests"]} for p in people]
    cannot = {g: [run.expert_label(p["email"]) for p in people if run.gaps[g] in p["declined"]] for g in gaps}
    run.note("get_task_experts", True, f"{len(experts)} possible experts", t0)
    return {"ok": True, "experts": experts, "do_not_ask": {g: who for g, who in cannot.items() if who}}


async def request_task_recording(gap: str, expert: str, tool_context: ToolContext) -> dict:
    """Asks one expert (EXPERT-n) to record one gap (GAP-n). Creates a recording request they see in Tasklore.
    Returns ok: true only if the request was really created."""
    run, t0, created = current(tool_context), time.time(), False
    try:
        topic, email = run.gaps.get(gap), run.experts.get(expert)
        if not topic or not email:
            return _refuse(run, "request_task_recording", f"Unknown label {gap if not topic else expert}. "
                           "Use labels from get_knowledge_gaps and get_task_experts.", t0)
        if run.member.get("role") != "manager":
            return _refuse(run, "request_task_recording", "Only managers can ask experts to record.", t0)
        if reason := await asyncio.to_thread(services.expert_refusal, run.plan_id, topic, email, run.member.get("email", "")):
            return _refuse(run, "request_task_recording", f"{expert} can't be asked for {gap}: {reason}.", t0)
        try:
            req = await asyncio.to_thread(services.create_request, run.plan_id, topic, email, run.member)
        except services.ServiceError as e:
            labelled = e.detail
            for label, e_mail in run.experts.items():
                labelled = labelled.replace(e_mail, label)
            return _refuse(run, "request_task_recording", f"{gap}: {labelled}", t0)
        run.created.append({"id": req["id"], "topic": topic, "expert_email": email})
        created = True
        run.note("request_task_recording", True, f"Asked {expert} to record {gap}", t0)
        return {"ok": True, "gap": gap, "expert": expert, "status": "open"}
    finally:
        if not created:
            run.request_slots -= 1   # nothing was created: give back the slot policy.before_tool reserved


async def recheck_role_coverage(tool_context: ToolContext) -> dict:
    """Re-matches the plan's tasks against the current playbook library and recomputes coverage. Call once, at the end.
    A new recording request does not change coverage; only a recorded playbook does."""
    run, t0 = current(tool_context), time.time()
    if run.rechecks:
        return _refuse(run, "recheck_role_coverage", "Coverage was already re-checked in this run.", t0)
    run.rechecks += 1
    r = await asyncio.to_thread(services.recheck, run.plan_id)
    run.recheck = {"coverage_before": r["coverage_before"], "coverage_after": r["coverage_after"],
                   "newly_covered": r["newly_covered"]}
    run.note("recheck_role_coverage", True, f"Coverage {r['coverage_before']}% to {r['coverage_after']}%", t0)
    return {"ok": True, "coverage_before": r["coverage_before"], "coverage_after": r["coverage_after"],
            "newly_covered": [run.gap_label(t) for t in r["newly_covered"]], "gaps_left": r["gaps"]}


TOOLS = [get_knowledge_gaps, get_task_experts, request_task_recording, recheck_role_coverage]
TOOL_NAMES = {t.__name__ for t in TOOLS}
WRITE_TOOLS = {"request_task_recording"}
