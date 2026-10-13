"""FirstRung core API (runs on Cloud Run).

The API is the product; the web app is just the first client. Any other client
(Google Chat bot, Slack app, HR system) can call the same endpoints later.

Every endpoint except /api/health needs `Authorization: Bearer <Firebase ID token>` (see auth.py).

  GET  /api/health          infra check, no AI, no login
  GET  /api/me              who is signed in (login check)
  GET  /api/hello           infra check, one real Gemini call
  POST /api/capture         screen recording -> playbook (call the Cloud Run URL directly: can exceed 60 s)
  GET  /api/playbooks       list the team's playbook library (newest first)
  GET  /api/playbooks/{id}  one playbook
  DELETE /api/playbooks/{id}  remove a playbook
  POST /api/plan            role description -> onboarding plan + computed coverage + gap report
  GET  /api/plans           manager view: all plans with progress
  GET  /api/plans/{id}      one plan with its saved progress (resume after refresh)
  POST /api/session         new hire reports done / stuck / already_know -> plan adapts, progress saved
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
from schemas import PlanDraft, Playbook, PlanRequest, SessionRequest, SessionResponse
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


MAX_VIDEO_MB = 25  # Cloud Run requests cap at 32 MB
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
# A plain `def` runs in a worker thread, so a 30-60 s Gemini call doesn't block other requests.
@app.post("/api/capture")
def capture(file: UploadFile = File(...), hint: str = Form("", max_length=500), user: dict = SignedIn):
    data = file.file.read()
    if not data:
        raise HTTPException(400, "The video is empty. Record again and stop the recording before uploading.")
    if len(data) > MAX_VIDEO_MB * 1024 * 1024:
        raise HTTPException(413, f"Video too large; keep recordings under {MAX_VIDEO_MB} MB (about 2-3 minutes).")
    mime = (file.content_type or "video/webm").split(";")[0]
    if not mime.startswith("video/"):
        raise HTTPException(415, "Please upload a video file (WebM or MP4).")

    t = time.time()
    if gemini.is_mock():
        playbook, model = mock_data.MOCK_PLAYBOOK, "mock"
    else:
        prompt = prompts.CAPTURE_USER.format(hint=f" Context from the recorder: {hint}" if hint else "")
        model, resp = gemini.generate([gemini.video_part(data, mime), prompt],
                                      schema=Playbook, system=prompts.CAPTURE_SYSTEM)
        playbook = _parsed(resp, Playbook)

    doc = {**playbook, "created_at": time.time(), "created_by": _who(user),
           "source": {"filename": file.filename, "size_mb": round(len(data) / 1048576, 1)}}
    pb_id = store.put("playbooks", doc)
    return {"id": pb_id, "model": model, "ms": int((time.time() - t) * 1000), "playbook": doc}


def _who(user: dict) -> dict:
    return {"uid": user["uid"], "email": user.get("email", ""), "name": user.get("name", "")}


@app.get("/api/playbooks", dependencies=[SignedIn])
def list_playbooks():
    items = [{"id": k, **v} for k, v in store.list("playbooks").items()]
    items.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return {"playbooks": items}


@app.get("/api/playbooks/{playbook_id}", dependencies=[SignedIn])
def get_playbook(playbook_id: str):
    pb = store.get("playbooks", playbook_id)
    if not pb:
        raise HTTPException(404, "Playbook not found")
    return {"id": playbook_id, "playbook": pb}


@app.delete("/api/playbooks/{playbook_id}", dependencies=[SignedIn])
def delete_playbook(playbook_id: str):
    if not store.get("playbooks", playbook_id):
        raise HTTPException(404, "Playbook not found")
    store.delete("playbooks", playbook_id)
    return {"deleted": playbook_id}


# ---------------- plan + gaps ----------------
def _library_text(library: dict) -> str:
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


@app.post("/api/plan")
def make_plan(req: PlanRequest, user: dict = SignedIn):
    library = store.list("playbooks")
    t = time.time()
    if gemini.is_mock():
        draft, model = mock_data.mock_plan(req.role_description, req.days, library), "mock"
    else:
        model, resp = gemini.generate(
            prompts.PLAN_USER.format(role=req.role_description, days=req.days, library=_library_text(library)),
            schema=PlanDraft, system=prompts.PLAN_SYSTEM)
        draft = _parsed(resp, PlanDraft)

    plan = finish_plan(draft, library)
    plan["progress"] = _new_progress(len(plan["items"]))
    plan_id = store.put("plans", {**plan, "role_description": req.role_description, "days": req.days,
                                  "created_at": time.time(), "created_by": _who(user)})
    titles = {pid: p["title"] for pid, p in library.items()}  # lets clients show playbook names
    return {"id": plan_id, "model": model, "ms": int((time.time() - t) * 1000), "plan": plan,
            "playbook_titles": titles}


# ---------------- progress: stored on the plan, so it survives refreshes and devices ----------------
# progress.status[i] is "" (to do), "done", "skipped" or "stuck" for plan item i.
FINISHED = ("done", "skipped")


def _new_progress(n: int) -> dict:
    return {"status": [""] * n, "current": 0 if n else None, "log": [], "updated_at": None}


def _progress(plan: dict) -> dict:
    """Progress of a plan; plans made before progress existed get a fresh one."""
    p = plan.get("progress") or _new_progress(len(plan["items"]))
    if len(p["status"]) != len(plan["items"]):
        p["status"] = (p["status"] + [""] * len(plan["items"]))[:len(plan["items"])]
    return p


def _summary(p: dict) -> dict:
    s = p["status"]
    finished = sum(x in FINISHED for x in s)
    return {"total": len(s), "done": s.count("done"), "skipped": s.count("skipped"),
            "stuck_now": s.count("stuck"), "stuck_events": sum(e["outcome"] == "stuck" for e in p["log"]),
            "percent": round(100 * finished / len(s)) if s else 100, "complete": bool(s) and finished == len(s),
            "updated_at": p.get("updated_at")}


def _next_open(status: list, after: int) -> int | None:
    """First unfinished item after `after`, else the first unfinished one anywhere, else None (plan complete)."""
    order = list(range(after + 1, len(status))) + list(range(0, after + 1))
    return next((i for i in order if status[i] not in FINISHED), None)


@app.get("/api/plans", dependencies=[SignedIn])
def list_plans():
    """Manager view: every onboarding plan with its progress, newest first."""
    out = [{"id": pid, "role": p["role"], "coverage_percent": p.get("coverage_percent"),
            "created_at": p.get("created_at"), "created_by": p.get("created_by"),
            "gaps": len(p.get("gaps", [])), "progress": _summary(_progress(p))}
           for pid, p in store.list("plans").items()]
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"plans": out}


@app.get("/api/plans/{plan_id}", dependencies=[SignedIn])
def get_plan(plan_id: str):
    plan = store.get("plans", plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    plan["progress"] = _progress(plan)
    titles = {pid: store.get("playbooks", pid).get("title", pid)
              for pid in {i["playbook_id"] for i in plan["items"] if i["playbook_id"]}
              if store.get("playbooks", pid)}
    return {"id": plan_id, "plan": plan, "summary": _summary(plan["progress"]), "playbook_titles": titles}


# ---------------- session: the new hire reports progress, the plan adapts ----------------
@app.post("/api/session", response_model=SessionResponse)
def session(req: SessionRequest, user: dict = SignedIn):
    plan = store.get("plans", req.plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    items = plan["items"]
    if not 0 <= req.item_index < len(items):
        raise HTTPException(400, "item_index out of range")
    item, i = items[req.item_index], req.item_index
    prog = _progress(plan)
    status = prog["status"]
    entry = {"at": time.time(), "item_index": i, "outcome": req.outcome, "note": req.note[:500],
             "by": user.get("email", "")}

    if req.outcome == "done":
        status[i] = "done"
        nxt = _next_open(status, i)
        branch, message = "next", ("Nice work. On to the next item." if nxt is not None else "Plan complete! 🎉")

    elif req.outcome == "already_know":
        # Skip the whole topic: this item and every later item that uses the same playbook.
        pb = item.get("playbook_id")
        skipped = [j for j in range(i, len(items)) if j == i or (pb and items[j].get("playbook_id") == pb)]
        for j in skipped:
            if status[j] not in FINISHED:
                status[j] = "skipped"
        entry["skipped"] = skipped
        nxt = _next_open(status, i)
        titles = ", ".join(f'"{items[j]["title"]}"' for j in skipped)
        branch, message = "skip", f"Skipped {titles}, since you already know this."

    else:  # stuck: stay on this item and give a hint grounded in the playbook
        status[i] = "stuck"
        nxt = i
        pb = store.get("playbooks", item["playbook_id"]) if item.get("playbook_id") else None
        before = [e for e in prog["log"] if e["item_index"] == i and e["outcome"] == "stuck"]
        earlier = [f'they said "{e["note"] or "(nothing)"}", you advised: {e.get("hint", "")[:400]}' for e in before]
        if gemini.is_mock():
            hint = mock_data.MOCK_HINT
        else:
            _, resp = gemini.generate(prompts.STUCK_USER.format(
                title=item["title"], detail=item["detail"],
                steps="; ".join(f"{s['title']}: {s['action']}" for s in pb["steps"]) if pb else "(no playbook)",
                gotchas="; ".join(pb.get("gotchas", [])) if pb else "(none)",
                success="; ".join(pb.get("success_criteria", [])) if pb else "(none)",
                earlier=" | ".join(earlier) or "(none)", note=req.note or "(no note)"),
                system=prompts.STUCK_SYSTEM)
            hint = resp.text
        entry["hint"] = hint
        branch, message = "hint", hint

    prog["current"] = nxt
    prog["log"] = (prog["log"] + [entry])[-100:]
    prog["updated_at"] = entry["at"]
    plan["progress"] = prog
    store.put("plans", plan, doc_id=req.plan_id)
    return SessionResponse(branch=branch, next_item_index=nxt, message=message, progress=prog,
                           summary=_summary(prog))


# Local dev convenience: serve the web app from the same server.
_static = os.getenv("STATIC_DIR")
if _static and os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
