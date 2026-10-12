"""Data shapes shared by the API and by Gemini's structured output."""
from pydantic import BaseModel, Field


# ---------- Capture: a recording becomes a playbook ----------
class Step(BaseModel):
    title: str = Field(description="Short imperative title, e.g. 'Open the vendor ledger'")
    action: str = Field(description="Exactly what to do on screen")
    why: str = Field(description="Reason behind the step, taken from the narration")
    timestamp: str = Field(description="mm:ss in the video where this step happens")


class Playbook(BaseModel):
    title: str
    goal: str = Field(description="What this task achieves for the business")
    role_hint: str = Field(description="Which job role usually does this task")
    tools: list[str] = Field(description="Software or systems used")
    steps: list[Step]
    gotchas: list[str] = Field(description="Mistakes a beginner is likely to make")
    success_criteria: list[str] = Field(description="How to tell the task was done right")


# ---------- Plan: a role description becomes an onboarding plan + gap report ----------
class PlanItem(BaseModel):
    day: int = Field(description="Working day number, starting at 1")
    title: str
    kind: str = Field(description="One of: learn, practice, meet, setup")
    playbook_id: str = Field(description="ID of the playbook used, or empty string if none")
    detail: str = Field(description="One or two sentences on what the new hire does")


class Gap(BaseModel):
    topic: str = Field(description="Something the role needs that no playbook covers")
    why_needed: str
    suggested_recorder: str = Field(description="Who on the team should record it, e.g. 'Finance lead'")


class OnboardingPlan(BaseModel):
    role: str
    summary: str
    coverage_percent: int = Field(description="0-100: how much of the role the existing playbooks cover")
    items: list[PlanItem]
    gaps: list[Gap]


class PlanRequest(BaseModel):
    role_description: str
    days: int = 5


# ---------- Session: the plan adapts as the new hire works ----------
class SessionRequest(BaseModel):
    plan_id: str
    item_index: int
    outcome: str = Field(description="done | stuck | already_know")
    note: str = ""


class SessionResponse(BaseModel):
    branch: str
    next_item_index: int | None
    message: str
