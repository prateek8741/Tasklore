"""MOCK mode for the agent (no Gemini key): a scripted stand-in for the model that follows the same steps.

It goes through the real ADK runner, tools, checks and database, so local runs and tests exercise everything except
Gemini's judgement. It picks an expert only when their recorded work shares a word with the task.
"""
import re
from typing import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

NO_TOKENS = types.GenerateContentResponseUsageMetadata(prompt_token_count=0, candidates_token_count=0, total_token_count=0)
STOPWORDS = {"the", "and", "with", "for", "from", "your", "into", "that", "this", "how", "run", "use", "set", "open",
             "get", "make", "new", "one", "all", "its", "what"}


def _words(*texts) -> set[str]:
    return {w for t in texts for w in re.findall(r"[a-z0-9]+", str(t).lower()) if len(w) > 2 and w not in STOPWORDS}


def _call(name: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def _latest_responses(llm_request) -> dict[str, list[dict]]:
    seen: dict[str, list[dict]] = {}
    for content in llm_request.contents:
        for part in content.parts or []:
            if part.function_response:
                seen.setdefault(part.function_response.name, []).append(part.function_response.response or {})
    return seen


class MockAgentLlm(BaseLlm):
    model: str = "mock"

    async def generate_content_async(self, llm_request, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(content=types.Content(role="model", parts=self.next_parts(_latest_responses(llm_request))),
                          usage_metadata=NO_TOKENS)

    @staticmethod
    def next_parts(seen: dict[str, list[dict]]) -> list[types.Part]:
        if "get_knowledge_gaps" not in seen:
            return [_call("get_knowledge_gaps")]
        gaps = seen["get_knowledge_gaps"][-1]
        if not gaps.get("ok"):
            return [types.Part(text="I couldn't read this plan's gaps, so I did nothing.")]
        if not gaps["gaps"]:
            return [types.Part(text=f"This plan has no gaps: coverage is {gaps['coverage_percent']}%. Nothing to record.")]
        todo = [g for g in gaps["gaps"] if g["state"] in ("not_requested", "declined")]
        if todo and "get_task_experts" not in seen:
            return [_call("get_task_experts", gaps=[g["gap"] for g in todo])]
        if todo and "request_task_recording" not in seen and seen["get_task_experts"][-1].get("ok"):
            if calls := MockAgentLlm.pick(todo, seen["get_task_experts"][-1]):
                return calls
        if "recheck_role_coverage" not in seen:
            return [_call("recheck_role_coverage")]
        return [types.Part(text=MockAgentLlm.summary(gaps, seen))]

    @staticmethod
    def pick(todo: list[dict], found: dict) -> list[types.Part]:
        load = {e["expert"]: e["open_requests"] for e in found["experts"]}
        calls = []
        for g in todo:
            task = _words(g["task"])
            scored = [(len(task & _words(e["recorded"], e["skills"], e["steps_shown"])), -load[e["expert"]], e["expert"])
                      for e in found["experts"] if e["expert"] not in found.get("do_not_ask", {}).get(g["gap"], [])]
            best = max(scored, default=None)
            if best and best[0] > 0:
                calls.append(_call("request_task_recording", gap=g["gap"], expert=best[2]))
                load[best[2]] += 1
        return calls

    @staticmethod
    def summary(gaps: dict, seen: dict) -> str:
        asked = [r for r in seen.get("request_task_recording", []) if r.get("ok")]
        lines = [f"Asked {r['expert']} to record {r['gap']}." for r in asked]
        if pending := [g["gap"] for g in gaps["gaps"] if g["state"] == "requested"]:
            lines.append(f"Already requested before: {', '.join(pending)}.")
        done = {r["gap"] for r in asked}
        if review := [g["gap"] for g in gaps["gaps"] if g["state"] == "recorded_not_covering"]:
            lines.append(f"A recording came in for {', '.join(review)} but didn't cover it; please review.")
        if still := [g["gap"] for g in gaps["gaps"] if g["state"] in ("not_requested", "declined") and g["gap"] not in done]:
            lines.append(f"Nobody on the team has recorded anything close to {', '.join(still)}, so I asked nobody.")
        check = (seen.get("recheck_role_coverage") or [{}])[-1]
        if check.get("ok"):
            lines.append(f"Coverage went from {check['coverage_before']}% to {check['coverage_after']}%."
                         if check["coverage_after"] != check["coverage_before"]
                         else f"Coverage stays {check['coverage_after']}% until someone records.")
        return " ".join(lines)
