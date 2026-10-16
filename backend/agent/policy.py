"""ADK callbacks: the limits and checks around every model and tool call, enforced in Python whatever the model says.

before_model  counts model calls (RunConfig.max_llm_calls is the hard stop)
before_tool   only our four tools; caps tool calls and requests per run; rejects oversized arguments
after_tool    masks secrets and personal data in everything a tool hands back to the model
on_tool_error a tool that crashes becomes a plain "failed" answer instead of ending the run; details stay in our logs
A dict returned from before_tool is given to the model instead of running the tool.
"""
import logging
import time

from agent.tools import LIMITS, TOOL_NAMES, WRITE_TOOLS, current

log = logging.getLogger("agent")


def before_model(callback_context, llm_request):
    current(callback_context).model_calls += 1
    return None


def before_tool(tool, args, tool_context):
    run, t0 = current(tool_context), time.time()
    run.tool_calls += 1
    reason = None
    if tool.name not in TOOL_NAMES:
        reason = f"{tool.name} is not available."
    elif run.tool_calls > LIMITS.max_tool_calls:
        reason = "The tool limit for this run is reached. Stop and write the summary."
        run.stopped = run.stopped or "tool call limit reached"
    elif any(len(str(v)) > LIMITS.max_arg_chars for v in _flat(args)):
        reason = "An argument is too long. Use the GAP-n / EXPERT-n labels only."
    elif tool.name in WRITE_TOOLS:
        if run.request_slots >= LIMITS.max_requests:
            reason = f"This run may create at most {LIMITS.max_requests} requests. Stop and write the summary."
        else:
            run.request_slots += 1   # reserved now; the tool gives it back if nothing gets created
    if reason:
        log.info("agent run %s: refused %s (%s)", run.id, tool.name, reason)
        run.note(tool.name, False, reason, t0)
        return {"ok": False, "error": reason}
    return None


def after_tool(tool, args, tool_context, tool_response):
    return current(tool_context).redactor.scrub(tool_response)


def on_tool_error(tool, args, tool_context, error):
    run = current(tool_context)
    log.warning("agent run %s: tool %s failed: %s", run.id, tool.name, type(error).__name__)
    run.note(tool.name, False, "Failed for a technical reason (logged).", time.time())
    return {"ok": False, "error": f"{tool.name} failed for a technical reason. Don't retry it; say so in the summary."}


def _flat(args: dict):
    for v in args.values():
        yield from (v if isinstance(v, list) else [v])
