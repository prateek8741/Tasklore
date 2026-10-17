"""Data shapes shared by the API and by Gemini's structured output."""
from typing import Literal

from pydantic import BaseModel, Field


# ---------- Capture: a recording becomes a playbook ----------
class Step(BaseModel):
    title: str = Field(description="Short imperative title, e.g. 'Open the vendor ledger'")
    action: str = Field(description="Exactly what to do on screen: which app, which menu, which column or formula")
    why: str = Field(description="Reason behind the step, taken from the narration")
    timestamp: str = Field(description="mm:ss in the video where this step starts")


class Playbook(BaseModel):
    title: str = Field(description="Task name a new hire would search for, e.g. 'Reconcile vendor invoices in Excel'")
    goal: str = Field(description="What this task achieves for the business, one sentence")
    role_hint: str = Field(description="Which job role usually does this task")
    tools: list[str] = Field(description="Software or systems used")
    skills: list[str] = Field(description="3-6 short knowledge areas this task teaches, e.g. 'GST input tax credit'")
    estimated_minutes: int = Field(description="How long a beginner needs to do this task once")
    steps: list[Step]
    gotchas: list[str] = Field(description="Mistakes a beginner is likely to make on this task")
    success_criteria: list[str] = Field(description="How to tell the task was done right")
    narration_language: str = Field(description="Language the narrator spoke, e.g. 'English', 'Hindi', 'Hinglish', or 'none'")


# ---------- Self-improving playbooks: new hires' questions become playbook changes ----------
class Suggestion(BaseModel):
    kind: Literal["gotcha", "step", "faq"] = Field(
        description="gotcha = a new mistake to warn about; step = rewrite one step's action; faq = a question new hires ask, with its answer")
    step_index: int = Field(description="For 'step': 0-based index of the step to rewrite. -1 otherwise")
    text: str = Field(description="gotcha: the warning. step: the full new action text. faq: the question, in plain words")
    answer: str = Field(description="faq: the answer in at most 2 sentences. Empty string otherwise")
    reason: str = Field(description="One sentence: what the new hires struggled with that this fixes")
    evidence: list[int] = Field(description="Indices of the questions this suggestion is based on")


class Improvements(BaseModel):
    summary: str = Field(description="One sentence: what new hires struggle with on this playbook")
    suggestions: list[Suggestion] = Field(description="At most 5 changes, most useful first")


class ApplyImprovements(BaseModel):
    suggestions: list[Suggestion] = Field(default=[], max_length=10)   # empty = dismiss: mark the questions as reviewed
    based_on: int = Field(default=0, ge=0, le=500)                       # how many new-hire questions they came from


class YouTubeResolveRequest(BaseModel):
    url: str = Field(min_length=5, max_length=500)
    whole_playlist: bool = False  # for a link to a video inside a playlist: list the playlist instead


class YouTubeCaptureRequest(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    hint: str = Field(default="", max_length=300)
    request_id: str = Field(default="", max_length=64)  # answering a recording request
    plan_id: str = Field(default="", max_length=64)     # with gap: this video fills that gap on that plan
    gap: str = Field(default="", max_length=500)


# ---------- Plan: a role description becomes an onboarding plan + gap report ----------
class RoleTask(BaseModel):
    task: str = Field(description="One core task or knowledge area this role needs, specific enough to record as a 2-3 minute video")
    importance: int = Field(description="3 = done weekly and costly if wrong, 2 = regular, 1 = occasional")
    playbook_id: str = Field(description="ID of the library playbook that teaches this task, or empty string if none does")
    why_needed: str = Field(description="One sentence: why this role needs it, citing the role description")
    suggested_recorder: str = Field(description="Who on the team should record it, by role, e.g. 'Finance lead'")
    needed_by_day: int = Field(description="Working day the new hire first needs this")


class PlanItem(BaseModel):
    day: int = Field(description="Working day number, starting at 1")
    title: str
    kind: Literal["learn", "practice", "meet", "setup"]
    playbook_id: str = Field(description="ID of the playbook used, or empty string if none")
    detail: str = Field(description="One or two sentences on what the new hire does")
    stuck_example: str = Field(description="One realistic problem a new hire could hit on THIS step, in their own words, "
                                           "max 14 words, e.g. 'GitHub says Permission denied (publickey) when I clone'")


class PlanDraft(BaseModel):
    """What Gemini returns. Coverage and gaps are computed from role_tasks by the API."""
    role: str = Field(description="Short role title")
    summary: str = Field(description="Two sentences: the shape of the first days and why")
    role_tasks: list[RoleTask] = Field(description="5-10 core tasks of the role, most important first")
    items: list[PlanItem]


class TaskMatch(BaseModel):
    task_index: int = Field(description="Index of the task in the list given")
    playbook_id: str = Field(description="ID of the library playbook that teaches this task, or empty string if none does")


class Recheck(BaseModel):
    """Gemini's answer when an existing plan is re-checked against the current library."""
    matches: list[TaskMatch]


class Gap(BaseModel):
    topic: str
    why_needed: str
    suggested_recorder: str
    importance: int
    needed_by_day: int


class OnboardingPlan(BaseModel):
    role: str
    summary: str
    coverage_percent: int
    role_tasks: list[RoleTask]
    items: list[PlanItem]
    gaps: list[Gap]


class PlanRequest(BaseModel):
    role_description: str = Field(min_length=10, max_length=4000)
    days: int = Field(default=5, ge=1, le=10)


# ---------- Session: the plan adapts as the new hire works ----------
class SessionRequest(BaseModel):
    plan_id: str
    item_index: int
    outcome: Literal["done", "stuck", "already_know"]
    note: str = ""


class Transcript(BaseModel):
    text: str = Field(description="Exactly what was said, in the original language. Empty string if there is no clear speech")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class CoachRequest(BaseModel):
    plan_id: str
    item_index: int


# ---------- Proof of practice: a screenshot of the result, checked against "done right when" ----------
class CriterionCheck(BaseModel):
    criterion: str = Field(description="The 'done right when' criterion, as given")
    status: Literal["met", "missing", "cant_tell"] = Field(
        description="met = clearly visible; missing = visibly not done or wrong; cant_tell = this screenshot can't show it")
    evidence: str = Field(description="Exactly what you see that decides it (cell, column, value, label). Empty for cant_tell")


class ProofCheck(BaseModel):
    verdict: Literal["pass", "almost", "not_yet", "unclear"] = Field(
        description="pass = everything visible is right; almost = 1-2 small things missing; not_yet = key parts missing "
                    "or wrong; unclear = the screenshot doesn't show this task")
    criteria: list[CriterionCheck]
    gotchas_seen: list[str] = Field(description="Known gotchas you can actually see in the screenshot; empty if none")
    feedback: str = Field(description="1-3 warm, specific sentences to the new hire: what is good, then the one most important fix")


class SessionResponse(BaseModel):
    branch: str  # next | skip | hint | verified | proof
    next_item_index: int | None  # None = plan complete
    message: str
    heard: str | None = None  # for spoken questions: what Gemini heard
    progress: dict  # status per item + log; same shape as plan["progress"]
    summary: dict  # counts and percent complete
    proof: dict | None = None  # for screenshot checks: Gemini's ProofCheck


# ---------- Roles, assignment, recording requests ----------
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RoleSwitch(BaseModel):
    role: Literal["manager", "expert", "newhire"]


class TeamRoleChange(BaseModel):
    email: str = Field(max_length=254)
    role: Literal["admin", "manager", "expert", "newhire"]
    on: bool


class AssignRequest(BaseModel):
    email: str = Field(default="", max_length=200, pattern=r"^$|" + EMAIL)  # empty = unassign


class LinkGapRequest(BaseModel):
    gap: str = Field(max_length=500)
    playbook_id: str = Field(max_length=64)


class AskExpertRequest(BaseModel):
    plan_id: str
    topic: str = Field(max_length=500)
    expert_email: str = Field(max_length=200, pattern=EMAIL)


class DeclineRequest(BaseModel):
    reason: str = Field(default="", max_length=300)
    suggest: str = Field(default="", max_length=200)  # who to ask instead


# ---------- Gap-closing agent: every ID, email and number here comes from the database, not from the model ----------
class AgentRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)


class GapOutcome(BaseModel):
    topic: str
    importance: int
    state: Literal["requested_now", "requested", "recorded_not_covering", "declined", "not_requested"]
    expert_email: str | None = None   # who is asked now (requested_now / requested)
    request_id: str | None = None


class TraceStep(BaseModel):
    tool: str
    ok: bool
    note: str                          # what happened, for the manager (already redacted)
    ms: int = 0


class AgentRunResult(BaseModel):
    run_id: str
    plan_id: str
    status: Literal["completed", "no_gaps", "stopped", "failed"]
    stopped_reason: str | None = None
    summary: str
    summary_by: Literal["agent", "system"]   # agent = Gemini's words (checked); system = written by the API
    gaps_found: int
    requests_created: list[GapOutcome]
    already_requested: list[GapOutcome]
    unresolved: list[GapOutcome]       # still open and nobody asked: declined, not covered, or no fitting expert
    coverage_before: int
    coverage_after: int | None         # None = coverage was not re-checked in this run
    next_steps: list[str]
    trace: list[TraceStep]
    model_calls: int
    tool_calls: int
    ms: int
