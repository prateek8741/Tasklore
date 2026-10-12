"""FirstRung core API (runs on Cloud Run).

The API is the product; the web app is just the first client. Any other client
(Google Chat bot, Slack app, HR system) can call the same endpoints later.

Every endpoint except /api/health needs `Authorization: Bearer <Firebase ID token>` (see auth.py).

  GET  /api/health          infra check, no AI, no login
  GET  /api/me              who is signed in (login check)
  GET  /api/hello           infra check, one real Gemini call
  POST /api/capture         screen recording -> playbook
  GET  /api/playbooks       list the team's playbook library
  POST /api/plan            role description -> onboarding plan + gap report
  GET  /api/plans/{id}      fetch a plan
  POST /api/session         new hire reports done / stuck / already_know -> plan branches
"""
import json
import logging
import os
import time

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import gemini
import mock_data
import prompts
from schemas import OnboardingPlan, Playbook, PlanRequest, SessionRequest, SessionResponse
from store import store

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="FirstRung API", version="0.1.0")

# Allows other clients (local dev, a future Chat bot UI) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(gemini.GeminiError)
def gemini_failed(_request, exc: gemini.GeminiError):
    # 502 = an upstream service (Gemini) failed; clients get the reason instead of a bare 500.
    return JSONResponse(status_code=502, content={"detail": str(exc)})


MAX_VIDEO_MB = 25  # inline upload limit; Cloud Run requests cap at 32 MB
SignedIn = Depends(auth.current_user)  # add to a route to require login


def _parsed(resp, schema):
    """Structured output -> dict. Falls back to parsing the raw JSON text."""
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed.model_dump()
    return schema.model_validate(json.loads(resp.text)).model_dump()


# ---------------- infra checks ----------------
def _storage_check() -> str:
    try:
        store.get("health", "ping")  # cheap read: proves the database exists and we may use it
        return "ok"
    except Exception as e:
        return f"error: {e}"[:300]


@app.get("/api/health")
def health():
    return {"ok": True, "gemini_mode": gemini.mode(), "models": gemini.MODELS,
            "storage": "firestore" if os.getenv("USE_FIRESTORE") == "1" else "memory",
            "storage_check": _storage_check(),
            "auth": "required" if auth.AUTH_REQUIRED else "off (local dev)"}


@app.get("/api/me")
def me(user: dict = SignedIn):
    store.put("users", {**user, "last_seen": time.time()}, doc_id=user["uid"])
    return user


@app.get("/api/hello", dependencies=[SignedIn])
def hello():
    if gemini.is_mock():
        return {"model": "mock", "text": "(MOCK) Namaste! Hello! Set GEMINI_API_KEY to talk to real Gemini."}
    t = time.time()
    model, resp = gemini.generate("Say hello to a hackathon team in Hindi and English, in one short line.")
    return {"model": model, "text": resp.text, "ms": int((time.time() - t) * 1000)}


# ---------------- capture ----------------
@app.post("/api/capture", dependencies=[SignedIn])
async def capture(file: UploadFile = File(...), hint: str = Form("")):
    data = await file.read()
    if len(data) > MAX_VIDEO_MB * 1024 * 1024:
        raise HTTPException(413, f"Video too large; keep recordings under {MAX_VIDEO_MB} MB (about 2-3 minutes).")
    mime = (file.content_type or "video/webm").split(";")[0]

    if gemini.is_mock():
        playbook, model = mock_data.MOCK_PLAYBOOK, "mock"
    else:
        user = prompts.CAPTURE_USER.format(hint=f" Context from the recorder: {hint}" if hint else "")
        model, resp = gemini.generate([gemini.video_part(data, mime), user],
                                      schema=Playbook, system=prompts.CAPTURE_SYSTEM)
        playbook = _parsed(resp, Playbook)

    pb_id = store.put("playbooks", {**playbook, "created_at": time.time()})
    return {"id": pb_id, "model": model, "playbook": playbook}


@app.get("/api/playbooks", dependencies=[SignedIn])
def list_playbooks():
    return {"playbooks": [{"id": k, **v} for k, v in store.list("playbooks").items()]}


# ---------------- plan + gaps ----------------
@app.post("/api/plan", dependencies=[SignedIn])
def make_plan(req: PlanRequest):
    library = store.list("playbooks")
    if gemini.is_mock():
        plan, model = mock_data.mock_plan(req.role_description, req.days, library), "mock"
    else:
        lib_text = "\n".join(
            f"{pid}: {p['title']} - {p['goal']} - {', '.join(p.get('tools', []))}" for pid, p in library.items()
        ) or "(empty: no playbooks recorded yet)"
        model, resp = gemini.generate(
            prompts.PLAN_USER.format(role=req.role_description, days=req.days, library=lib_text),
            schema=OnboardingPlan, system=prompts.PLAN_SYSTEM)
        plan = _parsed(resp, OnboardingPlan)
        # Drop any playbook IDs the model invented.
        for item in plan["items"]:
            if item["playbook_id"] not in library:
                item["playbook_id"] = ""

    plan_id = store.put("plans", {**plan, "role_description": req.role_description, "created_at": time.time()})
    return {"id": plan_id, "model": model, "plan": plan}


@app.get("/api/plans/{plan_id}", dependencies=[SignedIn])
def get_plan(plan_id: str):
    plan = store.get("plans", plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return {"id": plan_id, "plan": plan}


# ---------------- session: the plan branches ----------------
@app.post("/api/session", response_model=SessionResponse, dependencies=[SignedIn])
def session(req: SessionRequest):
    plan = store.get("plans", req.plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    items = plan["items"]
    if not 0 <= req.item_index < len(items):
        raise HTTPException(400, "item_index out of range")
    item = items[req.item_index]

    store.put("events", {"plan_id": req.plan_id, "item_index": req.item_index,
                         "outcome": req.outcome, "note": req.note, "at": time.time()})

    if req.outcome == "done":
        nxt = req.item_index + 1
        return SessionResponse(branch="next", next_item_index=nxt if nxt < len(items) else None,
                               message="Nice work. On to the next item." if nxt < len(items) else "Plan complete!")

    if req.outcome == "already_know":
        # Skip the rest of this topic: jump past every following item that uses the same playbook.
        nxt = req.item_index + 1
        pb = item.get("playbook_id")
        while pb and nxt < len(items) and items[nxt].get("playbook_id") == pb:
            nxt += 1
        return SessionResponse(branch="skip", next_item_index=nxt if nxt < len(items) else None,
                               message="Skipped ahead since you already know this.")

    if req.outcome == "stuck":
        pb = store.get("playbooks", item["playbook_id"]) if item.get("playbook_id") else None
        steps = "; ".join(f"{s['title']}: {s['action']}" for s in pb["steps"]) if pb else "none"
        if gemini.is_mock():
            hint = mock_data.MOCK_HINT
        else:
            _, resp = gemini.generate(
                prompts.STUCK_USER.format(title=item["title"], detail=item["detail"], steps=steps,
                                          note=req.note or "(no note)"),
                system=prompts.STUCK_SYSTEM)
            hint = resp.text
        return SessionResponse(branch="hint", next_item_index=req.item_index, message=hint)

    raise HTTPException(400, "outcome must be done, stuck or already_know")


# Local dev convenience: serve the web app from the same server.
_static = os.getenv("STATIC_DIR")
if _static and os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
