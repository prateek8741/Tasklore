"""Runs the gap-closing agent once, for one plan, and reports what really happened.

The result is rebuilt from the database after the run: which requests exist, which gaps are still open, coverage.
The model's own words are used only for `summary`, after masking personal data and swapping its GAP-n / EXPERT-n
labels for real names (a label it made up shows as "unknown"). Without usable words, the API writes the summary.
"""
import asyncio
import logging
import re
import time
import uuid

from google.adk.agents import RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

import services
from agent.agent import build_agent, default_model
from agent.tools import LIMITS, RUNS, Run
from schemas import AgentRunResult, GapOutcome, TraceStep
from store import store

log = logging.getLogger("agent")
APP = "firstrung"
_sessions = InMemorySessionService()   # one short session per run; nothing durable lives here
_runner: Runner | None = None


def _runner_for(llm: BaseLlm | None) -> Runner:
    """One shared runner for the app's model. Runs can't see each other: each has its own session and Run context."""
    global _runner
    if llm is not None:
        return Runner(app_name=APP, agent=build_agent(llm), session_service=_sessions)
    if _runner is None:
        _runner = Runner(app_name=APP, agent=build_agent(default_model()), session_service=_sessions)
    return _runner


async def run_close_gaps(plan_id: str, member: dict, llm: BaseLlm | None = None, run_id: str = "") -> AgentRunResult:
    if member.get("role") != "manager":
        raise services.ServiceError(403, "Only managers can run the gap-closing agent.")
    plan = await asyncio.to_thread(services.get_plan, plan_id)
    run = Run(id=run_id or uuid.uuid4().hex[:12], plan_id=plan_id, member=member,
              coverage_start=plan.get("coverage_percent", 0))
    t0, text, failed = time.time(), "", None
    RUNS[run.id] = run
    session = await _sessions.create_session(app_name=APP, user_id=member["uid"], state={"run_id": run.id})
    log.info("agent run %s started on plan %s", run.id, plan_id)
    try:
        async with asyncio.timeout(LIMITS.timeout_s):
            async for event in _runner_for(llm).run_async(
                    user_id=member["uid"], session_id=session.id, run_config=RunConfig(max_llm_calls=LIMITS.max_llm_calls),
                    new_message=types.Content(role="user", parts=[types.Part(text="Close this plan's gaps.")])):
                if event.error_code:   # ADK 2.x reports a failed turn as an event (and may also raise it)
                    if event.error_code == LlmCallsLimitExceededError.__name__:
                        run.stopped = run.stopped or f"model call limit ({LIMITS.max_llm_calls}) reached"
                    else:
                        failed = _why_failed_event(event.error_code, event.error_message or "")
                elif event.is_final_response():
                    text = "".join(p.text for p in (event.content.parts if event.content else []) or []
                                   if p.text and not p.thought)
    except TimeoutError:
        run.stopped = f"time limit ({LIMITS.timeout_s:.0f} s) reached"
    except Exception as e:   # noqa: BLE001 - reported as a stopped or failed run, with details only in our logs
        if _cause(e, LlmCallsLimitExceededError):
            run.stopped = run.stopped or f"model call limit ({LIMITS.max_llm_calls}) reached"
        else:
            log.warning("agent run %s failed: %s: %s", run.id, type(e).__name__, str(e)[:200])
            failed = _why_failed(e)
    finally:
        RUNS.pop(run.id, None)
        await _sessions.delete_session(app_name=APP, user_id=member["uid"], session_id=session.id)
    result = await asyncio.to_thread(_result, run, len(plan.get("gaps", [])), text, failed, t0)
    log.info("agent run %s %s: %d created, %d model calls, %d tool calls, %d ms", run.id, result.status,
             len(result.requests_created), result.model_calls, result.tool_calls, result.ms)
    return result


def _cause(e: BaseException | None, kind: type) -> BaseException | None:
    """`e` or the error it wraps, if it is a `kind`. ADK 2.x wraps failures inside a node in DynamicNodeFailError."""
    for _ in range(10):
        if e is None or isinstance(e, kind):
            return e
        e = getattr(e, "error", None) or e.__cause__ or e.__context__
    return None


def _why_failed(e: Exception) -> str:
    if api := _cause(e, genai_errors.APIError):
        return _gemini_problem(api.code)
    return "The agent hit an unexpected error (logged)."


def _why_failed_event(code: str, message: str) -> str:
    """The same, from an error event: only the HTTP status is kept, never the raw message."""
    status = next((int(s) for s in ("402", "429", "500", "503") if message.startswith(s)), None)
    return _gemini_problem(status) if status else f"Gemini gave no usable answer ({code})."


def _gemini_problem(status: int | None) -> str:
    if status == 402:
        return "Gemini credits are used up, so the agent couldn't run."
    if status == 429:
        return "Gemini is busy right now. Try again in a minute."
    return f"Gemini failed (HTTP {status})."


# ---------------- the result: facts from the database ----------------
def display(run: Run, text: str) -> str:
    """Model or trace text for the manager: personal data masked, labels replaced by real names."""
    text = run.redactor.text(text)
    text = re.sub(r"\bEXPERT-\d+\b", lambda m: run.experts.get(m.group(0), "[unknown expert]"), text)
    return re.sub(r"\bGAP-\d+\b", lambda m: f'"{run.redactor.text(run.gaps[m.group(0)])}"' if m.group(0) in run.gaps
                  else "[unknown task]", text)


def _result(run: Run, gaps_at_start: int, text: str, failed: str | None, t0: float) -> AgentRunResult:
    try:
        _, gaps = services.gaps_with_requests(run.plan_id)
    except services.ServiceError:   # the plan was deleted during the run
        gaps = []
    created = []
    for c in run.created:
        if r := store.get("requests", c["id"]):   # trust the database, not our own notes
            created.append(GapOutcome(topic=r["topic"], importance=r["importance"], state="requested_now",
                                      expert_email=r["expert_email"], request_id=c["id"]))
    created_ids = {c.request_id for c in created}
    already, unresolved, hints = [], [], {}
    for g in gaps:
        pending = next((r for r in g["requests"] if r["status"] == "open"), None)
        if pending and pending["id"] in created_ids:
            continue
        if pending:
            already.append(GapOutcome(topic=g["topic"], importance=g["importance"], state="requested",
                                      expert_email=pending["expert_email"], request_id=pending["id"]))
        else:
            unresolved.append(GapOutcome(topic=g["topic"], importance=g["importance"], state=g["state"]))
            hints[g["topic"]] = g["suggested_recorder"]

    if failed and not (created or run.recheck):
        status, stopped = "failed", failed
    elif failed or run.stopped:
        status, stopped = "stopped", failed or run.stopped
    elif not gaps_at_start:
        status, stopped = "no_gaps", None
    else:
        status, stopped = "completed", None
    summary = display(run, text).strip()[:1200]
    coverage_after = run.recheck["coverage_after"] if run.recheck else None
    return AgentRunResult(
        run_id=run.id, plan_id=run.plan_id, status=status, stopped_reason=stopped,
        summary=summary or _system_summary(created, already, unresolved, run.coverage_start, coverage_after, stopped),
        summary_by="agent" if summary else "system", gaps_found=gaps_at_start,
        requests_created=created, already_requested=already, unresolved=unresolved,
        coverage_before=run.coverage_start, coverage_after=coverage_after,
        next_steps=_next_steps(created, already, unresolved, hints),
        trace=[TraceStep(**{**t, "note": display(run, t["note"])}) for t in run.trace],
        model_calls=run.model_calls, tool_calls=run.tool_calls, ms=int((time.time() - t0) * 1000))


def _system_summary(created, already, unresolved, before, after, stopped) -> str:
    parts = [f'Asked {c.expert_email} to record "{c.topic}".' for c in created]
    if already:
        parts.append(f"{len(already)} already requested.")
    if unresolved:
        parts.append(f"{len(unresolved)} still open with nobody asked.")
    if after is not None:
        parts.append(f"Coverage {before}% to {after}%.")
    if stopped:
        parts.append(f"Stopped: {stopped}")
    return " ".join(parts) or "Nothing to do: this plan has no gaps."


def _next_steps(created, already, unresolved, hints) -> list[str]:
    steps = [f'Wait for {c.expert_email} to record "{c.topic}". When they do, the request closes and coverage is '
             "re-checked automatically." for c in created]
    steps += [f'"{a.topic}" is already with {a.expert_email}. Nudge them if it is needed soon.' for a in already]
    for u in unresolved:
        if u.state == "declined":
            steps.append(f'"{u.topic}" was declined. Pick someone else from the plan page.')
        elif u.state == "recorded_not_covering":
            steps.append(f'A recording for "{u.topic}" came in but didn\'t cover the task. Review it, or link the right '
                         "playbook with Add a video.")
        else:
            steps.append(f'Nobody on the team has recorded work close to "{u.topic}". Ask someone who knows it '
                         f"(usually: {hints.get(u.topic) or 'a senior colleague'}).")
    return steps
