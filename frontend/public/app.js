"use strict";
// FirstRung web app: the first client of the FirstRung API (backend/main.py).
// Every API call carries the signed-in user's Firebase ID token; the API checks it.

const API = window.FIRSTRUNG_API || "";            // same origin: Firebase Hosting forwards /api/** to Cloud Run
const DIRECT_API = window.FIRSTRUNG_DIRECT_API || API; // video uploads go straight to Cloud Run (Hosting stops at 60 s)
const FIREBASE_SDK = "https://www.gstatic.com/firebasejs/12.19.0";
const MAX_MB = 25;
const IMPORTANCE = { 3: "Critical", 2: "Regular", 1: "Occasional" };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  authOn: false, user: null, fb: null, auth: null,
  plan: null, planId: null, progress: null, titles: {}, openSteps: null,
  previewUrl: null, lastPlanKey: "firstrung.lastPlan",
};

// ---------------- toasts ----------------
// A toast can carry one action (e.g. Undo). It materializes in, and leaves the same way it came.
function toast(msg, kind = "info", ms = 4500, action = null) {
  const t = document.createElement("div");
  t.className = `toast ${kind}`; t.setAttribute("role", kind === "bad" ? "alert" : "status");
  t.append(Object.assign(document.createElement("span"), { textContent: msg }));
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 220); };
  if (action) {
    const b = Object.assign(document.createElement("button"), { textContent: action.label });
    b.onclick = () => { action.onClick(); close(); };
    t.append(b);
  }
  $("#toasts").append(t); setTimeout(close, ms);
  return close;
}

// ---------------- appearance: Auto (follow the device) / Light / Dark ----------------
// The choice is a per-browser convenience, so it lives in localStorage; <head> applies it before first paint.
const THEME_KEY = "firstrung.theme";
function setTheme(mode, animate = true) {
  const root = document.documentElement;
  if (animate && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.classList.add("theme-anim"); setTimeout(() => root.classList.remove("theme-anim"), 400);
  }
  if (mode === "auto") delete root.dataset.theme; else root.dataset.theme = mode;
  try { mode === "auto" ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, mode); } catch {}
  $$("[data-theme-set]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.themeSet === mode)));
  // Product screenshots come in light and dark versions; make them follow the chosen theme, not only the device.
  $$("source[data-dark]").forEach(s => s.media = mode === "auto" ? "(prefers-color-scheme: dark)" : mode === "dark" ? "all" : "not all");
}
setTheme(document.documentElement.dataset.theme || "auto", false);

// The top bar shows its edge only when content scrolls underneath it.
const onScroll = () => document.body.classList.toggle("scrolled", window.scrollY > 4);
window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

// ---------------- sign-in (Firebase Auth with Google) ----------------
// Firebase Hosting serves the project's public config at /__/firebase/init.json, so no keys live in this code.
// Locally (uvicorn) it is missing and sign-in stays off; the API then runs without login too.
const authReady = (async () => {
  try {
    const r = await fetch("/__/firebase/init.json");
    if (!r.ok) throw new Error("no Firebase config here");
    const config = await r.json();
    const [{ initializeApp }, fb] = await Promise.all([
      import(`${FIREBASE_SDK}/firebase-app.js`), import(`${FIREBASE_SDK}/firebase-auth.js`)]);
    state.fb = fb; state.auth = fb.getAuth(initializeApp(config)); state.authOn = true;
    await new Promise(done => fb.onAuthStateChanged(state.auth, u => { state.user = u; renderAuth(); done(); }));
  } catch (e) {
    console.info("Sign-in off (local dev):", e.message);
    renderAuth();
  }
})();

function renderAuth() {
  const box = $("#authBox");
  if (!state.authOn) box.innerHTML = `<span class="chip">Local dev, sign-in off</span>`;
  else if (state.user) {
    const name = state.user.displayName || state.user.email;
    box.innerHTML = `<span class="avatar" aria-hidden="true">${esc(name[0].toUpperCase())}</span>
      <span class="who" title="${esc(state.user.email)}">${esc(name)}</span>
      <button class="btn sm ghost" data-action="sign-out">Sign out</button>`;
  } else box.innerHTML = `<button class="btn primary sm" data-action="sign-in">Sign in with Google</button>`;
  route();
}
async function signIn() {
  try { await state.fb.signInWithPopup(state.auth, new state.fb.GoogleAuthProvider()); }
  catch (e) { if (!/popup-closed|cancelled/.test(e.code || "")) toast("Sign-in didn't work: " + (e.code || e.message), "bad"); }
}

// ---------------- API ----------------
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

function friendly(status, detail) {
  const d = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(x => x.msg).join("; ") : "";
  if (status === 401) return "Your sign-in has expired. Please sign in again.";
  if (/prepayment|credits are depleted/i.test(d)) return "Gemini's credits are used up. Top up in Google AI Studio, then try again.";
  if (status === 502) return "Gemini couldn't finish this one. Please try again in a moment.";
  if (status === 429) return "Gemini is busy right now. Wait a few seconds and try again.";
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return d || `Request failed (HTTP ${status}).`;
}
async function token() { await authReady; return state.user ? state.user.getIdToken() : null; }

async function call(path, opts = {}, base = API) {
  const headers = { ...(opts.headers || {}) };
  const t = await token(); if (t) headers.Authorization = "Bearer " + t;
  let r;
  try { r = await fetch(base + path, { ...opts, headers }); }
  catch { throw new ApiError("Can't reach FirstRung. Check your internet connection and try again.", 0); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(friendly(r.status, body.detail), r.status);
  return body;
}
const postJSON = (path, data) => call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

// Uploads use XMLHttpRequest because fetch can't report upload progress.
async function upload(path, form, onProgress) {
  const t = await token();
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", DIRECT_API + path);
    if (t) x.setRequestHeader("Authorization", "Bearer " + t);
    x.upload.onprogress = e => e.lengthComputable && onProgress(e.loaded / e.total);
    x.upload.onload = () => onProgress(1);
    x.onload = () => {
      let body = {}; try { body = JSON.parse(x.responseText); } catch {}
      x.status < 300 ? resolve(body) : reject(new ApiError(friendly(x.status, body.detail), x.status));
    };
    x.onerror = () => reject(new ApiError("The upload failed. Check your connection and try again.", 0));
    x.send(form);
  });
}

const skeleton = (lines = 3) => Array.from({ length: lines }, (_, i) => `<div class="skel line" style="width:${90 - i * 18}%"></div>`).join("");

// ---------------- routing ----------------
const VIEWS = ["home", "capture", "plan", "learn", "system"];
const ON_ENTER = { home: loadStats, capture: loadLibrary, plan: loadPlans, learn: loadPlanPicker };
function route() {
  let v = location.hash.slice(1) || "home";
  if (!VIEWS.includes(v)) v = "home";
  const gated = v !== "home" && state.authOn && !state.user;
  $$(".view").forEach(s => s.hidden = true);
  $(gated ? "#view-signin" : `#view-${v}`).hidden = false;
  $$("#nav a").forEach(a => { const on = a.dataset.view === v; a.classList.toggle("on", on); on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"); });
  if (!gated && (v === "home" ? state.user || !state.authOn : true)) ON_ENTER[v]?.();
}
window.addEventListener("hashchange", () => { route(); window.scrollTo(0, 0); });

// ---------------- home ----------------
async function loadStats() {
  try {
    const [{ playbooks }, { plans }] = await Promise.all([call("/api/playbooks"), call("/api/plans")]);
    const avg = plans.length ? Math.round(plans.reduce((s, p) => s + (p.coverage_percent || 0), 0) / plans.length) : 0;
    const active = plans.filter(p => !p.progress.complete && p.progress.updated_at).length;
    $("#stats").innerHTML = [[playbooks.length, "playbooks recorded"], [plans.length, "onboarding plans"],
      [plans.length ? avg + "%" : "-", "average role coverage"], [active, "new hires in progress"]]
      .map(([n, l]) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`).join("");
    $("#stats").hidden = false;
  } catch { $("#stats").hidden = true; }
}

// ---------------- 1. capture ----------------
let recorder = null, recTimer = null;
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

if (!navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) { $("#recIdle").hidden = true; $("#recUnsupported").hidden = false; }

$("#btnRec").onclick = async () => {
  let screen;
  try { screen = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false }); }
  catch { return toast("Screen sharing was cancelled."); }
  const tracks = [...screen.getVideoTracks()];
  try { const mic = await navigator.mediaDevices.getUserMedia({ audio: true }); tracks.push(...mic.getAudioTracks()); }
  catch { toast("No microphone: recording without narration. Explaining out loud makes much better playbooks.", "bad", 7000); }
  const stream = new MediaStream(tracks);
  const type = ["video/webm;codecs=vp9,opus", "video/webm", "video/mp4"].find(t => MediaRecorder.isTypeSupported(t)) || "";
  const chunks = []; let bytes = 0; const started = Date.now();
  recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 600000 });
  recorder.ondataavailable = e => {
    if (!e.data.size) return;
    chunks.push(e.data); bytes += e.data.size;
    $("#recSize").textContent = `${(bytes / 1048576).toFixed(1)} MB`;
    if (bytes > (MAX_MB - 1) * 1048576 && recorder.state === "recording") { toast("Reached the size limit, so recording stopped."); recorder.stop(); }
  };
  recorder.onstop = () => {
    stream.getTracks().forEach(t => t.stop()); clearInterval(recTimer);
    $("#recLive").hidden = true; $("#recIdle").hidden = false;
    const blob = new Blob(chunks, { type: (type || "video/webm").split(";")[0] });
    sendVideo(blob, type.includes("mp4") ? "recording.mp4" : "recording.webm");
  };
  screen.getVideoTracks()[0].onended = () => recorder.state === "recording" && recorder.stop();
  recorder.start(1000);
  $("#recIdle").hidden = true; $("#recLive").hidden = false; $("#recTime").textContent = "0:00"; $("#recSize").textContent = "";
  recTimer = setInterval(() => $("#recTime").textContent = fmtTime((Date.now() - started) / 1000), 500);
};
$("#btnStop").onclick = () => recorder && recorder.state === "recording" && recorder.stop();

const drop = $("#drop");
drop.onclick = () => $("#file").click();
drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file").click(); } };
$("#file").onchange = e => { const f = e.target.files[0]; if (f) sendVideo(f, f.name); e.target.value = ""; };
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) sendVideo(f, f.name); };

function stepper(stage, uploadPct, secs, mb) {
  const steps = [[`Uploading ${mb} MB${stage === 0 ? `, ${Math.round(uploadPct * 100)}%` : ""}`],
                 [`Gemini is watching the recording${stage === 1 ? ` (${secs} s)` : ""}`],
                 ["Writing the playbook: steps, the why, gotchas, checks"]];
  return `<div class="card"><h2>Creating the playbook</h2><p class="muted small">Usually 15 to 60 seconds. You can keep this tab open while it works.</p>
    <div class="stepper">${steps.map(([t], i) => `<div class="s ${i < stage ? "done" : i === stage ? "active" : ""}"><span class="dot">${i < stage ? "✓" : ""}</span>${t}</div>`).join("")}</div>
    ${stage === 0 ? `<div class="bar" style="margin-top:12px"><div style="width:${Math.round(uploadPct * 100)}%"></div></div>` : ""}</div>`;
}

async function sendVideo(blob, name) {
  const mb = (blob.size / 1048576).toFixed(1);
  if (!blob.type.startsWith("video/") && !/\.(mp4|webm|mov|m4v)$/i.test(name)) return toast("Please choose a video file (MP4 or WebM).", "bad");
  if (blob.size > MAX_MB * 1048576) return toast(`This video is ${mb} MB. Keep recordings under ${MAX_MB} MB (about 2 to 3 minutes).`, "bad", 7000);
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(blob);

  const out = $("#capOut"); let stage = 0, pct = 0, waitStart = 0;
  const draw = () => out.innerHTML = stepper(stage, pct, Math.round((Date.now() - waitStart) / 1000), mb);
  draw(); out.scrollIntoView({ behavior: "smooth", block: "nearest" });
  const timer = setInterval(() => stage === 1 && draw(), 1000);
  const form = new FormData(); form.append("file", blob, name); form.append("hint", $("#hint").value);
  try {
    const r = await upload("/api/capture", form, p => { pct = p; if (p >= 1 && stage === 0) { stage = 1; waitStart = Date.now(); } draw(); });
    clearInterval(timer);
    out.innerHTML = playbookCard(r.playbook, r.id, { preview: state.previewUrl, footer: `Saved to your team's library in ${Math.round(r.ms / 1000)} s` })
      + `<div class="card flat row" style="justify-content:space-between"><span>Did this fill a gap? Plans built earlier don't update by themselves.</span>
         <a class="btn sm" href="#plan">Re-check your plans</a></div>`;
    toast("Playbook created and added to the library.", "good");
    loadLibrary();
  } catch (e) {
    clearInterval(timer);
    out.innerHTML = `<div class="card"><h2>That didn't work</h2><p>${esc(e.message)}</p>
      <button class="btn primary" data-action="retry-upload">Try again</button></div>`;
    state.retry = () => sendVideo(blob, name);
  }
}

// ---------------- import from YouTube ----------------
// 1) resolve the link into videos (no Gemini cost), 2) the user picks, 3) one playbook per video, one after another.
const MAX_YT = 10;
const secsOf = (ts) => String(ts).split(":").map(Number).reduce((a, n) => a * 60 + (n || 0), 0);
const ytThumb = (id) => `https://i.ytimg.com/vi/${encodeURIComponent(id)}/mqdefault.jpg`;

$("#btnYt").onclick = () => findYouTube(false);
$("#ytUrl").onkeydown = e => { if (e.key === "Enter") findYouTube(false); };  // a handler returning false would swallow typing

async function findYouTube(wholePlaylist) {
  const url = $("#ytUrl").value.trim(), btn = $("#btnYt");
  if (!url) return $("#ytUrl").focus();
  btn.disabled = true; btn.textContent = "Finding…";
  $("#ytOut").innerHTML = skeleton(2);
  try {
    state.yt = await postJSON("/api/youtube/resolve", { url, whole_playlist: wholePlaylist });
    state.yt.status = {};
    let picked = 0;   // pre-select importable videos, up to the limit
    state.yt.videos.forEach(v => v.pick = !v.in_library && !v.problem && picked++ < MAX_YT);
    renderYouTube();
  } catch (e) { $("#ytOut").innerHTML = `<p class="field-note">${esc(e.message)}</p>`; }
  btn.disabled = false; btn.textContent = "Find videos";
}

function renderYouTube() {
  const y = state.yt, busy = !!y.running, n = y.videos.filter(v => v.pick).length;
  const head = y.kind === "playlist"
    ? `<b>${esc(y.title)}</b> <span class="small muted">${y.videos.length} videos${y.total > y.videos.length ? ` (first ${y.videos.length} of ${y.total})` : ""}</span>`
    : `<b>1 video</b>${y.also_playlist ? ` <button class="btn sm ghost" data-yt-playlist ${busy ? "disabled" : ""}>Show the whole playlist instead</button>` : ""}`;
  $("#ytOut").innerHTML = `<div class="yt-head"><div>${head}</div></div>
    ${y.videos.map((v, i) => {
      const st = y.status[v.video_id], off = v.in_library || v.problem;
      const status = st ? st.html : v.in_library ? `<span class="chip good">Already in library</span>` : v.problem ? `<span class="chip warn">${esc(v.problem)}</span>` : "";
      return `<div class="yt-row ${off ? "off" : ""}">
        <input type="checkbox" data-yt-pick="${i}" ${v.pick ? "checked" : ""} ${off || busy ? "disabled" : ""} aria-label="Import ${esc(v.title)}">
        <img src="${ytThumb(v.video_id)}" alt="" loading="lazy">
        <div class="t"><b title="${esc(v.title)}">${esc(v.title)}</b><span class="small muted">${esc(v.channel)}${v.duration_s ? `, ${fmtTime(v.duration_s)}` : ""}</span></div>
        <div class="st-col small">${status}</div></div>`; }).join("")}
    <div class="yt-foot"><span class="small muted">${n > MAX_YT ? `<span class="field-note">Pick at most ${MAX_YT} videos per import.</span>` : n ? `${n} selected. Each takes about 15 to 60 seconds.` : "Tick the videos to turn into playbooks."}</span>
      <button class="btn primary" data-yt-import ${busy || !n || n > MAX_YT ? "disabled" : ""}>${busy ? "Creating playbooks…" : n ? `Create ${n} playbook${n === 1 ? "" : "s"}` : "Create playbooks"}</button></div>`;
}

async function importYouTube() {
  const y = state.yt, queue = y.videos.filter(v => v.pick);
  y.running = true; let ok = 0;
  const hint = $("#hint").value.trim();
  for (const v of queue) {
    const started = Date.now();
    const tick = () => { y.status[v.video_id] = { html: `<span class="chip brand">Gemini is watching, ${Math.round((Date.now() - started) / 1000)} s</span>` }; renderYouTube(); };
    tick(); const timer = setInterval(tick, 1000);
    try {
      const r = await call("/api/capture/youtube", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: v.video_id, hint }) }, DIRECT_API);
      ok++; v.pick = false; v.in_library = r.id;
      y.status[v.video_id] = { html: `<span class="chip good">Done</span> <button class="btn sm ghost" data-view-pb="${esc(r.id)}">View</button>` };
      loadLibrary();
    } catch (e) {
      y.status[v.video_id] = { html: `<span class="chip bad" title="${esc(e.message)}">Failed</span> <span class="small muted">${esc(e.message)}</span>` };
    }
    clearInterval(timer); renderYouTube();
  }
  y.running = false; renderYouTube();
  if (ok) {
    toast(`${ok} playbook${ok === 1 ? "" : "s"} created from YouTube.`, "good", 6000);
    $("#ytOut").insertAdjacentHTML("beforeend", `<div class="row" style="justify-content:space-between; margin-top:12px">
      <span class="small">Did these fill a gap? Plans built earlier don't update by themselves.</span><a class="btn sm" href="#plan">Re-check your plans</a></div>`);
  }
}

function playbookCard(p, id, { preview, footer, closable } = {}) {
  const yt = p.source?.type === "youtube" ? p.source : null;
  const by = yt ? `From YouTube: <a href="${esc(yt.url)}" target="_blank" rel="noopener">${esc(yt.title)}</a> by ${esc(yt.channel)}. Imported by ${esc(p.created_by?.name || "a teammate")}`
           : p.created_by?.name ? `Recorded by ${esc(p.created_by.name)}` : "";
  const tsHtml = (s) => yt ? `<a class="ts" href="${esc(yt.url)}&t=${secsOf(s.timestamp)}s" target="_blank" rel="noopener" title="Open this moment on YouTube">${esc(s.timestamp)}</a>`
           : preview ? `<button class="ts" data-seek="${esc(s.timestamp)}" title="Jump to this moment">${esc(s.timestamp)}</button>` : `<span class="ts">${esc(s.timestamp)}</span>`;
  return `<div class="card" id="pb-${esc(id)}">
    <div class="pb-head"><div><h2>${esc(p.title)}</h2><p class="muted">${esc(p.goal)}</p></div>
      ${closable ? `<button class="btn sm ghost" data-action="close-playbook">Close</button>` : ""}</div>
    <div>${p.role_hint ? `<span class="chip brand">${esc(p.role_hint)}</span>` : ""}${(p.tools || []).map(t => `<span class="chip">${esc(t)}</span>`).join("")}
      ${p.estimated_minutes ? `<span class="chip">~${esc(p.estimated_minutes)} min</span>` : ""}
      ${p.narration_language && p.narration_language !== "English" ? `<span class="chip warn">Narrated in ${esc(p.narration_language)}</span>` : ""}</div>
    ${preview ? `<video class="pb-video" src="${preview}" controls preload="metadata"></video>` : ""}
    <ol class="timeline">${p.steps.map(s => `<li><b>${esc(s.title)}</b>${tsHtml(s)}
      <div>${esc(s.action)}</div><div class="why">Why: ${esc(s.why)}</div></li>`).join("")}</ol>
    <div class="lists">
      <div class="warn"><h3>Watch out for</h3><ul>${p.gotchas.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div>
      <div class="good"><h3>Done right when</h3><ul>${p.success_criteria.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div>
    </div>
    ${(p.skills || []).length ? `<p class="small muted" style="margin-top:12px">Skills: ${p.skills.map(esc).join(", ")}</p>` : ""}
    <p class="small muted">${[by, footer && esc(footer)].filter(Boolean).join(". ")}</p></div>`;
}

async function loadLibrary() {
  if (!$("#library .lib-card")) $("#library").innerHTML = `<div class="skel block"></div><div class="skel block"></div>`;
  try {
    const { playbooks } = await call("/api/playbooks");
    $("#libCount").textContent = playbooks.length ? `${playbooks.length} recorded` : "";
    $("#library").innerHTML = playbooks.length ? playbooks.map(p => `<div class="card lib-card">
        <h3>${esc(p.title)}</h3>
        <span class="small muted">${esc(p.role_hint)}, ${p.steps.length} steps${p.estimated_minutes ? `, about ${esc(p.estimated_minutes)} min` : ""}</span>
        <div>${(p.skills || []).slice(0, 4).map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>
        ${p.source?.type === "youtube" ? `<span class="small muted">From YouTube, ${esc(p.source.channel)}</span>`
          : p.created_by?.name ? `<span class="small muted">by ${esc(p.created_by.name)}</span>` : ""}
        <div class="actions"><button class="btn sm" data-view-pb="${esc(p.id)}">View</button>
          <button class="btn sm ghost" data-del-pb="${esc(p.id)}" aria-label="Delete ${esc(p.title)}">Delete</button></div></div>`).join("")
      : `<div class="empty" style="grid-column:1/-1">No playbooks yet. Record the first task above; it takes about two minutes.</div>`;
  } catch (e) { $("#library").innerHTML = `<div class="empty" style="grid-column:1/-1">Couldn't load the library. ${esc(e.message)}</div>`; }
}
async function viewPlaybook(id) {
  try {
    const { playbook } = await call(`/api/playbooks/${id}`);
    $("#capOut").innerHTML = playbookCard(playbook, id, { closable: true });
    $("#capOut").scrollIntoView({ behavior: "smooth" });
  } catch (e) { toast(e.message, "bad"); }
}
// Delete with Undo instead of an "are you sure?" dialog: the card leaves at once, the API call waits 5 s.
function deletePlaybook(id, btn) {
  const card = btn.closest(".lib-card"); card.classList.add("leaving");
  const commit = setTimeout(async () => {
    try { await call(`/api/playbooks/${id}`, { method: "DELETE" }); $(`#pb-${CSS.escape(id)}`)?.remove(); loadLibrary(); }
    catch (e) { card.classList.remove("leaving"); toast(e.message, "bad"); }
  }, 5000);
  toast("Playbook deleted.", "info", 5000, { label: "Undo", onClick: () => { clearTimeout(commit); card.classList.remove("leaving"); } });
}

// ---------------- 2. plan & gaps ----------------
function ring(pct, cls = "") {
  const c = 2 * Math.PI * 42, off = c * (1 - pct / 100);
  return `<svg class="ring ${cls}" viewBox="0 0 100 100" role="img" aria-label="${pct}%">
    <circle class="track" cx="50" cy="50" r="42" fill="none" stroke-width="10"/>
    <circle class="val" cx="50" cy="50" r="42" fill="none" stroke-width="10" stroke-linecap="round" style="--c:${c.toFixed(1)}"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 50 50)"/>
    <text x="50" y="58" text-anchor="middle">${pct}%</text></svg>`;
}
$$("[data-role]").forEach(b => b.onclick = () => { $("#role").value = b.dataset.role; $("#roleNote").hidden = true; $("#role").focus(); });
$("#role").oninput = () => { if ($("#role").value.trim().length >= 10) $("#roleNote").hidden = true; };

$("#btnPlan").onclick = async (ev) => {
  const btn = ev.currentTarget, role = $("#role").value.trim();
  $("#roleNote").hidden = role.length >= 10;   // validate inline, next to the field
  if (role.length < 10) return $("#role").focus();
  btn.disabled = true; btn.textContent = "Building the plan…";
  $("#planOut").innerHTML = `<div class="card" aria-busy="true"><p class="muted small">Matching the role against your playbook library and finding the gaps. This takes about 15 seconds.</p>${skeleton(3)}</div><div class="grid2"><div class="skel block"></div><div class="skel block"></div></div>`;
  try {
    const r = await postJSON("/api/plan", { role_description: role, days: +$("#days").value });
    state.titles = r.playbook_titles || {};
    renderPlan(r.plan, r.id);
    loadPlans();
  } catch (e) { $("#planOut").innerHTML = `<div class="card"><h2>That didn't work</h2><p>${esc(e.message)}</p></div>`; }
  btn.disabled = false; btn.textContent = "Generate plan & find gaps";
};

const KIND_HELP = { setup: "get accounts and tool access", learn: "watch a recorded playbook",
                    practice: "do the task yourself", meet: "sit with a colleague, for what isn't recorded yet" };

function renderPlan(plan, id) {
  const tasks = plan.role_tasks || [], covered = tasks.filter(t => t.playbook_id).length;
  const total = tasks.reduce((s, t) => s + t.importance, 0), got = tasks.filter(t => t.playbook_id).reduce((s, t) => s + t.importance, 0);
  const byDay = {}; plan.items.forEach(it => (byDay[it.day] ||= []).push(it));
  const days = Object.keys(byDay).length;
  $("#planOut").innerHTML = `
    <div class="card"><div class="cover">${ring(plan.coverage_percent)}
      <div style="flex:1; min-width:240px"><h2>${esc(plan.role)}: ${plan.coverage_percent}% of this job is already recorded</h2>
        <p class="muted">${covered} of ${tasks.length} core tasks have a playbook your team recorded. The rest are listed under "Missing know-how".</p>
        <details class="how"><summary>How is this % calculated?</summary>
          <p class="small muted">Each core task gets points by importance: Critical 3, Regular 2, Occasional 1. Coverage is the share of points whose task has a playbook.</p>
          <table><thead><tr><th>Core task</th><th>Importance</th><th>Points</th><th>Recorded?</th></tr></thead><tbody>
          ${tasks.map(t => `<tr><td>${esc(t.task)}</td><td>${IMPORTANCE[t.importance] || ""}</td><td>${t.importance}</td><td>${t.playbook_id ? "Yes" : "No"}</td></tr>`).join("")}
          </tbody><tfoot><tr><td colspan="4">${got} recorded points out of ${total} = <b>${plan.coverage_percent}%</b></td></tr></tfoot></table></details>
        <p class="small muted">Plan summary: ${esc(plan.summary)}</p>
        <a class="btn primary" href="#learn" data-open-plan="${esc(id)}">Start this plan as the new hire</a></div></div></div>
    <div class="grid2">
      <div class="card"><h2>Core tasks of the role</h2>
        <p class="muted small">What this job involves, and which parts your team has already recorded. ✓ means a playbook teaches it; ! means nobody has recorded it yet.</p>
        ${tasks.map(t => `<div class="task"><span class="mark ${t.playbook_id ? "yes" : "no"}" aria-label="${t.playbook_id ? "covered" : "gap"}">${t.playbook_id ? "✓" : "!"}</span>
          <div style="flex:1"><div>${esc(t.task)}</div><span class="small muted">${t.playbook_id ? "Covered by “" + esc(state.titles[t.playbook_id] || "a playbook") + "”" : "Not recorded yet"}</span></div>
          <span class="chip ${t.importance === 3 ? "bad" : ""}">${IMPORTANCE[t.importance] || ""}</span></div>`).join("")}</div>
      <div class="card"><h2>Missing know-how (${plan.gaps.length})</h2>
        <p class="muted small">Tasks this job needs that nobody has recorded. Send the request to the suggested person; once they record it, click "Re-check coverage" under Team onboarding and the % goes up.</p>
        ${plan.gaps.map((g, i) => `<div class="gap"><b>${esc(g.topic)}</b><div class="small">${esc(g.why_needed)}</div>
          <div class="foot"><span class="small">Ask <b>${esc(g.suggested_recorder)}</b>, needed by day ${esc(g.needed_by_day)} ${g.importance === 3 ? `<span class="chip bad">Critical</span>` : ""}</span>
          <button class="btn sm" data-copy-gap="${i}">Copy request</button></div></div>`).join("") || `<div class="empty">No gaps. Your library covers this whole role.</div>`}</div>
    </div>
    <div class="card"><h2>Day-by-day plan</h2>
      <p class="muted small">The new hire's timetable for their first ${days} day${days === 1 ? "" : "s"}.
        ${Object.entries(KIND_HELP).map(([k, v]) => `<span class="k ${k}">${k[0].toUpperCase() + k.slice(1)}</span>: ${v}`).join(". ")}.</p>
      <div class="days">${Object.entries(byDay).map(([d, items]) => `<div class="day"><h3>Day ${d}</h3>${items.map(it => `<div class="pi">
        <span class="k ${esc(it.kind)}">${esc(it.kind)}</span><div><b>${esc(it.title)}</b></div><div class="small muted">${esc(it.detail)}</div>
        ${it.playbook_id ? `<div class="small" style="margin-top:4px">Playbook: ${esc(state.titles[it.playbook_id] || "recorded task")}</div>` : ""}</div>`).join("")}</div>`).join("")}</div></div>`;
  state.lastPlan = plan; state.lastPlanId = id;
  $("#planOut").scrollIntoView({ behavior: "smooth" });
}

function copyGap(i) {
  const plan = state.lastPlan, g = plan.gaps[i];
  const text = `Hi! We're onboarding a new ${plan.role} and nobody has recorded "${g.topic}" yet. Could you record a 2 to 3 minute FirstRung capture of you doing it, explaining each step out loud? They'll need it by day ${g.needed_by_day}. Thank you!`;
  navigator.clipboard.writeText(text).then(() => toast("Request copied. Paste it into chat or email.", "good"), () => prompt("Copy this message:", text));
}

const ago = (t) => { if (!t) return "not started yet"; const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "active just now" : m < 60 ? `active ${m} min ago` : m < 1440 ? `active ${Math.round(m / 60)} h ago` : `active ${Math.round(m / 1440)} d ago`; };

const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

// Restart with Undo instead of an "are you sure?" dialog: progress is cleared only after 5 s.
function restartPlan(id, btn) {
  btn.disabled = true;
  const commit = setTimeout(async () => {
    try {
      const r = await call(`/api/plans/${id}/restart`, { method: "POST" });
      if (state.planId === id) { state.plan = r.plan; state.progress = r.plan.progress; state.openSteps = null; renderLearn(); }
      loadPlans(); toast("Plan restarted from the first step.", "good");
    } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
  }, 5000);
  toast("Restarting this plan: progress and stuck notes will be cleared.", "info", 5000,
        { label: "Undo", onClick: () => { clearTimeout(commit); btn.disabled = false; } });
}

// Plans are snapshots of the library when they were built. Re-checking matches the plan's core tasks
// against today's library, so a gap someone has since recorded now counts as covered.
async function recheckPlan(id, btn) {
  btn.disabled = true; btn.textContent = "Re-checking…";
  try {
    const r = await call(`/api/plans/${id}/recheck`, { method: "POST" });
    const up = r.coverage_after - r.coverage_before;
    if (r.newly_covered.length) toast(`Coverage ${r.coverage_before}% to ${r.coverage_after}%. Now covered: ${r.newly_covered.join(", ")}.`, "good", 8000);
    else toast(`No change: none of the new playbooks teach this role's missing tasks. Coverage stays ${r.coverage_after}%.`, "info", 7000);
    if (state.lastPlanId === id && up !== 0) { state.titles = r.playbook_titles || {}; renderPlan(r.plan, id); }
    loadPlans();
  } catch (e) { toast(e.message, "bad"); btn.disabled = false; btn.textContent = "Re-check coverage"; }
}

async function loadPlans() {
  if (!$("#plansList .plan-row")) $("#plansList").innerHTML = skeleton(2);
  try {
    const { plans } = await call("/api/plans");
    $("#plansList").innerHTML = plans.length ? plans.map(p => `<div class="plan-row">${ring(p.progress.percent, "small")}
        <div class="grow"><b>${esc(p.role)}</b> <span class="small muted">created ${fmtDate(p.created_at)}</span>
          <div class="small muted">Coverage ${p.coverage_percent}%, ${p.gaps} gaps, ${ago(p.progress.updated_at)}</div>
          <div class="small">${p.progress.complete ? `<span class="chip good">Complete</span>` : `${p.progress.done} done, ${p.progress.skipped} skipped`}
          ${p.progress.stuck_events ? ` <span class="chip warn">stuck ${p.progress.stuck_events}×</span>` : ""}${p.progress.coach_sessions ? ` <span class="chip brand">live coach ${p.progress.coach_sessions}×</span>` : ""}</div></div>
        <div class="row">${p.gaps ? `<button class="btn sm ghost" data-recheck="${esc(p.id)}" title="Match this plan against the playbooks recorded since">Re-check coverage</button>` : ""}
          ${p.progress.updated_at ? `<button class="btn sm ghost" data-restart="${esc(p.id)}" title="Clear this plan's progress and start again">Restart</button>` : ""}
          <a class="btn sm" href="#learn" data-open-plan="${esc(p.id)}">Open</a></div></div>`).join("")
      : `<div class="empty">No onboarding plans yet. Describe a role above to create the first one.</div>`;
  } catch (e) { $("#plansList").innerHTML = `<div class="empty">Couldn't load plans. ${esc(e.message)}</div>`; }
}

// ---------------- 3. new hire ----------------
const remember = (id) => { try { localStorage.setItem(state.lastPlanKey, id); } catch {} };
const recalled = () => { try { return localStorage.getItem(state.lastPlanKey); } catch { return null; } };

async function loadPlanPicker() {
  try {
    const { plans } = await call("/api/plans");
    const want = [recalled(), state.planId, plans[0]?.id].find(id => id && plans.some(p => p.id === id));
    $("#planPick").innerHTML = plans.length
      ? plans.map(p => `<option value="${esc(p.id)}" ${p.id === want ? "selected" : ""}>${esc(p.role)} (${p.progress.percent}% done, coverage ${p.coverage_percent}%)</option>`).join("")
      : `<option value="">No plans yet</option>`;
    if (!plans.length) { $("#learnOut").innerHTML = `<div class="empty">No onboarding plan yet. <a href="#plan">Build one</a> first.</div>`; return; }
    if (want !== state.planId || !state.plan) await openPlan(want); else renderLearn();
  } catch (e) { $("#learnOut").innerHTML = `<div class="empty">Couldn't load your plans. ${esc(e.message)}</div>`; }
}
$("#planPick").onchange = e => e.target.value && openPlan(e.target.value);

async function openPlan(id) {
  try {
    const r = await call(`/api/plans/${id}`);
    Object.assign(state, { plan: r.plan, planId: id, progress: r.plan.progress, titles: r.playbook_titles || {}, openSteps: null });
    remember(id); renderLearn();
  } catch (e) { $("#learnOut").innerHTML = `<div class="empty">Couldn't open this plan. ${esc(e.message)}</div>`; }
}

const ST_ICON = { done: "✓", skipped: "↷", stuck: "?" };
function renderLearn(message, kind = "", heard = null) {
  state.lastHint = message && kind !== "good" ? message : null;
  const { plan, progress } = state; if (!plan) return;
  const s = progress.status, n = s.length, finished = s.filter(x => x === "done" || x === "skipped").length;
  const pct = n ? Math.round(100 * finished / n) : 100;
  const it = progress.current == null ? null : plan.items[progress.current];
  const opt = $("#planPick").selectedOptions[0];
  if (opt) opt.textContent = opt.textContent.replace(/\(\d+% done/, `(${pct}% done`);
  $("#learnOut").innerHTML = `
    <div class="card"><div class="cover">${ring(pct, "small")}<div><h2>Your plan as ${esc(plan.role)}</h2>
      <p class="muted small" style="margin:0">${finished} of ${n} steps finished, saved automatically</p></div></div></div>
    ${it ? `<div class="card now">
      <span class="small muted">Now, day ${it.day}: <span class="k ${esc(it.kind)}">${esc(it.kind)}</span></span>
      <h2 style="margin-top:4px">${esc(it.title)}</h2><p>${esc(it.detail)}</p>
      ${it.playbook_id ? `<button class="btn sm" data-action="toggle-steps">${state.openSteps ? "Hide" : "Show"} the playbook: ${esc(state.titles[it.playbook_id] || "steps")}</button>
        ${state.openSteps || ""}` : ""}
      <label for="note" style="margin-top:14px">Stuck? Say what's confusing <span class="opt">(optional)</span></label>
      <input type="text" id="note" maxlength="400" placeholder="e.g. XLOOKUP shows #N/A for every row">
      <div class="row" style="margin-top:10px">
        <button class="btn primary" data-outcome="done">Done, next step</button>
        <button class="btn" data-outcome="stuck">I'm stuck</button>
        <button class="btn ghost" data-outcome="already_know">I already know this</button></div>
      <div class="row voice-row">
        <button class="btn ghost" data-action="ask-voice" title="Say what you're stuck on, in English or Hindi">Ask by voice</button>
        <button class="btn ghost" data-action="coach" title="A voice conversation with a coach who knows this playbook and can see your screen">Talk to a live coach</button>
        <span class="small muted">Speak in English or Hindi</span></div>
      ${message ? `<div class="hint ${kind}">${state.lastHint ? `<button class="btn sm listen" data-action="listen">Listen</button>` : ""}${heard ? `<span class="heard">You asked: “${esc(heard)}”</span>` : ""}${esc(message)}</div>` : ""}</div>`
    : `<div class="card done-banner"><div class="check-big" aria-hidden="true">✓</div><h2>You've finished your onboarding plan</h2>
        <p class="muted">${esc(message || "Great work. Your manager can see your progress.")}</p>
        <button class="btn" data-restart="${esc(state.planId)}">Start this plan again</button></div>`}
    <div class="card status-list"><h2>Your plan</h2>${plan.items.map((x, i) => (i === 0 || plan.items[i - 1].day !== x.day ? `<h3 style="margin:12px 0 6px">Day ${x.day}</h3>` : "") +
      `<div class="pi ${s[i] || ""} ${i === progress.current ? "current" : ""}"><span class="st ${s[i] || ""}" aria-label="${s[i] || "to do"}">${ST_ICON[s[i]] || ""}</span>
        <div><span class="k ${esc(x.kind)}">${esc(x.kind)}</span><div><b>${esc(x.title)}</b></div><div class="small muted">${esc(x.detail)}</div></div></div>`).join("")}</div>`;
}

async function toggleSteps() {
  if (state.openSteps) { state.openSteps = null; return renderLearn(); }
  const pbId = state.plan.items[state.progress.current].playbook_id;
  try {
    const { playbook: p } = await call(`/api/playbooks/${pbId}`);
    state.openSteps = `<ol class="timeline" style="margin-top:14px">${p.steps.map(st => `<li><b>${esc(st.title)}</b><div>${esc(st.action)}</div><div class="why">Why: ${esc(st.why)}</div></li>`).join("")}</ol>
      <div class="lists"><div class="warn"><h3>Watch out for</h3><ul>${p.gotchas.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div>
      <div class="good"><h3>Done right when</h3><ul>${p.success_criteria.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div></div>`;
    renderLearn();
  } catch (e) { toast(e.message, "bad"); }
}

async function report(outcome, btn) {
  const note = $("#note")?.value || "";
  $$("#learnOut [data-outcome]").forEach(b => b.disabled = true);
  btn.textContent = outcome === "stuck" ? "Thinking…" : "Saving…";
  try {
    const r = await postJSON("/api/session", { plan_id: state.planId, item_index: state.progress.current, outcome, note });
    state.progress = r.progress;
    if (outcome !== "stuck") state.openSteps = null;
    renderLearn(r.message, outcome === "done" ? "good" : "");
  } catch (e) { toast(e.message, "bad"); renderLearn(); }
}


// ---------------- voice: listen, ask by voice, live coach ----------------
// Listen: a hint read aloud by Gemini text-to-speech (any language the hint is in).
async function speakText(text, btn) {
  if (state.audio) { state.audio.pause(); state.audio = null; $$("[data-action=listen]").forEach(b => b.textContent = "Listen"); return; }
  if (btn) btn.textContent = "Preparing audio…";
  try {
    const t = await token();
    const r = await fetch(API + "/api/speak", { method: "POST", body: JSON.stringify({ text }),
      headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) } });
    if (!r.ok) { const b = await r.json().catch(() => ({})); throw new ApiError(friendly(r.status, b.detail), r.status); }
    const url = URL.createObjectURL(await r.blob()), audio = new Audio(url);
    state.audio = audio;
    audio.onended = () => { state.audio = null; URL.revokeObjectURL(url); $$("[data-action=listen]").forEach(b => b.textContent = "Listen"); };
    $$("[data-action=listen]").forEach(b => b.textContent = "Stop");
    await audio.play();
  } catch (e) { state.audio = null; if (btn) btn.textContent = "Listen"; toast(e.message, "bad"); }
}

// Ask by voice: tap to start, tap again to send. Gemini understands the question and answers in the same language.
async function askByVoice(btn) {
  if (state.rec) return finishVoice();
  const chunks = [], started = Date.now();
  let loudest = 0;
  const mic = new MicCapture(c => { chunks.push(c); loudest = Math.max(loudest, mic.level); });
  try { await mic.start(); } catch { return toast("The microphone is blocked. Allow microphone access for this site and try again.", "bad", 7000); }
  state.rec = { mic, chunks, started, loudest: () => loudest, timer: setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000), b = $("[data-action=ask-voice]");
    if (b) { b.classList.add("recording"); b.textContent = `Stop and ask (${fmtTime(secs)})`; }
    if (secs >= 60) finishVoice();   // keep questions short
  }, 250) };
  btn.classList.add("recording"); btn.textContent = "Stop and ask (0:00)";
}
async function finishVoice() {
  const rec = state.rec; if (!rec) return;
  state.rec = null; clearInterval(rec.timer); rec.mic.stop();
  const b = $("[data-action=ask-voice]");
  if (b) { b.classList.remove("recording"); b.textContent = "Thinking…"; b.disabled = true; }
  if (Date.now() - rec.started < 700) { toast("That was very short. Tap, ask your question, then tap again."); return renderLearn(); }
  if (rec.loudest() < 0.02) {   // the microphone never picked up a voice: don't send silence
    toast("I couldn't hear anything. Check that the right microphone is selected and not muted.", "bad", 7000); return renderLearn();
  }
  const form = new FormData();
  form.append("plan_id", state.planId); form.append("item_index", state.progress.current);
  form.append("file", wavFromPcm16(rec.chunks), "question.wav");
  try {
    const r = await call("/api/session/voice", { method: "POST", body: form });
    state.progress = r.progress;
    renderLearn(r.message, "", r.heard);
    if (r.branch === "hint") speakText(r.message, $("[data-action=listen]"));   // answer out loud, since the question was spoken
  } catch (e) { toast(e.message, "bad"); renderLearn(); }
}

// Live coach: a voice conversation (Gemini Live API) grounded in this task's playbook, optionally seeing the screen.
function coachCaption(who, text) {
  const box = $("#coachCaptions");
  if (who === null) { state.coachLine = null; return; }
  if (who === "system") { box.insertAdjacentHTML("beforeend", `<p class="system">${esc(text)}</p>`); }
  else {
    if (!state.coachLine || state.coachLine.who !== who) {
      const p = document.createElement("p"); p.className = who === "you" ? "you" : "coach-line";
      p.innerHTML = `<b>${who === "you" ? "You" : "Coach"}:</b> <span></span>`;
      box.append(p); state.coachLine = { who, span: p.querySelector("span") };
    }
    state.coachLine.span.textContent += text;
  }
  box.scrollTop = box.scrollHeight;
}
function coachStatus(text, cls) { $("#coachStatus").textContent = text; $("#coachDock").className = `coach ${cls || ""}`; }

async function startCoach(btn) {
  if (state.coach) return;
  const dock = $("#coachDock");
  $("#coachCaptions").innerHTML = ""; state.coachLine = null;
  dock.hidden = false; document.body.classList.add("coach-open");
  $("[data-action=coach-screen]").hidden = !navigator.mediaDevices?.getDisplayMedia;   // phones can't share a screen from the browser
  coachStatus("Connecting…", "connecting"); btn.disabled = true;
  const coach = state.coach = new LiveCoach({ caption: coachCaption, ended: (reason) => endCoachUi(reason) });
  try {
    const tok = await postJSON("/api/coach/start", { plan_id: state.planId, item_index: state.progress.current });
    await coach.start(tok);
    state.coachTimer = setInterval(() => {
      if (!state.coach) return;
      $("#coachLevel").style.width = `${Math.min(100, Math.round(state.coach.level * 180))}%`;
      if (state.coach.speaking) coachStatus("Coach is speaking. Talk any time to interrupt.", "speaking");
      else coachStatus(state.coach.sharing ? "Listening, and watching your shared screen" : "Listening");
    }, 150);
  } catch (e) { toast(e.message, "bad", 7000); coach.stop(); }
  btn.disabled = false;
}
function endCoachUi(reason) {
  clearInterval(state.coachTimer); state.coach = null;
  $("#coachDock").hidden = true; document.body.classList.remove("coach-open");
  $("[data-action=coach-screen]").textContent = "Share my screen";
  if (reason) toast("The live coach session ended: " + reason);
}
async function toggleCoachScreen(btn) {
  const coach = state.coach; if (!coach) return;
  if (coach.sharing) { coach.stopScreen(); btn.textContent = "Share my screen"; return; }
  try { await coach.shareScreen(() => btn.textContent = "Share my screen"); btn.textContent = "Stop sharing"; }
  catch { toast("Screen sharing was cancelled."); }
}

// ---------------- system check ----------------
$$("[data-check]").forEach(b => b.onclick = async () => {
  const label = b.textContent; b.disabled = true; b.textContent = "Checking…";
  try { $("#statusOut").textContent = JSON.stringify(await call(b.dataset.check), null, 2); }
  catch (e) { $("#statusOut").textContent = "Check failed: " + e.message; }
  b.disabled = false; b.textContent = label;
});

document.addEventListener("change", (e) => {
  const i = e.target.dataset?.ytPick;
  if (i !== undefined) { state.yt.videos[+i].pick = e.target.checked; renderYouTube(); }
});

// ---------------- clicks on dynamic content ----------------
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, a"); if (!t) return;
  const d = t.dataset;
  if (d.action === "sign-in") signIn();
  else if (d.action === "sign-out") state.fb.signOut(state.auth);
  else if (d.action === "retry-upload") state.retry?.();
  else if (d.action === "close-playbook") $("#capOut").innerHTML = "";
  else if (d.action === "toggle-steps") toggleSteps();
  else if (d.action === "listen") speakText(state.lastHint, t);
  else if (d.action === "ask-voice") askByVoice(t);
  else if (d.action === "coach") startCoach(t);
  else if (d.action === "coach-end") state.coach?.stop();
  else if (d.action === "coach-screen") toggleCoachScreen(t);
  else if (d.viewPb) viewPlaybook(d.viewPb);
  else if (d.delPb) deletePlaybook(d.delPb, t);
  else if (d.copyGap) copyGap(+d.copyGap);
  else if (d.themeSet) {   // tapping the active mode (the only one visible on phones) moves to the next
    const modes = ["auto", "light", "dark"];
    setTheme(t.getAttribute("aria-pressed") === "true" ? modes[(modes.indexOf(d.themeSet) + 1) % 3] : d.themeSet);
  }
  else if (d.recheck) recheckPlan(d.recheck, t);
  else if (d.restart) restartPlan(d.restart, t);
  else if (d.ytImport !== undefined) importYouTube();
  else if (d.ytPlaylist !== undefined) findYouTube(true);
  else if (d.openPlan) { remember(d.openPlan); state.plan = null; }   // the #learn link then opens it
  else if (d.outcome) report(d.outcome, t);
  else if (d.seek) {
    const v = t.closest(".card").querySelector("video"); if (!v) return;
    const [m, s] = d.seek.split(":").map(Number); v.currentTime = (m || 0) * 60 + (s || 0); v.play();
  }
});

// The first route() runs from renderAuth() once sign-in state is known.
