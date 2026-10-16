"""agent_runs: one Firestore document per agent run, for the audit trail and the "last run" view.

Stored: who ran it, on which plan, when, the status and the sanitized result (summary, outcomes, coverage, trace
notes, counts). Never stored: prompts, raw model output, raw tool payloads. The Firestore rules already keep every
collection server-only, so nobody reads this except through the API.

Also the cost guards: at most AGENT_RUNS_PER_HOUR runs per person, and one run at a time per plan.
"""
import os
import time
import uuid

import services
from agent.tools import LIMITS
from schemas import AgentRunResult
from store import store

RUNS_PER_HOUR = int(os.getenv("AGENT_RUNS_PER_HOUR", "10"))


def start(plan_id: str, member: dict) -> str:
    """Checks the plan and the limits, records the run as running and returns its id."""
    services.get_plan(plan_id)   # 404 before anything is recorded
    now, runs = time.time(), store.list("agent_runs")
    mine = [r for r in runs.values() if (r.get("by") or {}).get("uid") == member["uid"] and r["started_at"] > now - 3600]
    if len(mine) >= RUNS_PER_HOUR:
        raise services.ServiceError(429, f"You can run the agent {RUNS_PER_HOUR} times an hour. Try again later.")
    if any(r["plan_id"] == plan_id and r["status"] == "running" and r["started_at"] > now - LIMITS.timeout_s - 10
           for r in runs.values()):
        raise services.ServiceError(409, "The agent is already working on this plan. Wait for it to finish.")
    run_id = uuid.uuid4().hex[:12]
    store.put("agent_runs", {"plan_id": plan_id, "by": services.who(member), "status": "running", "started_at": now},
              doc_id=run_id)
    return run_id


def finish(result: AgentRunResult) -> None:
    doc = store.get("agent_runs", result.run_id) or {}
    store.put("agent_runs", {**doc, **result.model_dump(), "finished_at": time.time()}, doc_id=result.run_id)


def abandon(run_id: str) -> None:
    """The run never got going (refused, or the server failed): keep the record, marked as such."""
    doc = store.get("agent_runs", run_id) or {}
    store.put("agent_runs", {**doc, "status": "failed", "stopped_reason": "The run did not start.",
                             "finished_at": time.time()}, doc_id=run_id)


def recent(plan_id: str, limit: int = 10) -> list[dict]:
    runs = [{"run_id": rid, **r} for rid, r in store.list("agent_runs").items() if r["plan_id"] == plan_id]
    return sorted(runs, key=lambda r: -r["started_at"])[:limit]
