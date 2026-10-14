"""FirstRung core API (runs on Cloud Run).

The API is the product; the web app is just the first client. Any other client
(Google Chat bot, Slack app, HR system) can call the same endpoints later.

Every endpoint except /api/health needs `Authorization: Bearer <Firebase ID token>` (see auth.py).

  GET  /api/health          infra check, no AI, no login
  GET  /api/me              who is signed in (login check)
  GET  /api/hello           infra check, one real Gemini call
  POST /api/capture         screen recording -> playbook (call the Cloud Run URL directly: can exceed 60 s)
  POST /api/youtube/resolve      YouTube video or playlist link -> the videos it contains (no AI call yet)
  POST /api/capture/youtube      one public YouTube video -> playbook (Gemini reads the URL directly)
  GET  /api/playbooks       list the team's playbook library (newest first)
  GET  /api/playbooks/{id}  one playbook
  DELETE /api/playbooks/{id}  remove a playbook
  POST /api/plan            role description -> onboarding plan + computed coverage + gap report
  GET  /api/plans           manager view: all plans with progress
  GET  /api/plans/{id}      one plan with its saved progress (resume after refresh)
  POST /api/plans/{id}/recheck  re-match the plan's tasks against the current library -> new coverage + gaps
  POST /api/plans/{id}/restart  start a plan over (progress cleared, plan kept)
  POST /api/session         new hire reports done / stuck / already_know -> plan adapts, progress saved
  POST /api/session/voice   'I'm stuck', spoken (any language) -> spoken-language hint
  POST /api/speak           text -> speech (read a hint aloud)
  POST /api/coach/start     single-use token for a live voice coach (Gemini Live API, voice + screen)
"""
import array
import io
import json
import logging
import os
import time
import wave

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import access
import auth
import gemini
import mock_data
import prompts
import youtube
from schemas import (AskExpertRequest, AssignRequest, CoachRequest, DeclineRequest, PlanDraft, Playbook, PlanRequest,
                     Recheck, RoleSwitch, SessionRequest, SessionResponse, SpeakRequest, YouTubeCaptureRequest,
                     YouTubeResolveRequest)
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


@app.exception_handler(youtube.YouTubeError)
def youtube_problem(_request, exc: youtube.YouTubeError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


MAX_VIDEO_MB = 25  # Cloud Run requests cap at 32 MB
SignedIn = Depends(auth.current_user)  # login only (no role needed)
AnyRole = access.Member                         # signed in and holding some role
Manager = Depends(access.need("manager"))
Creator = Depends(access.need("manager", "expert"))    # may record and import playbooks
Learner = Depends(access.need("manager", "newhire"))   # may open plans (managers: all; new hires: their own)


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
def me(member: dict = AnyRole):
    """Who is signed in, their roles, and the role they are acting as."""
    return member


@app.post("/api/me/role")
def set_role(req: RoleSwitch, member: dict = AnyRole):
    """Switch the active role ("View as" in demo mode; people holding several roles can switch too)."""
    if req.role not in member["roles"]:
        raise HTTPException(403, "You don't have that role.")
    doc = store.get("users", member["uid"]) or {}
    store.put("users", {**doc, "active_role": req.role}, doc_id=member["uid"])
    return {**member, "role": req.role}


@app.get("/api/hello", dependencies=[Manager])
def hello():
    if gemini.is_mock():
        return {"model": "mock", "text": "(MOCK) Namaste! Hello! Set GEMINI_API_KEY to talk to real Gemini."}
    t = time.time()
    model, resp = gemini.generate("Say hello to a hackathon team in Hindi and English, in one short line.")
    return {"model": model, "text": resp.text, "ms": int((time.time() - t) * 1000)}


# ---------------- capture ----------------
# A plain `def` runs in a worker thread, so a 30-60 s Gemini call doesn't block other requests.
@app.post("/api/capture")
def capture(file: UploadFile = File(...), hint: str = Form("", max_length=500), request_id: str = Form(""),
            user: dict = Creator):
    data = file.file.read()
    if not data:
        raise HTTPException(400, "The video is empty. Record again and stop the recording before uploading.")
    if len(data) > MAX_VIDEO_MB * 1024 * 1024:
        raise HTTPException(413, f"Video too large; keep recordings under {MAX_VIDEO_MB} MB (about 2-3 minutes).")
    mime = (file.content_type or "video/webm").split(";")[0]
    if not mime.startswith("video/"):
        raise HTTPException(415, "Please upload a video file (WebM or MP4).")

    source = {"type": "upload", "filename": file.filename, "size_mb": round(len(data) / 1048576, 1)}
    return _make_playbook(lambda: gemini.video_part(data, mime), hint, source, user, request_id)


def _make_playbook(video, hint: str, source: dict, user: dict, request_id: str = "") -> dict:
    """Gemini watches the video (an upload or a YouTube URL) and the playbook is saved to the library.
    With request_id, it answers a recording request: the request is closed and its plan re-checked."""
    t = time.time()
    req = _open_request_for(user, request_id) if request_id else None
    if gemini.is_mock():
        playbook, model = mock_data.MOCK_PLAYBOOK, "mock"
    else:
        prompt = prompts.CAPTURE_USER.format(hint=f" Context: {hint}" if hint else "")
        model, resp = gemini.generate([video(), prompt], schema=Playbook, system=prompts.CAPTURE_SYSTEM)
        playbook = _parsed(resp, Playbook)
    doc = {**playbook, "created_at": time.time(), "created_by": _who(user), "source": source}
    pb_id = store.put("playbooks", doc)
    out = {"id": pb_id, "model": model, "ms": int((time.time() - t) * 1000), "playbook": doc}
    if req:
        store.put("requests", {**req, "status": "done", "playbook_id": pb_id, "done_at": time.time()}, doc_id=request_id)
        result = _recheck(req["plan_id"]) if store.get("plans", req["plan_id"]) else None
        out["request"] = {"id": request_id, "topic": req["topic"], **({k: result[k] for k in
                          ("coverage_before", "coverage_after", "newly_covered")} if result else {})}
    return out


def _first_name(user: dict) -> str:
    """'Prateek Kumar' -> 'Prateek'; falls back to the part of the email before '@'."""
    name = (user.get("name") or "").strip() or (user.get("email") or "there").split("@")[0].replace(".", " ")
    return name.split()[0].title() if name else "there"


def _who(user: dict) -> dict:
    return {"uid": user["uid"], "email": user.get("email", ""), "name": user.get("name", "")}


# ---------------- import from YouTube ----------------
MAX_YOUTUBE_MINUTES = 30  # longer videos rarely hold one task, and cost more of the Gemini budget


def _youtube_in_library() -> dict[str, str]:
    """video_id -> playbook id, so a video is never imported twice."""
    return {p["source"]["video_id"]: pid for pid, p in store.list("playbooks").items()
            if (p.get("source") or {}).get("type") == "youtube"}


@app.post("/api/youtube/resolve", dependencies=[Creator])
def youtube_resolve(req: YouTubeResolveRequest):
    """Which videos does this link mean? Returns them with title, channel, length and whether each can be imported.
    No Gemini call yet, so nothing is spent until the user picks videos."""
    ids = youtube.parse(req.url)
    if ids["playlist_id"] and (req.whole_playlist or not ids["video_id"]):
        pl = youtube.playlist(ids["playlist_id"])
        kind, title, videos, total = "playlist", pl["title"], pl["videos"], pl["total"]
    else:
        kind, title, total = "video", None, 1
        videos = [youtube.video(ids["video_id"])]
    have = _youtube_in_library()
    for v in videos:
        v["in_library"] = have.get(v["video_id"])
        v["problem"] = ("Private or unlisted, so Gemini can't open it" if v["privacy"] != "public"
                        else f"Longer than {MAX_YOUTUBE_MINUTES} minutes" if (v["duration_s"] or 0) > MAX_YOUTUBE_MINUTES * 60
                        else None)
    return {"kind": kind, "title": title, "total": total, "videos": videos,
            "also_playlist": bool(kind == "video" and ids["playlist_id"]), "max_minutes": MAX_YOUTUBE_MINUTES}


# Long videos can take over a minute, so the web app calls the Cloud Run URL directly (like /api/capture).
@app.post("/api/capture/youtube")
def capture_youtube(req: YouTubeCaptureRequest, user: dict = Creator):
    if pb := _youtube_in_library().get(req.video_id):
        raise HTTPException(409, f"This video is already in the library (playbook {pb}).")
    v = youtube.video(req.video_id)
    if v["privacy"] != "public":
        raise HTTPException(400, "This video is private or unlisted, so Gemini can't open it. Download it and use Upload instead.")
    if (v["duration_s"] or 0) > MAX_YOUTUBE_MINUTES * 60:
        raise HTTPException(400, f"This video is longer than {MAX_YOUTUBE_MINUTES} minutes. Pick a shorter one that shows one task.")
    url = youtube.watch_url(req.video_id)
    context = f'YouTube video "{v["title"]}" by {v["channel"]}.' + (f" {req.hint}" if req.hint else "")
    source = {"type": "youtube", "video_id": req.video_id, "url": url, "title": v["title"],
              "channel": v["channel"], "duration_s": v["duration_s"]}
    return _make_playbook(lambda: gemini.youtube_part(url), context, source, user, req.request_id)


def _playbooks_visible_to(member: dict) -> set[str] | None:
    """None = all playbooks; for new hires, only playbooks used in plans assigned to them."""
    if member["role"] != "newhire":
        return None
    return {i["playbook_id"] for p in store.list("plans").values() if access.can_see_plan(member, p)
            for i in p["items"] if i.get("playbook_id")}


@app.get("/api/playbooks")
def list_playbooks(member: dict = AnyRole):
    visible = _playbooks_visible_to(member)
    items = [{"id": k, **v} for k, v in store.list("playbooks").items() if visible is None or k in visible]
    items.sort(key=lambda p: p.get("created_at", 0), reverse=True)
    return {"playbooks": items}


@app.get("/api/playbooks/{playbook_id}")
def get_playbook(playbook_id: str, member: dict = AnyRole):
    pb = store.get("playbooks", playbook_id)
    visible = _playbooks_visible_to(member)
    if not pb or (visible is not None and playbook_id not in visible):
        raise HTTPException(404, "Playbook not found")
    return {"id": playbook_id, "playbook": pb}


@app.delete("/api/playbooks/{playbook_id}")
def delete_playbook(playbook_id: str, member: dict = Creator):
    pb = store.get("playbooks", playbook_id)
    if not pb:
        raise HTTPException(404, "Playbook not found")
    if member["role"] != "manager" and (pb.get("created_by") or {}).get("uid") != member["uid"]:
        raise HTTPException(403, "Experts can only delete playbooks they recorded.")
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
def make_plan(req: PlanRequest, user: dict = Manager):
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
            "coach_sessions": sum(e["outcome"] == "coach" for e in p["log"]),
            "percent": round(100 * finished / len(s)) if s else 100, "complete": bool(s) and finished == len(s),
            "updated_at": p.get("updated_at")}


def _next_open(status: list, after: int) -> int | None:
    """First unfinished item after `after`, else the first unfinished one anywhere, else None (plan complete)."""
    order = list(range(after + 1, len(status))) + list(range(0, after + 1))
    return next((i for i in order if status[i] not in FINISHED), None)


@app.get("/api/plans")
def list_plans(member: dict = Learner):
    """Managers: every onboarding plan with its progress, newest first. New hires: only plans assigned to them."""
    out = [{"id": pid, "role": p["role"], "coverage_percent": p.get("coverage_percent"),
            "created_at": p.get("created_at"), "created_by": p.get("created_by"), "assigned_to": p.get("assigned_to"),
            "gaps": len(p.get("gaps", [])), "progress": _summary(_progress(p))}
           for pid, p in store.list("plans").items() if access.can_see_plan(member, p)]
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"plans": out}


@app.post("/api/plans/{plan_id}/recheck", dependencies=[Manager])
def recheck_plan(plan_id: str):
    if not store.get("plans", plan_id):
        raise HTTPException(404, "Plan not found")
    return _recheck(plan_id)


def _recheck(plan_id: str) -> dict:
    """Re-match a plan's core tasks against the CURRENT library (e.g. after someone recorded a gap).
    Coverage and gaps are recomputed; the plan's items and the new hire's progress stay as they are."""
    plan = store.get("plans", plan_id)
    library = store.list("playbooks")
    tasks = plan["role_tasks"]
    before = {i: t["playbook_id"] for i, t in enumerate(tasks)}
    if not gemini.is_mock():
        _, resp = gemini.generate(prompts.RECHECK_USER.format(
            role=plan.get("role_description") or plan["role"],
            tasks="\n".join(f"{i}: {t['task']}" for i, t in enumerate(tasks)), library=_library_text(library)),
            schema=Recheck, system=prompts.RECHECK_SYSTEM)
        for m in _parsed(resp, Recheck)["matches"]:
            if 0 <= m["task_index"] < len(tasks):
                tasks[m["task_index"]]["playbook_id"] = m["playbook_id"]
    old_coverage = plan.get("coverage_percent", 0)
    plan.update(finish_plan(plan, library))
    plan["rechecked_at"] = time.time()
    store.put("plans", plan, doc_id=plan_id)
    newly = [t["task"] for i, t in enumerate(tasks) if t["playbook_id"] and not before.get(i)]
    return {"id": plan_id, "coverage_before": old_coverage, "coverage_after": plan["coverage_percent"],
            "newly_covered": newly, "gaps": len(plan["gaps"]), "plan": plan,
            "playbook_titles": {pid: p["title"] for pid, p in library.items()}}


@app.post("/api/plans/{plan_id}/restart")
def restart_plan(plan_id: str, member: dict = Learner):
    """Start the plan over: progress, stuck notes and coach usage are cleared; tasks, gaps and timetable stay."""
    plan = access.plan_for(member, plan_id)
    plan["progress"] = _new_progress(len(plan["items"]))
    plan["restarts"] = plan.get("restarts", 0) + 1
    plan["restarted_at"] = time.time()
    store.put("plans", plan, doc_id=plan_id)
    return {"id": plan_id, "plan": plan, "summary": _summary(plan["progress"])}


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str, member: dict = Learner):
    plan = access.plan_for(member, plan_id)
    plan["progress"] = _progress(plan)
    titles = {pid: store.get("playbooks", pid).get("title", pid)
              for pid in {i["playbook_id"] for i in plan["items"] if i["playbook_id"]}
              if store.get("playbooks", pid)}
    requests = [{"id": rid, **r} for rid, r in store.list("requests").items() if r["plan_id"] == plan_id] \
        if member["role"] == "manager" else []
    return {"id": plan_id, "plan": plan, "summary": _summary(plan["progress"]), "playbook_titles": titles, "requests": requests}


@app.delete("/api/plans/{plan_id}", dependencies=[Manager])
def delete_plan(plan_id: str):
    if not store.get("plans", plan_id):
        raise HTTPException(404, "Plan not found")
    store.delete("plans", plan_id)
    for rid, r in store.list("requests").items():   # its open recording requests go too
        if r["plan_id"] == plan_id and r["status"] == "open":
            store.delete("requests", rid)
    return {"deleted": plan_id}


@app.post("/api/plans/{plan_id}/assign", dependencies=[Manager])
def assign_plan(plan_id: str, req: AssignRequest):
    """Give the plan to a new hire by email (empty email = unassign). The email is invited as a new hire."""
    plan = store.get("plans", plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    email = req.email.strip().lower()
    if email:
        access.invite(email, "newhire")
    store.put("plans", {**plan, "assigned_to": email or None, "assigned_at": time.time()}, doc_id=plan_id)
    return {"id": plan_id, "assigned_to": email or None}


# ---------------- recording requests: manager asks an expert to record a gap ----------------
@app.post("/api/requests")
def ask_expert(req: AskExpertRequest, member: dict = Manager):
    plan = store.get("plans", req.plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    gap = next((g for g in plan.get("gaps", []) if g["topic"] == req.topic), None)
    if not gap:
        raise HTTPException(400, "That gap is no longer open on this plan. Re-check coverage and try again.")
    email = req.expert_email.strip().lower()
    for rid, r in store.list("requests").items():
        if r["plan_id"] == req.plan_id and r["topic"] == req.topic and r["status"] == "open":
            raise HTTPException(409, f"Already requested from {r['expert_email']}.")
    access.invite(email, "expert")
    doc = {"plan_id": req.plan_id, "plan_role": plan["role"], "assigned_to": plan.get("assigned_to"),
           "topic": gap["topic"], "why_needed": gap["why_needed"], "needed_by_day": gap["needed_by_day"],
           "importance": gap["importance"], "expert_email": email, "status": "open",
           "created_by": _who(member), "created_at": time.time()}
    rid = store.put("requests", doc)
    return {"id": rid, **doc}


@app.get("/api/requests")
def list_requests(member: dict = AnyRole):
    """Managers: every request. Experts: requests addressed to them. Newest first."""
    if member["role"] == "newhire":
        raise HTTPException(403, "New hires don't have recording requests.")
    out = [{"id": rid, **r} for rid, r in store.list("requests").items()
           if member["role"] == "manager" or r["expert_email"] == member["email"]]
    out.sort(key=lambda r: (r["status"] != "open", -r.get("created_at", 0)))
    return {"requests": out}


@app.post("/api/requests/{request_id}/decline")
def decline_request(request_id: str, req: DeclineRequest, member: dict = Depends(access.need("expert"))):
    r = _open_request_for(member, request_id)
    store.put("requests", {**r, "status": "declined", "decline_reason": req.reason.strip(),
                           "suggested": req.suggest.strip(), "declined_at": time.time()}, doc_id=request_id)
    return {"id": request_id, "status": "declined"}


def _open_request_for(member: dict, request_id: str) -> dict:
    r = store.get("requests", request_id)
    if not r or (member["role"] != "manager" and r["expert_email"] != member["email"]):
        raise HTTPException(404, "Request not found")
    if r["status"] != "open":
        raise HTTPException(409, f"This request is already {r['status']}.")
    return r


@app.get("/api/people", dependencies=[Manager])
def people():
    """Known emails by role, for the 'assign to' and 'ask an expert' pickers."""
    out = {"expert": set(), "newhire": set()}
    for u in store.list("users").values():
        for role in out:
            if role in (u.get("roles") or []) and u.get("email"):
                out[role].add(u["email"])
    for email, inv in store.list("invites").items():
        for role in out:
            if role in inv.get("roles", []):
                out[role].add(email)
    return {role: sorted(emails) for role, emails in out.items()}


# ---------------- session: the new hire reports progress, the plan adapts ----------------
def _plan_item(member: dict, plan_id: str, item_index: int) -> tuple[dict, dict]:
    plan = access.plan_for(member, plan_id)
    if not 0 <= item_index < len(plan["items"]):
        raise HTTPException(400, "item_index out of range")
    return plan, plan["items"][item_index]


def _playbook_context(item: dict) -> dict:
    """The team's playbook for this plan item, as text for a prompt."""
    pb = store.get("playbooks", item["playbook_id"]) if item.get("playbook_id") else None
    return {"pb_title": pb["title"] if pb else "(no playbook recorded for this task)",
            "steps": "; ".join(f"{s['title']}: {s['action']}" for s in pb["steps"]) if pb else "(no playbook)",
            "gotchas": "; ".join(pb.get("gotchas", [])) if pb else "(none)",
            "success": "; ".join(pb.get("success_criteria", [])) if pb else "(none)"}


def _earlier_help(prog: dict, i: int) -> str:
    """What the new hire said and what they were told the earlier times they were stuck on this item."""
    return " | ".join(f'they said "{e["note"] or "(nothing)"}", you advised: {e.get("hint", "")[:400]}'
                      for e in prog["log"] if e["item_index"] == i and e["outcome"] == "stuck") or "(none)"


def _save_progress(plan_id: str, plan: dict, prog: dict, entry: dict, nxt) -> dict:
    prog["current"] = nxt
    prog["log"] = (prog["log"] + [entry])[-100:]
    prog["updated_at"] = entry["at"]
    plan["progress"] = prog
    store.put("plans", plan, doc_id=plan_id)
    return prog


def _stuck(plan_id: str, plan: dict, i: int, note: str, user: dict, spoken: bool = False) -> SessionResponse:
    """Stay on this item and give a hint grounded in the playbook (and in what they said, typed or spoken)."""
    item, prog = plan["items"][i], _progress(plan)
    if gemini.is_mock():
        hint = mock_data.MOCK_HINT
    else:
        _, resp = gemini.generate(prompts.STUCK_USER.format(
            name=_first_name(user), title=item["title"], detail=item["detail"], earlier=_earlier_help(prog, i), note=note or "(no note)",
            **{k: v for k, v in _playbook_context(item).items() if k != "pb_title"}), system=prompts.STUCK_SYSTEM)
        hint = resp.text
    prog["status"][i] = "stuck"
    entry = {"at": time.time(), "item_index": i, "outcome": "stuck", "note": note[:500], "hint": hint,
             "by": user.get("email", ""), **({"spoken": True} if spoken else {})}
    prog = _save_progress(plan_id, plan, prog, entry, i)
    return SessionResponse(branch="hint", next_item_index=i, message=hint, heard=note if spoken else None,
                           progress=prog, summary=_summary(prog))


@app.post("/api/session", response_model=SessionResponse)
def session(req: SessionRequest, user: dict = Learner):
    plan, item = _plan_item(user, req.plan_id, req.item_index)
    if req.outcome == "stuck":
        return _stuck(req.plan_id, plan, req.item_index, req.note, user)
    items, i = plan["items"], req.item_index
    prog = _progress(plan)
    status = prog["status"]
    entry = {"at": time.time(), "item_index": i, "outcome": req.outcome, "note": req.note[:500], "by": user.get("email", "")}

    if req.outcome == "done":
        status[i] = "done"
        nxt = _next_open(status, i)
        branch, message = "next", ("Nice work. On to the next item." if nxt is not None else "Plan complete. Well done.")
    else:  # already_know: skip the whole topic, this item and every later item that uses the same playbook
        pb = item.get("playbook_id")
        skipped = [j for j in range(i, len(items)) if j == i or (pb and items[j].get("playbook_id") == pb)]
        for j in skipped:
            if status[j] not in FINISHED:
                status[j] = "skipped"
        entry["skipped"] = skipped
        nxt = _next_open(status, i)
        titles = ", ".join(f'"{items[j]["title"]}"' for j in skipped)
        branch, message = "skip", f"Skipped {titles}, since you already know this."

    prog = _save_progress(req.plan_id, plan, prog, entry, nxt)
    return SessionResponse(branch=branch, next_item_index=nxt, message=message, progress=prog, summary=_summary(prog))


# ---------------- voice ----------------
MAX_VOICE_MB = 10


@app.post("/api/session/voice", response_model=SessionResponse)
def session_voice(plan_id: str = Form(...), item_index: int = Form(...), file: UploadFile = File(...), user: dict = Learner):
    """'I'm stuck', spoken. 1) a loudness check, 2) Gemini writes down what was said (no task context, so it can't
    invent a likely question), 3) the normal hint, in the language they spoke."""
    data = file.file.read()
    mime = (file.content_type or "audio/wav").split(";")[0]
    if not data or not mime.startswith("audio/"):
        raise HTTPException(400, "No recording received. Tap Ask by voice, speak, then tap again.")
    if len(data) > MAX_VOICE_MB * 1024 * 1024:
        raise HTTPException(413, "That recording is too long. Keep the question under a minute.")
    plan, _ = _plan_item(user, plan_id, item_index)
    heard = "" if _too_quiet(data, mime) else ("(mock) spoken question" if gemini.is_mock() else gemini.transcribe(data, mime))
    if not heard:
        prog = _progress(plan)
        return SessionResponse(branch="retry", next_item_index=item_index, heard="", progress=prog, summary=_summary(prog),
                               message="I couldn't hear a question. Check your microphone, then tap Ask by voice and speak a little closer.")
    return _stuck(plan_id, plan, item_index, heard, user, spoken=True)


def _too_quiet(data: bytes, mime: str) -> bool:
    """True for (nearly) silent WAV recordings. Other formats skip the check."""
    if mime not in ("audio/wav", "audio/x-wav", "audio/wave"):
        return False
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                return False
            samples = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError):
        return False
    if not samples:
        return True
    loudest = sorted(abs(x) for x in samples[::8])[-max(1, len(samples) // 8 // 50):]   # top 2% of sampled amplitudes
    return sum(loudest) / len(loudest) < 600   # speech peaks are typically in the thousands


def _voice_available():
    if gemini.is_mock():
        raise HTTPException(503, "Voice needs Gemini. Set GEMINI_API_KEY to use it.")


@app.post("/api/speak", dependencies=[AnyRole])
def speak(req: SpeakRequest):
    """Read a hint aloud: text -> WAV in a natural voice (Gemini text-to-speech)."""
    _voice_available()
    audio, mime = gemini.speak(req.text)
    return Response(content=audio, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/coach/start")
def coach_start(req: CoachRequest, user: dict = Learner):
    """Start a live voice coach for the new hire's current item. Returns a single-use token; the browser then
    talks to the Gemini Live API directly (voice + optional screen), grounded in this item's playbook."""
    _voice_available()
    plan, item = _plan_item(user, req.plan_id, req.item_index)
    prog = _progress(plan)
    name = _first_name(user)
    instruction = prompts.COACH_SYSTEM.format(name=name, role=plan["role"], day=item["day"], title=item["title"], detail=item["detail"],
                                              earlier=_earlier_help(prog, req.item_index), **_playbook_context(item))
    token = gemini.live_token(instruction)
    entry = {"at": time.time(), "item_index": req.item_index, "outcome": "coach", "note": "", "by": user.get("email", "")}
    _save_progress(req.plan_id, plan, prog, entry, req.item_index)   # this is now their step; the manager sees the coach was used
    # The first thing the coach hears: it greets them by name and asks where they're stuck in this exact step.
    token["greeting"] = f'(Session started. Greet {name} by first name and ask where they are stuck in "{item["title"]}".)'
    return token


# Local dev convenience: serve the web app from the same server.
_static = os.getenv("STATIC_DIR")
if _static and os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
