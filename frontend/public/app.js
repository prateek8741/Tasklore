"use strict";
// Tasklore web app: the first client of the Tasklore API (backend/main.py).
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
    // Sign in through this site's own /__/auth helper (every Firebase Hosting site serves it), so Google's popup
    // names tasklore.web.app instead of the project's firebaseapp.com address.
    if (/\.(web\.app|firebaseapp\.com)$/.test(location.hostname)) config.authDomain = location.hostname;
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
  refreshMe();
}

// ---------------- roles: manager, expert, new hire (the API enforces them; the UI mirrors them) ----------------
const ROLE_NAME = { manager: "Manager", expert: "Expert", newhire: "New hire" };
const ROLE_HOME = { manager: "#plan", expert: "#capture", newhire: "#learn" };
const VIEW_ROLES = { capture: ["manager", "expert"], plan: ["manager"], knowledge: ["manager"], learn: ["manager", "newhire"], system: ["manager"] };
const ROLE_HINT = { manager: "builds plans, assigns them and asks experts to record gaps",
                    expert: "records tasks and answers recording requests", newhire: "sees only the plan assigned to them" };

async function refreshMe() {
  state.me = null;
  if (state.authOn && !state.user) return route();
  try { state.me = await call("/api/me"); }
  catch (e) { state.me = { noaccess: e.status === 403 ? e.message : "", role: null, roles: [] }; }
  applyRole(); route();
}

function applyRole() {
  const me = state.me || {}, role = me.role;
  $("#btnResetSample").hidden = !me.demo || role !== "manager";
  $$("#nav a[data-roles]").forEach(a => a.hidden = !role || !a.dataset.roles.split(" ").includes(role));
  $("#navLearn").textContent = role === "newhire" ? "My onboarding" : "New hire";
  const showBar = !!role && (me.demo || (me.roles || []).length > 1);
  $("#roleBar").hidden = !showBar;
  if (showBar) {
    $("#roleBarLabel").textContent = me.demo ? "Demo mode" : "Your roles";
    $$("[data-role-set]").forEach(b => { b.hidden = !me.roles.includes(b.dataset.roleSet); b.setAttribute("aria-pressed", String(b.dataset.roleSet === role)); });
    $("#roleHint").textContent = `${ROLE_NAME[role]}: ${ROLE_HINT[role]}`;
  }
  const cta = { manager: [["#plan", "Onboard a new hire", 1], ["#capture", "Record a task"]],
                expert: [["#capture", "See my recording requests", 1]], newhire: [["#learn", "Open my onboarding", 1]] }[role]
              || [["#plan", "Onboard a new hire", 1], ["#capture", "Record a task"]];
  $("#heroCta").innerHTML = cta.map(([href, label, primary]) => `<a class="btn ${primary ? "primary" : ""} lg" href="${href}">${label}</a>`).join("");
}


async function setRole(role) {
  try {
    state.me = { ...state.me, ...(await postJSON("/api/me/role", { role })) };
    Object.assign(state, { plan: null, planId: null, progress: null, lastPlan: null, lastPlanId: null, activeReq: null });
    $("#planOut").innerHTML = ""; $("#capOut").innerHTML = ""; $("#reqActive").innerHTML = "";
    applyRole();
    if (location.hash === ROLE_HOME[role]) route(); else location.hash = ROLE_HOME[role];
    toast(`Now viewing as ${ROLE_NAME[role]}.`);
  } catch (e) { toast(e.message, "bad"); }
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
  if (status === 429) return /times an hour/.test(d) ? d : "Gemini is busy right now. Wait a few seconds and try again.";
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return d || `Request failed (HTTP ${status}).`;
}
async function token() { await authReady; return state.user ? state.user.getIdToken() : null; }

async function call(path, opts = {}, base = API) {
  const headers = { ...(opts.headers || {}) };
  const t = await token(); if (t) headers.Authorization = "Bearer " + t;
  let r;
  try { r = await fetch(base + path, { ...opts, headers }); }
  catch { throw new ApiError("Can't reach Tasklore. Check your internet connection and try again.", 0); }
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
const VIEWS = ["home", "capture", "plan", "knowledge", "learn", "system"];
const ON_ENTER = { home: loadStats, capture: () => { loadLibrary(); loadRequests(); }, plan: () => { renderFlow(); loadPlans(); loadPeople(); },
                   knowledge: () => { loadKnowledge(); loadPeople(); }, learn: loadPlanPicker };
function route() {
  let v = location.hash.slice(1) || "home";
  if (!VIEWS.includes(v)) v = "home";
  const signedIn = !state.authOn || state.user;
  if (signedIn && !state.me) return;   // refreshMe() routes once roles are known
  const role = state.me?.role;
  if (signedIn && role && VIEW_ROLES[v] && !VIEW_ROLES[v].includes(role)) { location.hash = ROLE_HOME[role]; return; }
  const gated = v !== "home" && !signedIn;
  const noaccess = signedIn && state.me?.noaccess !== undefined;
  $$(".view").forEach(s => s.hidden = true);
  if (noaccess) { $("#noAccessMsg").textContent = state.me.noaccess || $("#noAccessMsg").textContent; $("#view-noaccess").hidden = false; return; }
  $(gated ? "#view-signin" : `#view-${v}`).hidden = false;
  $$("#nav a").forEach(a => { const on = a.dataset.view === v; a.classList.toggle("on", on); on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"); });
  if (!gated && (v === "home" ? state.user || !state.authOn : true)) ON_ENTER[v]?.();
}
window.addEventListener("hashchange", () => { route(); window.scrollTo(0, 0); });

// ---------------- home ----------------
async function loadStats() {
  if (state.me?.role !== "manager") { $("#stats").hidden = true; return; }
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
  if (state.activeReq) form.append("request_id", state.activeReq.id);
  try {
    const r = await upload("/api/capture", form, p => { pct = p; if (p >= 1 && stage === 0) { stage = 1; waitStart = Date.now(); } draw(); });
    clearInterval(timer);
    out.innerHTML = playbookCard(r.playbook, r.id, { preview: state.previewUrl, footer: `Saved to your team's library in ${Math.round(r.ms / 1000)} s` })
      + `<div class="card flat row" style="justify-content:space-between"><span>Did this fill a gap? Plans built earlier don't update by themselves.</span>
         <a class="btn sm" href="#plan">Re-check your plans</a></div>`;
    toast("Playbook created and added to the library.", "good");
    requestAnswered(r.request);
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
        body: JSON.stringify({ video_id: v.video_id, hint, request_id: state.activeReq?.id || "" }) }, DIRECT_API);
      requestAnswered(r.request);
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
           : p.source?.type === "sample" ? `Sample playbook by ${esc(p.created_by?.name || "the sample team")}, written for the demo (no video)`
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
    <ol class="timeline">${p.steps.map(s => `<li><b>${esc(s.title)}</b>${tsHtml(s)}${improvedMark(s)}
      <div>${esc(s.action)}</div><div class="why">Why: ${esc(s.why)}</div></li>`).join("")}</ol>
    ${lessonsHtml(p)}
    ${(p.skills || []).length ? `<p class="small muted" style="margin-top:12px">Skills: ${p.skills.map(esc).join(", ")}</p>` : ""}
    <p class="small muted">${[by, versionText(p), footer && esc(footer)].filter(Boolean).join(". ")}</p></div>`;
}

// What a playbook has learned from new hires is marked, so everyone can see it is a living document.
const improvedMark = (s) => s.improved ? ` <span class="chip good">Clarified from new hires' questions</span>` : "";
const versionText = (p) => p.version > 1 ? `Version ${p.version}, improved from ${p.learned_from} new-hire question${p.learned_from === 1 ? "" : "s"}` : "";
function lessonsHtml(p) {
  const learned = new Set(p.learned_gotchas || []);
  return `<div class="lists">
      <div class="warn"><h3>Watch out for</h3><ul>${p.gotchas.map(g => `<li>${esc(g)}${learned.has(g) ? ` <span class="chip brand">from new hires</span>` : ""}</li>`).join("")}</ul></div>
      <div class="good"><h3>Done right when</h3><ul>${p.success_criteria.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div>
    </div>${faqHtml(p.faq, "New hires asked")}`;
}
const faqHtml = (faq, title) => (faq || []).length ? `<div class="faq"><h3>${title}</h3>${faq.map(f =>
  `<details><summary>${esc(f.q)}</summary><p>${esc(f.a)}</p></details>`).join("")}</div>` : "";

async function loadLibrary() {
  if (!$("#library .lib-card")) $("#library").innerHTML = `<div class="skel block"></div><div class="skel block"></div>`;
  try {
    const { playbooks } = await call("/api/playbooks");
    renderImproveBox(playbooks);
    $("#libCount").textContent = playbooks.length ? `${playbooks.length} recorded` : "";
    $("#library").innerHTML = playbooks.length ? playbooks.map(p => `<div class="card lib-card">
        <h3>${esc(p.title)}</h3>
        <span class="small muted">${esc(p.role_hint)}, ${p.steps.length} steps${p.estimated_minutes ? `, about ${esc(p.estimated_minutes)} min` : ""}</span>
        <div>${(p.skills || []).slice(0, 4).map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>
        ${p.version > 1 || p.signals?.questions ? `<div>${p.version > 1 ? `<span class="chip good">Version ${p.version}, learned from new hires</span>` : ""}${p.signals?.questions ? `<span class="chip warn">${p.signals.questions} new question${p.signals.questions === 1 ? "" : "s"}</span>` : ""}</div>` : ""}
        ${p.source?.type === "youtube" ? `<span class="small muted">From YouTube, ${esc(p.source.channel)}</span>`
          : p.created_by?.name ? `<span class="small muted">by ${esc(p.created_by.name)}</span>` : ""}
        <div class="actions"><button class="btn sm" data-view-pb="${esc(p.id)}">View</button>
          ${p.sample ? `<span class="chip">Sample</span>` : `<button class="btn sm ghost" data-del-pb="${esc(p.id)}" aria-label="Delete ${esc(p.title)}">Delete</button>`}</div></div>`).join("")
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

// ---------------- self-improving playbooks ----------------
// Every "I'm stuck" on a step goes back to the playbook it uses. The recorder (or a manager) asks Gemini what to
// change, ticks the suggestions worth keeping, and the playbook becomes a new version that hints and the coach use.
const canEdit = (p) => state.me?.role === "manager" || (p.created_by?.uid && p.created_by.uid === state.me?.uid);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const KIND_LABEL = { gotcha: "New gotcha", step: "Clearer step", faq: "New FAQ answer" };
state.sug = {};

function renderImproveBox(playbooks) {
  const list = playbooks.filter(p => p.signals?.questions);
  if (!list.length) { $("#improveBox").innerHTML = ""; return; }
  $("#improveBox").innerHTML = `<div class="card"><h2>Learn from your new hires</h2>
    <p class="muted small">New hires got stuck on these playbooks. Gemini reads their questions and suggests what to add; you approve each change.
      The hints and the live coach use the new version straight away.</p>
    ${list.map(p => { const g = p.signals;
      return `<div class="sig-row" id="sig-${esc(p.id)}"><div class="grow"><b>${esc(p.title)}</b>
        <div class="small muted">${plural(g.questions, "question")} from ${plural(g.people, "new hire")}${g.coach ? `, ${plural(g.coach, "live coach session")}` : ""} since ${p.version > 1 ? `version ${p.version}` : "it was recorded"}</div>
        ${g.last_note ? `<div class="small quote">“${esc(g.last_note)}”</div>` : ""}</div>
        ${canEdit(p) ? `<button class="btn sm primary" data-improve="${esc(p.id)}">${state.sug[p.id] ? "Ask again" : "Improve with Gemini"}</button>`
                     : `<span class="small muted">Only ${esc(p.created_by?.name || "the recorder")} or a manager can change it</span>`}
        <div class="sug-out" id="sug-${esc(p.id)}"></div></div>`; }).join("")}</div>`;
  Object.keys(state.sug).forEach(renderSuggestions);
  if (state.improveTarget) {   // arrived from the knowledge map's "Review" button
    const row = $(`#sig-${CSS.escape(state.improveTarget)}`); state.improveTarget = null;
    if (row) { row.scrollIntoView({ behavior: "smooth", block: "center" }); row.classList.add("flash"); }
  }
}

async function openImprove(id, btn) {
  btn.disabled = true; btn.textContent = "Gemini is reading the questions…";
  try {
    const r = await call(`/api/playbooks/${id}/suggestions`, { method: "POST" });
    state.sug[id] = { ...r, picked: r.suggestions.map(() => true) };
    btn.textContent = "Ask again"; renderSuggestions(id);
  } catch (e) { toast(e.message, "bad"); btn.textContent = "Improve with Gemini"; }
  btn.disabled = false;
}

function renderSuggestions(id) {
  const d = state.sug[id], el = $(`#sug-${CSS.escape(id)}`); if (!d || !el) return;
  const n = d.picked.filter(Boolean).length;
  el.innerHTML = `<div class="sug-panel"><p style="margin:0 0 6px"><b>What new hires struggle with:</b> ${esc(d.summary)}</p>
    <details class="how"><summary>The ${plural(d.questions.length, "question")} Gemini read</summary><ol class="qlist">${d.questions.map(q =>
      `<li>${q.note ? `“${esc(q.note)}”` : `<span class="muted">(pressed I'm stuck without a note)</span>`}
        <span class="small muted">on “${esc(q.step)}”${q.spoken ? ", spoken" : ""}${q.resolved ? ", finished the step after the hint" : ""}</span></li>`).join("")}</ol>
      <p class="small muted" style="margin:0">Shown without names: you see what was hard, not who found it hard.</p></details>
    ${d.suggestions.length ? `<p class="small muted" style="margin:8px 0 0">Tick what to keep. You know the team best: click any text to correct it before saving.</p>`
      + d.suggestions.map((x, i) => `<div class="sug ${d.picked[i] ? "on" : ""}">
      <label class="tick"><input type="checkbox" data-sug-pick="${esc(id)}:${i}" ${d.picked[i] ? "checked" : ""} aria-label="Keep this change"></label>
      <div><span class="chip ${x.kind === "gotcha" ? "warn" : x.kind === "faq" ? "brand" : "good"}">${KIND_LABEL[x.kind]}${x.kind === "step" ? `, step ${x.step_index + 1}` : ""}</span>
        ${x.kind === "step" ? `<div class="small muted was">${esc(x.current)}</div>` : ""}
        <div class="edit ${x.kind === "faq" ? "q" : ""}" contenteditable="plaintext-only" spellcheck="true" data-sug-edit="${esc(id)}:${i}:text">${esc(x.text)}</div>
        ${x.kind === "faq" ? `<div class="edit small" contenteditable="plaintext-only" spellcheck="true" data-sug-edit="${esc(id)}:${i}:answer">${esc(x.answer)}</div>` : ""}
        <div class="small muted">${esc(x.reason.trim().replace(/([^.!?])$/, "$1."))}${x.evidence.length ? ` From question${x.evidence.length === 1 ? "" : "s"} ${x.evidence.map(e => e + 1).join(", ")}.` : ""}</div></div></div>`).join("")
      : `<p class="small muted">Gemini found nothing worth changing in these questions.</p>`}
    <div class="row" style="margin-top:10px">
      ${d.suggestions.length ? `<button class="btn sm primary" data-sug-apply="${esc(id)}" ${n ? "" : "disabled"}>Save ${plural(n, "change")} as version ${(d.version || 1) + 1}</button>` : ""}
      <button class="btn sm ghost" data-sug-dismiss="${esc(id)}">Mark as reviewed</button></div></div>`;
}

async function applyImprove(id, btn, dismiss = false) {
  const d = state.sug[id], chosen = dismiss ? [] : d.suggestions.filter((_, i) => d.picked[i]);
  btn.disabled = true;
  try {
    const r = await postJSON(`/api/playbooks/${id}/improve`, { suggestions: chosen, based_on: d.questions.length });
    delete state.sug[id];
    if (r.applied) {
      toast(`Saved as version ${r.version}. Hints and the live coach use it now.`, "good", 7000);
      $("#capOut").innerHTML = playbookCard(r.playbook, id, { closable: true });
      $("#capOut").scrollIntoView({ behavior: "smooth" });
    } else toast("Marked as reviewed. New questions will show up here again.");
    loadLibrary();
  } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
}

// ---------------- knowledge map ----------------
// Built from each role's core tasks (the newest plan per role): recorded, requested, or only in someone's head.
const KM_STATUS = { recorded: "Recorded", requested: "Recording requested", declined: "Declined, not recorded", missing: "Only in someone's head" };

async function loadKnowledge() {
  try { state.km = await call("/api/knowledge"); renderKnowledge(); }
  catch (e) { $("#kmOut").innerHTML = `<div class="empty">Couldn't load the knowledge map. ${esc(e.message)}</div>`; }
}

function renderKnowledge() {
  const m = state.km, t = m.team;
  if (!m.roles.length) {
    $("#kmOut").innerHTML = `<div class="empty">No map yet. <a href="#plan">Build an onboarding plan</a> first: the map is made from each role's core tasks.</div>`;
    return;
  }
  $("#kmOut").innerHTML = `
    <div class="stats">${[[t.coverage_percent + "%", "of the team's core know-how is recorded"], [t.critical_missing, "critical tasks only in someone's head"],
      [t.holders, "people or roles holding unrecorded know-how"], [t.playbooks, "playbooks recorded"]]
      .map(([n, l], i) => `<div class="stat ${i === 1 && n ? "alarm" : ""}"><b>${n}</b><span>${l}</span></div>`).join("")}</div>
    <div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Risk briefing</h2>
        <span class="row"><button class="btn sm" data-km-listen hidden>Listen</button><button class="btn sm primary" data-km-brief>Brief me with Gemini</button></span></div>
      <p class="muted small" id="kmBrief" style="margin:8px 0 0">Gemini reads this map and tells you what to record first, and from whom.</p></div>
    <div class="grid2">
      <div class="card"><h2>Only in people's heads</h2>
        <p class="muted small">Know-how nobody has recorded, grouped by who has it today, critical first. If they are away or leave, nobody can teach it. Ask them to record it once.</p>
        ${m.holders.map(holderHtml).join("") || `<div class="empty">Nothing. Every core task is recorded.</div>`}</div>
      <div class="card"><h2>Coverage by role</h2>
        <p class="muted small">Each square is one core task of the role. Tap a square for details.</p>
        ${m.roles.map((r, ri) => `<div class="km-role"><div class="row" style="justify-content:space-between"><b>${esc(r.role)}</b><span class="small muted">${r.coverage_percent}% recorded</span></div>
          <div class="bar thin"><div style="width:${r.coverage_percent}%"></div></div>
          <div class="km-cells">${r.tasks.map((x, ti) => `<button class="cell ${x.status}${x.importance === 3 ? " crit" : ""}" data-km-cell="${ri}:${ti}"
            title="${esc(x.task)}: ${KM_STATUS[x.status]}" aria-label="${esc(x.task)}, ${IMPORTANCE[x.importance]}, ${KM_STATUS[x.status]}"></button>`).join("")}</div>
          <div class="km-detail small" id="km-detail-${ri}"></div>
          <p class="small muted" style="margin:6px 0 0">From the newest of ${plural(r.plans, "plan")} for this role${r.assigned_to ? `, assigned to ${esc(r.assigned_to)}` : ""}.</p></div>`).join("")}
        <div class="km-legend small muted"><span><i class="recorded"></i>Recorded</span><span><i class="requested"></i>Requested</span>
          <span><i class="missing"></i>Not recorded</span><span><i class="missing crit"></i>Critical, not recorded</span></div></div>
    </div>
    ${m.struggling.length ? `<div class="card"><h2>Recorded, but new hires struggle</h2>
      <p class="muted small">Playbooks new hires keep getting stuck on since they were last reviewed. The recorder can improve them with Gemini on the Library page.</p>
      ${m.struggling.map(g => `<div class="sig-row"><div class="grow"><b>${esc(g.title)}</b>
        <div class="small muted">${plural(g.questions, "question")} from ${plural(g.people, "new hire")}${g.coach ? `, ${plural(g.coach, "live coach session")}` : ""}</div>
        ${g.last_note ? `<div class="small quote">“${esc(g.last_note)}”</div>` : ""}</div>
        <a class="btn sm" href="#capture" data-goto-improve="${esc(g.playbook_id)}">Review</a></div>`).join("")}</div>` : ""}
    <div class="card"><h2>Who shares know-how</h2>
      <p class="muted small">A recording keeps teaching after the senior has moved on to other work.</p>
      ${m.contributors.map(c => `<div class="person"><span class="avatar" aria-hidden="true">${esc((c.name || "?")[0].toUpperCase())}</span>
        <div class="grow"><b>${esc(c.name)}</b><div class="small muted">${plural(c.playbooks, "playbook")}, used in ${plural(c.plans, "plan")}${c.improved ? `, ${c.improved} improved from new hires' questions` : ""}</div></div>
        ${c.new_hires ? `<span class="chip good">Helped onboard ${plural(c.new_hires, "new hire")}</span>` : ""}</div>`).join("") || `<div class="empty">No playbooks yet.</div>`}</div>`;
}

function holderHtml(h, hi) {
  return `<div class="holder"><div class="row" style="justify-content:space-between"><b>${esc(h.holder)}</b>
      ${h.critical ? `<span class="chip bad">${h.critical} critical</span>` : ""}</div>
    <div class="small muted">${plural(h.tasks.length, "task")} only ${h.is_person ? "they" : "this role"} can teach today${h.is_person ? "" : ". Role suggested by Gemini"}</div>
    ${h.tasks.map((x, ti) => `<div class="htask"><div class="grow"><div>${esc(x.task)}</div>
        <span class="small muted">${IMPORTANCE[x.importance]}, ${esc(x.role)}, needed by day ${esc(x.needed_by_day)}</span></div>
      ${x.status === "requested" ? `<span class="chip brand">Requested from ${esc(x.request.expert_email)}</span>`
        : `${x.status === "declined" ? `<span class="chip warn" title="${esc(x.request?.decline_reason || "")}">Declined by ${esc(x.request?.expert_email)}</span>` : ""}
           <button class="btn sm" data-km-ask="${hi}:${ti}">Ask to record</button>`}</div>
      <div id="km-ask-${hi}-${ti}"></div>`).join("")}</div>`;
}

function kmCell(ri, ti, btn) {
  const x = state.km.roles[ri].tasks[ti], box = $(`#km-detail-${ri}`);
  const was = btn.classList.contains("sel");
  $$(`#kmOut .cell.sel`).forEach(c => c.classList.remove("sel"));
  $$(`#kmOut .km-detail`).forEach(d => d.innerHTML = "");
  if (was) return;
  btn.classList.add("sel");
  box.innerHTML = `<b>${esc(x.task)}</b> <span class="chip ${x.importance === 3 ? "bad" : ""}">${IMPORTANCE[x.importance]}</span><br>` + (
    x.status === "recorded" ? `Recorded${x.recorded_by ? ` by ${esc(x.recorded_by)}` : ""} as “${esc(x.playbook_title)}”.`
    : x.status === "requested" ? `Recording requested from ${esc(x.request.expert_email)}. Needed by day ${esc(x.needed_by_day)}.`
    : `Only ${esc(x.holder)} can teach it today. Needed by day ${esc(x.needed_by_day)}.${x.status === "declined" ? ` ${esc(x.request?.expert_email)} declined${x.request?.decline_reason ? `: “${esc(x.request.decline_reason)}”` : ""}.` : ""}`);
}

function kmAskForm(hi, ti) {
  const h = state.km.holders[hi], x = h.tasks[ti], box = $(`#km-ask-${hi}-${ti}`);
  if (box.innerHTML) { box.innerHTML = ""; return; }
  const guess = h.is_person && /@/.test(h.holder) ? h.holder : "";
  box.innerHTML = `<div class="row" style="margin:8px 0 4px">
    <input type="text" id="kmEmail-${hi}-${ti}" list="expertList" inputmode="email" placeholder="${esc(h.is_person ? "expert@company.com" : `Email of your ${h.holder}`)}" value="${esc(guess)}" style="flex:1; min-width:200px" aria-label="Expert's email">
    <button class="btn sm primary" data-km-send="${hi}:${ti}">Send request</button></div>`;
  $(`#kmEmail-${hi}-${ti}`).focus();
}

async function kmSend(hi, ti, btn) {
  const x = state.km.holders[hi].tasks[ti], email = $(`#kmEmail-${hi}-${ti}`).value.trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return toast("Enter the expert's email address.", "bad");
  btn.disabled = true;
  try {
    await postJSON("/api/requests", { plan_id: x.plan_id, topic: x.task, expert_email: email });
    toast(`Asked ${email} to record “${x.task}”. They'll see it under Library.`, "good", 6000);
    loadKnowledge(); loadPeople();
  } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
}

async function kmBrief(btn) {
  btn.disabled = true; btn.textContent = "Gemini is reading the map…";
  try {
    const r = await call("/api/knowledge/briefing", { method: "POST" });
    state.kmBrief = r.text;
    const p = $("#kmBrief"); p.textContent = r.text; p.className = "brief-text";
    $("[data-km-listen]").hidden = false; btn.textContent = "Brief me again";
  } catch (e) { toast(e.message, "bad"); btn.textContent = "Brief me with Gemini"; }
  btn.disabled = false;
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

// The Onboard page walks a manager through three steps. They guide, they don't block: assigning before every gap
// is filled is fine, the new hire then shadows someone for what isn't recorded.
const FLOW_TARGET = ["plan-role", "plan-gaps", "plan-assign"];
function renderFlow() {
  const p = state.lastPlan, gaps = p?.gaps || [];
  const handled = gaps.filter(g => gapRequest(g)?.status === "open").length;
  const done = [!!p, !!p && handled === gaps.length, !!p?.assigned_to];
  const active = done.indexOf(false);
  const sub = [p ? `${p.coverage_percent}% already recorded` : "Gemini finds what's missing",
               !p ? "Add videos or ask an expert" : !gaps.length ? "Nothing missing" : `${gaps.length} missing${handled ? `, ${handled} asked` : ""}`,
               p?.assigned_to ? `To ${p.assigned_to}` : "Give the plan to the new hire"];
  $("#flow").innerHTML = ["Describe the role", "Fill the gaps", "Assign it"].map((t, i) => `<li class="${done[i] ? "done" : i === active ? "active" : ""}">
    <button data-flow="${FLOW_TARGET[i]}" ${!p && i ? "disabled" : ""} ${i === active ? `aria-current="step"` : ""}>
      <span class="n" aria-hidden="true">${done[i] ? "✓" : i + 1}</span><span><b>${t}</b><span class="small">${esc(sub[i])}</span></span></button></li>`).join("");
}

function renderPlan(plan, id, requests = [], { scroll = true } = {}) {
  state.planRequests = requests;
  const tasks = plan.role_tasks || [], covered = tasks.filter(t => t.playbook_id).length;
  const total = tasks.reduce((s, t) => s + t.importance, 0), got = tasks.filter(t => t.playbook_id).reduce((s, t) => s + t.importance, 0);
  const byDay = {}; plan.items.forEach(it => (byDay[it.day] ||= []).push(it));
  const days = Object.keys(byDay).length;
  $("#planOut").innerHTML = `
    <div class="card"><div class="cover">${ring(plan.coverage_percent)}
      <div style="flex:1; min-width:240px"><h2>${esc(plan.role)}: ${plan.coverage_percent}% of this job is already recorded</h2>
        <p class="muted">${covered} of ${tasks.length} core tasks have a playbook your team recorded.${plan.gaps.length ? " Fill the rest below before you assign the plan." : ""}</p>
        <details class="how"><summary>How is this % calculated?</summary>
          <p class="small muted">Each core task gets points by importance: Critical 3, Regular 2, Occasional 1. Coverage is the share of points whose task has a playbook.</p>
          <table><thead><tr><th>Core task</th><th>Importance</th><th>Points</th><th>Recorded?</th></tr></thead><tbody>
          ${tasks.map(t => `<tr><td>${esc(t.task)}</td><td>${IMPORTANCE[t.importance] || ""}</td><td>${t.importance}</td><td>${t.playbook_id ? "Yes" : "No"}</td></tr>`).join("")}
          </tbody><tfoot><tr><td colspan="4">${got} recorded points out of ${total} = <b>${plan.coverage_percent}%</b></td></tr></tfoot></table></details>
        <p class="small muted">Plan summary: ${esc(plan.summary)}</p></div></div></div>
    <div class="card" id="plan-gaps"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Fill the gaps (${plan.gaps.length})</h2>
        <span id="agentBtn">${agentButtonHtml(id, plan.gaps.length)}</span></div>
      <p class="muted small" style="margin-top:8px">Tasks this job needs that nobody has recorded. Add a video you already have and the gap is filled in about a minute,
        or ask the expert who knows it: they see the request when they sign in, and recording it fills the gap.
        ${plan.gaps.length ? "Or let the agent ask the right people for every gap at once." : ""}</p>
      <div id="agentBox">${agentPanelHtml(id)}</div>
      ${plan.gaps.map((g, i) => `<div class="gap"><b>${esc(g.topic)}</b><div class="small">${esc(g.why_needed)}</div>
        <div class="foot"><span class="small">Best recorded by <b>${esc(g.suggested_recorder)}</b>, needed by day ${esc(g.needed_by_day)} ${g.importance === 3 ? `<span class="chip bad">Critical</span>` : ""}</span>
        <span class="row">${gapRequestHtml(g, i, id)}</span></div><div id="ask-${i}">${fillStatusHtml(id, g.topic)}</div></div>`).join("")
        || `<div class="empty">No gaps. Your library covers this whole role.</div>`}</div>
    <div class="card" id="plan-assign"><h2>Assign it</h2>
      <p class="muted small">The new hire sees only the plan assigned to them.${plan.gaps.length ? ` Anything still missing becomes a session shadowing a colleague, so you can assign now and fill gaps later.` : ""}</p>
      ${assignHtml(plan, id)}
      <a class="btn" href="#learn" data-open-plan="${esc(id)}">Preview as the new hire</a></div>
    <div class="section-head"><h2>Plan details</h2></div>
    <div class="card"><h2>Core tasks of the role</h2>
      <p class="muted small">What this job involves, and which parts your team has already recorded. ✓ means a playbook teaches it; ! means nobody has recorded it yet.</p>
      ${tasks.map(t => `<div class="task"><span class="mark ${t.playbook_id ? "yes" : "no"}" aria-label="${t.playbook_id ? "covered" : "gap"}">${t.playbook_id ? "✓" : "!"}</span>
        <div style="flex:1"><div>${esc(t.task)}</div><span class="small muted">${t.playbook_id ? "Covered by “" + esc(state.titles[t.playbook_id] || "a playbook") + "”" : "Not recorded yet"}</span></div>
        <span class="chip ${t.importance === 3 ? "bad" : ""}">${IMPORTANCE[t.importance] || ""}</span></div>`).join("")}</div>
    <div class="card"><h2>Day-by-day plan</h2>
      <p class="muted small">The new hire's timetable for their first ${days} day${days === 1 ? "" : "s"}.
        ${Object.entries(KIND_HELP).map(([k, v]) => `<span class="k ${k}">${k[0].toUpperCase() + k.slice(1)}</span>: ${v}`).join(". ")}.</p>
      <div class="days">${Object.entries(byDay).map(([d, items]) => `<div class="day"><h3>Day ${d}</h3>${items.map(it => `<div class="pi">
        <span class="k ${esc(it.kind)}">${esc(it.kind)}</span><div><b>${esc(it.title)}</b></div><div class="small muted">${esc(it.detail)}</div>
        ${it.playbook_id ? `<div class="small" style="margin-top:4px">Playbook: ${esc(state.titles[it.playbook_id] || "recorded task")}</div>` : ""}</div>`).join("")}</div>`).join("")}</div></div>`;
  state.lastPlan = plan; state.lastPlanId = id;
  renderFlow();
  loadLastAgentRun(id);
  if (scroll) $("#planOut").scrollIntoView({ behavior: "smooth" });
}

// ---------------- gap-closing agent (Google ADK, on the API) ----------------
// One click: the agent reads the plan's gaps, picks who on the team has recorded the closest work, asks them and
// re-checks coverage. Every name and number shown is read back from Tasklore's database, not from the model's words.
state.agent = {};   // planId -> { busy, started, result, at, error }
const AGENT_STEP = { get_knowledge_gaps: "Read the gaps", get_task_experts: "Looked for experts",
                     request_task_recording: "Asked an expert", recheck_role_coverage: "Re-checked coverage" };
const AGENT_STATUS = { completed: ["good", "Done"], no_gaps: ["good", "Nothing to do"], stopped: ["warn", "Stopped early"], failed: ["bad", "Didn't run"] };
const AGENT_OPEN = { declined: "declined; nobody else asked yet", recorded_not_covering: "a recording came in but didn't cover it: review it",
                     not_requested: "nobody on the team has recorded close work" };

function agentButtonHtml(planId, gaps) {
  if (!gaps) return "";
  const busy = state.agent[planId]?.busy;
  return `<button class="btn sm primary" data-agent-run="${esc(planId)}" ${busy ? "disabled" : ""}
    title="The agent asks the right expert to record each gap, then re-checks coverage">${busy ? "Agent working…" : "Close gaps with the agent"}</button>`;
}

function agentPanelHtml(planId) {
  const a = state.agent[planId];
  if (!a) return "";
  if (a.busy) return `<div class="agent-panel busy" role="status" aria-busy="true"><span class="spin" aria-hidden="true"></span>
    <span class="small">The agent is reading the gaps, picking experts from what your team has recorded, asking them and re-checking coverage (${Math.round((Date.now() - a.started) / 1000)} s)</span></div>`;
  if (a.error) return `<div class="agent-panel"><p class="field-note" style="margin:0">${esc(a.error)}</p></div>`;
  const r = a.result, [cls, label] = AGENT_STATUS[r.status] || ["", r.status];
  const cov = r.coverage_after == null ? `Coverage ${r.coverage_before}% (not re-checked)`
            : r.coverage_after === r.coverage_before ? `Coverage stays ${r.coverage_after}%` : `Coverage ${r.coverage_before}% to ${r.coverage_after}%`;
  const group = (title, items, line) => items.length ? `<h3 class="agent-h small">${title}</h3><ul class="agent-list">${items.map(line).join("")}</ul>` : "";
  return `<div class="agent-panel">
    <div class="row" style="justify-content:space-between"><span class="row"><span class="chip ${cls}">${label}</span><b class="small">${cov}</b></span>
      <span class="small muted">${a.at ? `${fmtDate(a.at)}, ` : ""}${plural(r.model_calls, "Gemini call")}, ${(r.ms / 1000).toFixed(1)} s</span></div>
    ${r.stopped_reason ? `<p class="small" style="margin:8px 0 0">${esc(r.stopped_reason)}</p>` : ""}
    <p class="agent-summary">${esc(r.summary)}</p>
    ${group("Asked now", r.requests_created, c => `<li><b>${esc(c.topic)}</b> <span class="small muted">asked ${esc(c.expert_email)}</span></li>`)}
    ${group("Already asked", r.already_requested, c => `<li><b>${esc(c.topic)}</b> <span class="small muted">with ${esc(c.expert_email)}</span></li>`)}
    ${group("Still open", r.unresolved, u => `<li><b>${esc(u.topic)}</b> <span class="small muted">${AGENT_OPEN[u.state] || ""}</span></li>`)}
    ${r.next_steps.length ? `<details class="how"><summary>Next steps (${r.next_steps.length})</summary>
      <ul class="agent-list small">${r.next_steps.map(s => `<li>${esc(s)}</li>`).join("")}</ul></details>` : ""}
    <details class="how"><summary>What the agent did (${plural(r.trace.length, "step")})</summary>
      <ol class="agent-trace small">${r.trace.map(t => `<li><span class="${t.ok ? "ok" : "no"}" aria-label="${t.ok ? "done" : "refused or failed"}">${t.ok ? "✓" : "!"}</span>
        <b>${esc(AGENT_STEP[t.tool] || t.tool)}</b> <span class="muted">${esc(t.note)}</span></li>`).join("")}</ol>
      <p class="small muted" style="margin:0">Requests, people and coverage are read back from Tasklore after the run.
        ${r.summary_by === "agent" ? "The summary is Gemini's words, checked and with personal data masked." : "The summary was written by Tasklore."}</p></details></div>`;
}

function drawAgent(planId) {
  if (state.lastPlanId !== planId) return;
  if ($("#agentBox")) $("#agentBox").innerHTML = agentPanelHtml(planId);
  if ($("#agentBtn")) $("#agentBtn").innerHTML = agentButtonHtml(planId, state.lastPlan.gaps.length);
}

async function runAgent(planId) {
  if (state.agent[planId]?.busy) return;
  const a = state.agent[planId] = { busy: true, started: Date.now() };
  drawAgent(planId);
  const timer = setInterval(() => drawAgent(planId), 1000);
  try {
    // Straight to Cloud Run, like video uploads: a run can take longer than Firebase Hosting's 60 s.
    const r = await call("/api/agent/close-gaps", { method: "POST", headers: { "Content-Type": "application/json" },
                                                     body: JSON.stringify({ plan_id: planId }) }, DIRECT_API);
    Object.assign(a, { busy: false, result: r, at: Date.now() / 1000 });
    const asked = r.requests_created.length;
    toast(r.status === "failed" ? r.stopped_reason
          : asked ? `The agent asked ${plural(asked, "expert")}. Coverage goes up when they record.`
          : r.status === "no_gaps" ? "Nothing to do: this plan has no gaps." : "The agent didn't ask anyone new. See why on the plan.",
          r.status === "failed" ? "bad" : "good", 7000);
    if (state.lastPlanId === planId) await viewPlan(planId, { scroll: false });   // gap cards now show who was asked
    loadPlans(); loadPeople();
  } catch (e) { Object.assign(a, { busy: false, error: e.message }); }
  clearInterval(timer);
  drawAgent(planId);
}

async function loadLastAgentRun(planId) {   // re-opening a plan shows what the agent last did on it
  if (state.agent[planId] || !state.lastPlan?.gaps) return;
  try {
    const { runs } = await call(`/api/agent/runs?plan_id=${encodeURIComponent(planId)}`);
    const last = runs.find(x => x.summary);
    if (last && !state.agent[planId]) { state.agent[planId] = { result: last, at: last.finished_at || last.started_at }; drawAgent(planId); }
  } catch {}
}

// ---------------- fill a gap from the plan: upload a video, a YouTube link, or a playbook already in the library ----------------
// The video becomes a playbook in the library (like the Library page) and is linked straight to this gap.
// Progress lives in state, keyed by plan and gap, so re-rendering the plan doesn't lose a running upload.
state.fills = {};
const fillKey = (planId, topic) => `${planId}|${topic}`;
const gapIndex = (topic) => (state.lastPlan?.gaps || []).findIndex(g => g.topic === topic);

function fillStatusHtml(planId, topic) {
  const f = state.fills[fillKey(planId, topic)]; if (!f) return "";
  if (f.error) return `<p class="field-note">${esc(f.error)}</p>`;
  const secs = Math.round((Date.now() - f.started) / 1000);
  const text = f.stage === 0 ? `Uploading ${f.mb} MB, ${Math.round(f.pct * 100)}%`
             : f.stage === 1 ? `Gemini is watching the video and writing the playbook (${secs} s)` : "Linking the playbook…";
  return `<div class="fill-status" role="status"><span class="spin" aria-hidden="true"></span><span class="small">${text}</span></div>
    ${f.stage === 0 ? `<div class="bar thin"><div style="width:${Math.round(f.pct * 100)}%"></div></div>` : ""}`;
}
function drawFill(planId, topic) {
  const i = gapIndex(topic);
  if (state.lastPlanId === planId && i >= 0) $(`#ask-${i}`).innerHTML = fillStatusHtml(planId, topic);
}

async function openFill(i) {
  const box = $(`#ask-${i}`);
  delete state.fills[fillKey(state.lastPlanId, state.lastPlan.gaps[i].topic)];   // clears a failed try
  box.innerHTML = `<div class="fill"><div class="row">
      <button class="btn sm primary" data-fill-file="${i}">Upload a video</button>
      <input type="text" id="fillYt-${i}" inputmode="url" autocomplete="off" placeholder="or paste a YouTube link" style="flex:1; min-width:180px" aria-label="YouTube link">
      <button class="btn sm" data-fill-yt="${i}">Add</button></div>
    <div class="row" id="fillLibRow-${i}" style="margin-top:8px"></div>
    <p class="small muted" style="margin:6px 0 0">Gemini turns it into a playbook in 15 to 60 seconds, adds it to the library and fills this gap. MP4 or WebM, up to ${MAX_MB} MB.</p></div>`;
  try {   // a playbook already in the library that the plan's matching missed
    const { playbooks } = await call("/api/playbooks");
    if (playbooks.length && $(`#fillLibRow-${i}`)) $(`#fillLibRow-${i}`).innerHTML = `
      <select id="fillLib-${i}" style="flex:1; min-width:180px" aria-label="Playbook from the library"><option value="">Already in the library? Pick a playbook</option>
        ${playbooks.map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}</select>
      <button class="btn sm" data-fill-link="${i}">Use it</button>`;
  } catch {}
}

async function fillRun(i, info, send) {
  const planId = state.lastPlanId, topic = state.lastPlan.gaps[i].topic, key = fillKey(planId, topic);
  if (state.fills[key] && !state.fills[key].error) return;
  const f = state.fills[key] = { stage: 0, pct: 0, started: Date.now(), ...info };
  const redraw = () => state.lastPlanId === planId && renderPlan(state.lastPlan, planId, state.planRequests, { scroll: false });
  redraw();   // the gap's buttons give way to the progress line
  const timer = setInterval(() => f.stage === 1 && drawFill(planId, topic), 1000);
  try {
    const r = await send({ plan_id: planId, gap: topic }, f, () => drawFill(planId, topic));
    delete state.fills[key];
    const done = r.filled || r;
    if (state.lastPlanId === planId) {
      state.titles = done.playbook_titles || state.titles;
      renderPlan(done.plan, planId, state.planRequests, { scroll: false });
    }
    toast(`“${topic}” is filled. Coverage went from ${done.coverage_before}% to ${done.coverage_after}%.`, "good", 8000);
    loadPlans();
  } catch (e) { f.error = e.message; redraw(); }
  clearInterval(timer);
}

function fillUpload(i, file) {
  const mb = (file.size / 1048576).toFixed(1);
  if (!file.type.startsWith("video/") && !/\.(mp4|webm|mov|m4v)$/i.test(file.name)) return toast("Please choose a video file (MP4 or WebM).", "bad");
  if (file.size > MAX_MB * 1048576) return toast(`This video is ${mb} MB. Keep videos under ${MAX_MB} MB (about 2 to 3 minutes).`, "bad", 7000);
  fillRun(i, { mb }, (target, f, draw) => {
    const form = new FormData(); form.append("file", file, file.name);
    form.append("hint", target.gap); form.append("plan_id", target.plan_id); form.append("gap", target.gap);
    return upload("/api/capture", form, p => { f.pct = p; if (p >= 1 && f.stage === 0) { f.stage = 1; f.started = Date.now(); } draw(); });
  });
}

async function fillYouTube(i, btn) {
  const url = $(`#fillYt-${i}`).value.trim(); if (!url) return $(`#fillYt-${i}`).focus();
  btn.disabled = true;
  try {
    const { videos } = await postJSON("/api/youtube/resolve", { url, whole_playlist: false });
    const v = videos[0];
    if (v.in_library) return fillLink(i, v.in_library);   // imported before: use that playbook, no new Gemini call
    if (v.problem) throw new Error(`${v.title}: ${v.problem}.`);
    fillRun(i, { stage: 1 }, (target) => call("/api/capture/youtube", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: v.video_id, hint: target.gap, ...target }) }, DIRECT_API));
  } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
}

$("#fillFile").onchange = e => {
  const f = e.target.files[0], i = gapIndex(state.fillTarget);
  if (f && i >= 0) fillUpload(i, f);
  e.target.value = "";
};
document.addEventListener("keydown", e => {   // Enter in a gap's YouTube box
  const m = e.key === "Enter" && e.target.id?.match(/^fillYt-(\d+)$/);
  if (m) fillYouTube(+m[1], $(`[data-fill-yt="${m[1]}"]`));
});

function fillLink(i, playbookId) {
  if (!playbookId) return $(`#fillLib-${i}`)?.focus();
  fillRun(i, { stage: 2 }, (target) => postJSON(`/api/plans/${target.plan_id}/fill`, { gap: target.gap, playbook_id: playbookId }));
}

function copyGap(i) {
  const plan = state.lastPlan, g = plan.gaps[i];
  const text = `Hi! We're onboarding a new ${plan.role} and nobody has recorded "${g.topic}" yet. Could you record a 2 to 3 minute Tasklore capture of you doing it, explaining each step out loud? They'll need it by day ${g.needed_by_day}. Thank you!`;
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

// Demo mode: put back the sample team. Undo instead of a dialog; the reset is sent only after 5 s.
function resetSample(btn) {
  btn.disabled = true;
  const commit = setTimeout(async () => {
    try {
      await call("/api/demo/reset", { method: "POST" });
      Object.assign(state, { lastPlan: null, lastPlanId: null, plan: null, planId: null, fills: {}, agent: {} });
      $("#planOut").innerHTML = ""; renderFlow(); loadPlans();
      toast("The sample team is back: John's Python Developer plan and 3 playbooks.", "good", 6000);
    } catch (e) { toast(e.message, "bad"); }
    btn.disabled = false;
  }, 5000);
  toast("Resetting: every plan, playbook and request is replaced by the sample team.", "info", 5000,
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
        <div class="grow"><b>${esc(p.role)}</b> <span class="small muted">created ${fmtDate(p.created_at)}, ${p.assigned_to ? `for ${esc(p.assigned_to)}` : "not assigned yet"}</span>
          <div class="small muted">Coverage ${p.coverage_percent}%, ${p.gaps} gaps, ${ago(p.progress.updated_at)}</div>
          <div class="small">${p.progress.complete ? `<span class="chip good">Complete</span>` : `${p.progress.done} done, ${p.progress.skipped} skipped`}
          ${p.progress.stuck_events ? ` <span class="chip warn">stuck ${p.progress.stuck_events}×</span>` : ""}${p.progress.coach_sessions ? ` <span class="chip brand">live coach ${p.progress.coach_sessions}×</span>` : ""}${p.progress.verified ? ` <span class="chip good">verified ${p.progress.verified}×</span>` : ""}</div></div>
        <div class="row">${p.gaps ? `<button class="btn sm ghost" data-recheck="${esc(p.id)}" title="Match this plan against the playbooks recorded since">Re-check coverage</button>` : ""}
          ${p.progress.updated_at ? `<button class="btn sm ghost" data-restart="${esc(p.id)}" title="Clear this plan's progress and start again">Restart</button>` : ""}
          ${p.sample ? `<span class="chip">Sample</span>` : `<button class="btn sm ghost" data-delete-plan="${esc(p.id)}">Delete</button>`}
          <button class="btn sm" data-view-plan="${esc(p.id)}">View</button></div></div>`).join("")
      : `<div class="empty">No onboarding plans yet. Describe a role above to create the first one.</div>`;
  } catch (e) { $("#plansList").innerHTML = `<div class="empty">Couldn't load plans. ${esc(e.message)}</div>`; }
}


// ---------------- assignment, recording requests, delete ----------------
function assignHtml(plan, id) {
  const who = plan.assigned_to;
  return `<div class="assign" id="assign-box">
    ${who ? `<p class="small">Assigned to <b>${esc(who)}</b>. Only they (and managers) can open this plan. <button class="btn sm ghost" data-assign-edit>Change</button></p>` : ""}
    <div class="row" ${who ? "hidden" : ""} id="assign-form">
      <label for="assignEmail" class="small" style="margin:0">Assign to a new hire</label>
      <input type="text" id="assignEmail" list="newhireList" inputmode="email" placeholder="name@company.com" style="flex:1; min-width:200px">
      <button class="btn sm primary" data-assign="${esc(id)}">Assign</button>
      ${state.me?.email ? `<button class="btn sm ghost" data-assign-me="${esc(id)}">Assign to me</button>` : ""}
    </div></div>`;
}
async function assignPlan(id, email) {
  try {
    const r = await postJSON(`/api/plans/${id}/assign`, { email });
    state.lastPlan.assigned_to = r.assigned_to;
    $("#assign-box").outerHTML = assignHtml(state.lastPlan, id);
    renderFlow();
    toast(r.assigned_to ? `Assigned to ${r.assigned_to}. They'll see it when they sign in.` : "Unassigned.", "good");
    loadPlans(); loadPeople();
  } catch (e) { toast(e.message, "bad"); }
}

const gapRequest = (g) => (state.planRequests || []).filter(x => x.topic === g.topic).sort((a, b) => b.created_at - a.created_at)[0];
function gapRequestHtml(g, i, planId) {
  const f = state.fills[fillKey(planId, g.topic)];
  if (f && !f.error) return `<span class="chip brand">Adding a video</span>`;
  const r = gapRequest(g), add = `<button class="btn sm primary" data-fill-gap="${i}">Add a video</button>`;
  if (r?.status === "open") return `<span class="chip brand">Requested from ${esc(r.expert_email)}</span>${add}`;
  if (r?.status === "done") return `<span class="chip good">Recorded by ${esc(r.expert_email)}</span>`;
  const declined = r?.status === "declined" ? `<span class="chip warn" title="${esc(r.decline_reason || "")}">Declined by ${esc(r.expert_email)}${r.suggested ? `, suggests ${esc(r.suggested)}` : ""}</span>` : "";
  return `${declined}${add}<button class="btn sm" data-ask-gap="${i}">Ask an expert</button><button class="btn sm ghost" data-copy-gap="${i}">Copy message</button>`;
}
function openAskForm(i) {
  const g = state.lastPlan.gaps[i], prev = (state.planRequests || []).find(x => x.topic === g.topic && x.status === "declined");
  $(`#ask-${i}`).innerHTML = `<div class="row" style="margin-top:8px">
    <input type="text" id="askEmail-${i}" list="expertList" inputmode="email" placeholder="expert@company.com" value="${esc(prev?.suggested || "")}" style="flex:1; min-width:200px" aria-label="Expert's email">
    <button class="btn sm primary" data-ask-send="${i}">Send request</button></div>
    <p class="small muted" style="margin:6px 0 0">They'll see it under Library when they sign in, and recording it updates this plan's coverage automatically.</p>`;
  $(`#askEmail-${i}`).focus();
}
async function sendAsk(i) {
  const g = state.lastPlan.gaps[i], email = $(`#askEmail-${i}`).value.trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return toast("Enter the expert's email address.", "bad");
  try {
    const r = await postJSON("/api/requests", { plan_id: state.lastPlanId, topic: g.topic, expert_email: email });
    state.planRequests = [...(state.planRequests || []), r];
    renderPlan(state.lastPlan, state.lastPlanId, state.planRequests);
    toast(`Asked ${email} to record “${g.topic}”.`, "good"); loadPeople();
  } catch (e) { toast(e.message, "bad"); }
}

async function viewPlan(id, opts = {}) {
  try {
    const r = await call(`/api/plans/${id}`);
    state.titles = r.playbook_titles || {};
    renderPlan(r.plan, id, r.requests || [], opts);
  } catch (e) { toast(e.message, "bad"); }
}

function deletePlan(id, btn) {
  const row = btn.closest(".plan-row"); row.classList.add("leaving"); row.style.opacity = ".4";
  const commit = setTimeout(async () => {
    try {
      await call(`/api/plans/${id}`, { method: "DELETE" });
      if (state.lastPlanId === id) { $("#planOut").innerHTML = ""; state.lastPlan = state.lastPlanId = null; renderFlow(); }
      loadPlans();
    }
    catch (e) { row.style.opacity = ""; toast(e.message, "bad"); }
  }, 5000);
  toast("Plan deleted.", "info", 5000, { label: "Undo", onClick: () => { clearTimeout(commit); row.style.opacity = ""; } });
}

async function loadPeople() {
  if (state.me?.role !== "manager") return;
  try {
    const p = await call("/api/people");
    $("#expertList").innerHTML = (p.expert || []).map(e => `<option value="${esc(e)}">`).join("");
    $("#newhireList").innerHTML = (p.newhire || []).map(e => `<option value="${esc(e)}">`).join("");
  } catch {}
}

// Experts: "Requests for you" on the Capture page. Recording (any way) while a request is active answers it.
async function loadRequests() {
  const box = $("#reqBox");
  if (state.me?.role !== "expert") { box.innerHTML = ""; return; }
  try {
    const { requests } = await call("/api/requests");
    const open = requests.filter(r => r.status === "open"), closed = requests.filter(r => r.status !== "open").slice(0, 3);
    box.innerHTML = `<div class="card"><h2>Recording requests for you (${open.length})</h2>
      <p class="muted small">A manager found these gaps in a new hire's plan. Record each once (2 to 3 minutes); the plan updates by itself.</p>
      ${open.map(r => `<div class="gap"><b>${esc(r.topic)}</b><div class="small">${esc(r.why_needed)}</div>
        <div class="foot"><span class="small">For a new ${esc(r.plan_role)}${r.assigned_to ? ` (${esc(r.assigned_to)})` : ""}, needed by day ${esc(r.needed_by_day)}. Asked by ${esc(r.created_by?.name || r.created_by?.email || "a manager")}</span>
        <span class="row"><button class="btn sm primary" data-req-record="${esc(r.id)}">Record it</button><button class="btn sm ghost" data-req-decline="${esc(r.id)}">Decline</button></span></div>
        <div id="decline-${esc(r.id)}"></div></div>`).join("") || `<div class="empty">No open requests. Nice.</div>`}
      ${closed.length ? `<p class="small muted" style="margin-top:10px">Recent: ${closed.map(r => `${esc(r.topic)} (${r.status})`).join(", ")}</p>` : ""}</div>`;
    state.requests = requests;
  } catch (e) { box.innerHTML = `<div class="empty">Couldn't load your requests. ${esc(e.message)}</div>`; }
}
function startRequest(id) {
  const r = state.requests.find(x => x.id === id); state.activeReq = r;
  $("#reqActive").innerHTML = `<div class="hint" style="margin-bottom:14px"><b>Recording for this request:</b> ${esc(r.topic)}.
    Record your screen, upload a video or import from YouTube below; it will be linked to the request.
    <button class="btn sm ghost" data-req-cancel style="float:right">Cancel</button></div>`;
  $("#hint").value = r.topic; $("#reqActive").scrollIntoView({ behavior: "smooth", block: "center" });
}
function requestAnswered(info) {
  if (!info) return;
  state.activeReq = null; $("#reqActive").innerHTML = "";
  const moved = info.coverage_after !== undefined && info.coverage_after !== info.coverage_before;
  toast(moved ? `Request done. The plan's coverage went from ${info.coverage_before}% to ${info.coverage_after}%.`
              : `Request done. The manager can see your recording${info.coverage_after !== undefined ? `; coverage stays ${info.coverage_after}% until it matches a task` : ""}.`,
        "good", 8000);
  loadRequests();
}
function openDecline(id) {
  $(`#decline-${CSS.escape(id)}`).innerHTML = `<div class="row" style="margin-top:8px">
    <input type="text" id="declineWhy-${esc(id)}" placeholder="Why? e.g. Not my area" style="flex:1; min-width:160px" aria-label="Reason">
    <input type="text" id="declineWho-${esc(id)}" list="expertList" placeholder="Who to ask instead (optional)" style="flex:1; min-width:160px" aria-label="Who to ask instead">
    <button class="btn sm" data-req-decline-send="${esc(id)}">Send</button></div>`;
}
async function sendDecline(id) {
  try {
    await postJSON(`/api/requests/${id}/decline`, { reason: $(`#declineWhy-${CSS.escape(id)}`).value, suggest: $(`#declineWho-${CSS.escape(id)}`).value });
    if (state.activeReq?.id === id) { state.activeReq = null; $("#reqActive").innerHTML = ""; }
    toast("Declined. The manager will see your note.");
    loadRequests();
  } catch (e) { toast(e.message, "bad"); }
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
    if (!plans.length) {
      $("#learnOut").innerHTML = state.me?.role === "newhire"
        ? `<div class="empty">No onboarding plan is assigned to you yet. Your manager assigns plans to <b>${esc(state.me.email)}</b>.${state.me.demo ? " In this demo, switch to Manager and use “Assign to me” on a plan." : ""}</div>`
        : `<div class="empty">No onboarding plan yet. <a href="#plan">Build one</a> first.</div>`;
      return;
    }
    if (want !== state.planId || !state.plan) await openPlan(want); else renderLearn();
  } catch (e) { $("#learnOut").innerHTML = `<div class="empty">Couldn't load your plans. ${esc(e.message)}</div>`; }
}
$("#planPick").onchange = e => e.target.value && openPlan(e.target.value);

async function openPlan(id) {
  try {
    const r = await call(`/api/plans/${id}`);
    Object.assign(state, { plan: r.plan, planId: id, progress: r.plan.progress, titles: r.playbook_titles || {}, openSteps: null, help: null, proof: null, pbCache: {} });
    remember(id); renderLearn();
  } catch (e) { $("#learnOut").innerHTML = `<div class="empty">Couldn't open this plan. ${esc(e.message)}</div>`; }
}

const firstName = () => ((state.me?.name || state.user?.displayName || "").trim().split(/\s+/)[0] || "");
const ST_ICON = { done: "✓", skipped: "↷", stuck: "?" };
const canProve = (x) => x.kind === "practice" && !!x.playbook_id;   // practice steps have a checklist to check against
function renderLearn(message, kind = "") {
  const { plan, progress } = state; if (!plan) return;
  const verified = new Set(progress.log.filter(e => e.outcome === "done" && e.verified).map(e => e.item_index));
  const s = progress.status, n = s.length, finished = s.filter(x => x === "done" || x === "skipped").length;
  const pct = n ? Math.round(100 * finished / n) : 100;
  const it = progress.current == null ? null : plan.items[progress.current];
  const opt = $("#planPick").selectedOptions[0];
  if (opt) opt.textContent = opt.textContent.replace(/\(\d+% done/, `(${pct}% done`);
  $("#learnOut").innerHTML = `
    <div class="card"><div class="cover">${ring(pct, "small")}<div><h2>${firstName() ? `Hi ${esc(firstName())}, here's your plan as ${esc(plan.role)}` : `Your plan as ${esc(plan.role)}`}</h2>
      <p class="muted small" style="margin:0">${finished} of ${n} steps finished, saved automatically</p></div></div></div>
    ${it ? `<div class="card now">
      <span class="small muted">Now, day ${it.day}: <span class="k ${esc(it.kind)}">${esc(it.kind)}</span></span>
      <h2 style="margin-top:4px">${esc(it.title)}</h2><p>${esc(it.detail)}</p>
      ${it.playbook_id ? `<button class="btn sm" data-action="toggle-steps">${state.openSteps ? "Hide" : "Show"} the playbook: ${esc(state.titles[it.playbook_id] || "steps")}</button>
        ${state.openSteps || ""}` : ""}
      <div class="row" style="margin-top:12px">
        ${canProve(it) ? `<button class="btn primary" data-proof="${progress.current}">Check my work</button>` : ""}
        <button class="btn ${canProve(it) ? "" : "primary"}" data-outcome="done">Done, next step</button>
        <button class="btn ghost" data-outcome="already_know">I already know this</button></div>
      <p class="small muted" style="margin:10px 0 0">${canProve(it) ? "Finished? <b>Check my work</b>: upload a screenshot of your result and Gemini checks it against your team's checklist. " : ""}Stuck on any step? Use <b>I'm stuck</b> or <b>Live coach</b> next to it in your plan below.</p>
      ${message ? `<div class="hint ${kind}">${esc(message)}</div>` : ""}</div>`
    : `<div class="card done-banner"><div class="check-big" aria-hidden="true">✓</div><h2>You've finished your onboarding plan</h2>
        <p class="muted">${esc(message || "Great work. Your manager can see your progress.")}</p>
        <button class="btn" data-restart="${esc(state.planId)}">Start this plan again</button></div>`}
    <div class="card status-list"><h2>Your plan</h2>${plan.items.map((x, i) => (i === 0 || plan.items[i - 1].day !== x.day ? `<h3 style="margin:12px 0 6px">Day ${x.day}</h3>` : "") +
      `<div class="pi ${s[i] || ""} ${i === progress.current ? "current" : ""}" id="step-${i}"><span class="st ${s[i] || ""}" aria-label="${s[i] || "to do"}">${ST_ICON[s[i]] || ""}</span>
        <div class="pi-main"><span class="k ${esc(x.kind)}">${esc(x.kind)}</span>${verified.has(i) ? ` <span class="chip good">Verified by screenshot</span>` : ""}
          <div><b>${esc(x.title)}</b></div><div class="small muted">${esc(x.detail)}</div>
          ${x.kind === "practice" && !x.playbook_id && s[i] !== "done" && s[i] !== "skipped"
            ? `<div class="small muted no-check">No recorded playbook for this yet, so there's no checklist to check your work against.</div>` : ""}
          ${state.help?.index === i ? helpPanel(x, i) : ""}${state.proof?.index === i ? proofPanel(x, i) : ""}</div>
        ${s[i] === "done" || s[i] === "skipped" ? "" : `<div class="pi-actions">
          ${canProve(x) ? `<button class="btn sm ${state.proof?.index === i ? "" : "ghost"}" data-proof="${i}" aria-expanded="${state.proof?.index === i}">Check my work</button>` : ""}
          <button class="btn sm ${state.help?.index === i ? "" : "ghost"}" data-help="${i}" aria-expanded="${state.help?.index === i}">I'm stuck</button>
          <button class="btn sm ghost" data-coach-step="${i}" title="Talk it through with a voice coach who knows this step and can see your screen">Live coach</button></div>`}</div>`).join("")}</div>`;
}

async function toggleSteps() {
  if (state.openSteps) { state.openSteps = null; return renderLearn(); }
  const pbId = state.plan.items[state.progress.current].playbook_id;
  try {
    const { playbook: p } = await call(`/api/playbooks/${pbId}`);
    state.openSteps = `<ol class="timeline" style="margin-top:14px">${p.steps.map(st => `<li><b>${esc(st.title)}</b>${improvedMark(st)}<div>${esc(st.action)}</div><div class="why">Why: ${esc(st.why)}</div></li>`).join("")}</ol>
      ${lessonsHtml(p)}`;
    renderLearn();
  } catch (e) { toast(e.message, "bad"); }
}

async function report(outcome, btn) {
  state.help = null;
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



// ---------------- help on a step: I'm stuck (typed or spoken), hint inside the step ----------------
const NEUTRAL_EXAMPLE = "what you tried, and what happened instead";
function helpPanel(item, i) {
  const h = state.help;
  return `<div class="help-panel">
    <label for="note-${i}" class="small">What's going wrong? <span class="opt">(optional)</span></label>
    <input type="text" id="note-${i}" maxlength="400" value="${esc(h.draft || "")}" placeholder="e.g. ${esc(item.stuck_example || NEUTRAL_EXAMPLE)}">
    <div class="row" style="margin-top:8px">
      <button class="btn sm primary" data-hint="${i}">Get a hint</button>
      <button class="btn sm ghost" data-voice="${i}" title="Say what's wrong, in English or Hindi">Ask by voice</button>
      <span class="small muted">English or Hindi</span></div>
    ${faqHtml(state.pbCache?.[item.playbook_id]?.faq, "Other new hires asked")}
    ${h.message ? `<div class="hint">${h.kind === "hint" ? `<button class="btn sm listen" data-action="listen">Listen</button>` : ""}${h.heard ? `<span class="heard">You asked: “${esc(h.heard)}”</span>` : ""}${esc(h.message)}</div>` : ""}
  </div>`;
}
function toggleHelp(i) {
  state.help = state.help?.index === i ? null : { index: i };
  state.proof = null;
  renderLearn();
  if (state.help) { $(`#note-${i}`)?.focus(); loadStepFaq(state.plan.items[i].playbook_id, i); }
}
// Answers the recorder approved for earlier new hires' questions, shown before they even ask.
async function loadStepFaq(pbId, i, forProof = false) {
  state.pbCache ||= {};
  if (!pbId || state.pbCache[pbId]) return;
  try {
    state.pbCache[pbId] = (await call(`/api/playbooks/${pbId}`)).playbook;
    if (forProof) { if (state.proof?.index === i) renderLearn(); return; }
    if (state.help?.index === i && state.pbCache[pbId].faq?.length) { renderLearn(); $(`#note-${i}`)?.focus(); }
  } catch {}
}
// ---------------- proof of practice: a screenshot of the result, checked against "done right when" ----------------
const VERDICT = { pass: ["good", "Verified: this matches your team's checklist"], almost: ["warn", "Almost there"],
                  not_yet: ["bad", "Not yet"], unclear: ["", "I can't see this task in the screenshot"] };
const CHECK_ICON = { met: "✓", missing: "!", cant_tell: "?" };
const CHECK_LABEL = { met: "done", missing: "missing", cant_tell: "can't tell from a screenshot" };

function proofPanel(item, i) {
  const pr = state.proof, r = pr.result, pb = state.pbCache?.[item.playbook_id];
  const [cls, label] = r ? VERDICT[r.verdict] : [];
  return `<div class="help-panel proof-panel">
    ${r && !pr.busy ? `<div class="proof-result ${cls}"><b class="verdict">${label}</b><p>${esc(r.feedback)}</p>
      <ul class="checks">${r.criteria.map(c => `<li class="${c.status}"><span class="ci" aria-label="${CHECK_LABEL[c.status]}">${CHECK_ICON[c.status]}</span>
        <div>${esc(c.criterion)}${c.evidence ? `<div class="small muted">${esc(c.evidence)}</div>` : ""}</div></li>`).join("")}</ul>
      ${r.gotchas_seen.length ? `<p class="small" style="margin:8px 0 0"><b>Watch out:</b> ${r.gotchas_seen.map(esc).join("; ")}</p>` : ""}
      ${pr.preview ? `<img class="proof-thumb" src="${pr.preview}" alt="The screenshot you uploaded">` : ""}</div>` : ""}
    ${pr.busy ? `<div class="proof-busy">${pr.preview ? `<img class="proof-thumb" src="${pr.preview}" alt="">` : ""}
        <span><span class="spin" aria-hidden="true"></span> Gemini is checking your screenshot against the checklist…</span></div>`
      : r?.verdict === "pass" ? "" : `
      <p class="small" style="margin:0 0 6px"><b>${r ? "Fixed it? Upload a new screenshot." : "Show your finished result."}</b>
        Take a screenshot of the whole result (for example the full sheet), then paste it here or choose a file.</p>
      ${pb && !r ? `<p class="small muted" style="margin:0 0 6px">Your team's checklist: ${pb.success_criteria.map(esc).join(" · ")}</p>` : ""}
      <button class="drop proof-drop" data-proof-pick="${i}"><b>Paste a screenshot</b>
        <span class="small muted">${navigator.platform?.includes("Mac") ? "⌘" : "Ctrl"} V, drop an image here, or click to choose</span></button>
      <p class="small muted" style="margin:6px 0 0">Your screenshot is only used for this check and isn't saved. Crop out anything private first.</p>`}
  </div>`;
}

function toggleProof(i) {
  state.proof = state.proof?.index === i ? null : { index: i };
  state.help = null;
  renderLearn();
  if (state.proof) { $(`#step-${i}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); loadStepFaq(state.plan.items[i].playbook_id, i, true); }
}

// Big retina screenshots are scaled down (still easy to read) so the upload is quick.
async function shrinkImage(file) {
  const img = await createImageBitmap(file), scale = Math.min(1, 2400 / img.width);
  if (scale === 1 && file.size < 2 * 1048576) return file;
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return new Promise(done => c.toBlob(b => done(b || file), "image/jpeg", 0.9));
}

async function sendProof(file) {
  const pr = state.proof; if (!pr || pr.busy) return;
  if (!file || !file.type.startsWith("image/")) return toast("Please choose an image: a screenshot of your result.", "bad");
  let blob = file;
  try { blob = await shrinkImage(file); } catch {}   // e.g. HEIC the browser can't draw: send as is
  if (blob.size > 7 * 1048576) return toast("That image is too large. Crop it to the part that shows your result.", "bad");
  if (pr.preview) URL.revokeObjectURL(pr.preview);
  pr.preview = URL.createObjectURL(blob); pr.busy = true; renderLearn();
  const form = new FormData();
  form.append("plan_id", state.planId); form.append("item_index", pr.index);
  form.append("file", blob, blob.type === "image/png" ? "screenshot.png" : "screenshot.jpg");
  try {
    const r = await call("/api/session/proof", { method: "POST", body: form });
    state.progress = r.progress; Object.assign(pr, { result: r.proof, busy: false });
    if (r.branch === "verified") { toast("Verified. This step is done.", "good"); renderLearn(r.message, "good"); }
    else renderLearn();
  } catch (e) { pr.busy = false; toast(e.message, "bad"); renderLearn(); }
}

// One request: the step becomes the current step and the hint appears inside it.
async function askHint(i, btn) {
  const note = $(`#note-${i}`)?.value || "";
  btn.disabled = true; btn.textContent = "Thinking…";
  try {
    const r = await postJSON("/api/session", { plan_id: state.planId, item_index: i, outcome: "stuck", note });
    state.progress = r.progress;
    state.help = { index: i, message: r.message, kind: "hint", draft: note };
    renderLearn();
  } catch (e) { toast(e.message, "bad"); btn.disabled = false; btn.textContent = "Get a hint"; }
}

// ---------------- voice: listen, ask by voice, live coach ----------------
// Listen: a hint read aloud by Gemini text-to-speech (any language the hint is in).
async function speakText(text, btn) {
  if (state.audio) { state.audio.pause(); state.audio = null; $$("[data-action=listen], [data-km-listen]").forEach(b => b.textContent = "Listen"); return; }
  if (btn) btn.textContent = "Preparing audio…";
  try {
    const t = await token();
    const r = await fetch(API + "/api/speak", { method: "POST", body: JSON.stringify({ text }),
      headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) } });
    if (!r.ok) { const b = await r.json().catch(() => ({})); throw new ApiError(friendly(r.status, b.detail), r.status); }
    const url = URL.createObjectURL(await r.blob()), audio = new Audio(url);
    state.audio = audio;
    audio.onended = () => { state.audio = null; URL.revokeObjectURL(url); $$("[data-action=listen], [data-km-listen]").forEach(b => b.textContent = "Listen"); };
    $$("[data-action=listen], [data-km-listen]").forEach(b => b.textContent = "Stop");
    await audio.play();
  } catch (e) { state.audio = null; if (btn) btn.textContent = "Listen"; toast(e.message, "bad"); }
}

// Ask by voice: tap to start, tap again to send. Gemini understands the question and answers in the same language.
async function askByVoice(btn, index) {
  if (state.rec) return finishVoice();
  const chunks = [], started = Date.now();
  let loudest = 0;
  const mic = new MicCapture(c => { chunks.push(c); loudest = Math.max(loudest, mic.level); });
  try { await mic.start(); } catch { return toast("The microphone is blocked. Allow microphone access for this site and try again.", "bad", 7000); }
  state.rec = { mic, chunks, started, index, loudest: () => loudest, timer: setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000), b = $(`[data-voice="${index}"]`);
    if (b) { b.classList.add("recording"); b.textContent = `Stop and ask (${fmtTime(secs)})`; }
    if (secs >= 60) finishVoice();   // keep questions short
  }, 250) };
  btn.classList.add("recording"); btn.textContent = "Stop and ask (0:00)";
}
async function finishVoice() {
  const rec = state.rec; if (!rec) return;
  state.rec = null; clearInterval(rec.timer); rec.mic.stop();
  const b = $(`[data-voice="${rec.index}"]`);
  if (b) { b.classList.remove("recording"); b.textContent = "Thinking…"; b.disabled = true; }
  if (Date.now() - rec.started < 700) { toast("That was very short. Tap, ask your question, then tap again."); return renderLearn(); }
  if (rec.loudest() < 0.02) {   // the microphone never picked up a voice: don't send silence
    toast("I couldn't hear anything. Check that the right microphone is selected and not muted.", "bad", 7000); return renderLearn();
  }
  const form = new FormData();
  form.append("plan_id", state.planId); form.append("item_index", rec.index);
  form.append("file", wavFromPcm16(rec.chunks), "question.wav");
  try {
    const r = await call("/api/session/voice", { method: "POST", body: form });
    state.progress = r.progress;
    state.help = { index: rec.index, message: r.message, heard: r.heard, kind: r.branch };
    renderLearn();
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

async function startCoach(btn, index) {
  if (state.coach) return toast("A live coach session is already running. End it first.");
  const dock = $("#coachDock");
  $("#coachCaptions").innerHTML = ""; state.coachLine = null;
  dock.hidden = false; document.body.classList.add("coach-open");
  $("[data-action=coach-screen]").hidden = !navigator.mediaDevices?.getDisplayMedia;   // phones can't share a screen from the browser
  coachStatus("Connecting…", "connecting"); btn.disabled = true;
  const coach = state.coach = new LiveCoach({ caption: coachCaption, ended: (reason) => endCoachUi(reason) });
  try {
    $("#coachTitle").textContent = `Coaching: ${state.plan.items[index].title}`;
    const tok = await postJSON("/api/coach/start", { plan_id: state.planId, item_index: index });
    state.progress.current = index; renderLearn();   // the server made this the current step too
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

document.addEventListener("input", (e) => {
  if (e.target.id?.startsWith("note-") && state.help) state.help.draft = e.target.value;
  const se = e.target.dataset?.sugEdit;   // the recorder corrects a suggestion before saving it
  if (se) { const [id, k, field] = se.split(":"); state.sug[id].suggestions[+k][field] = e.target.innerText.trim(); }
});
document.addEventListener("change", (e) => {
  const i = e.target.dataset?.ytPick;
  if (i !== undefined) { state.yt.videos[+i].pick = e.target.checked; renderYouTube(); }
  const sp = e.target.dataset?.sugPick;
  if (sp !== undefined) { const [id, k] = sp.split(":"); state.sug[id].picked[+k] = e.target.checked; renderSuggestions(id); }
});

// Proof of practice: paste a screenshot (the quickest way), drop it, or choose a file.
$("#proofFile").onchange = e => { sendProof(e.target.files[0]); e.target.value = ""; };
document.addEventListener("paste", (e) => {
  if (!state.proof || state.proof.busy || location.hash !== "#learn") return;
  const f = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith("image/"));
  if (f) { e.preventDefault(); sendProof(f); }
});
document.addEventListener("dragover", (e) => { const z = e.target.closest?.(".proof-drop"); if (z) { e.preventDefault(); z.classList.add("over"); } });
document.addEventListener("dragleave", (e) => e.target.closest?.(".proof-drop")?.classList.remove("over"));
document.addEventListener("drop", (e) => { const z = e.target.closest?.(".proof-drop"); if (z) { e.preventDefault(); sendProof(e.dataTransfer.files[0]); } });

// ---------------- clicks on dynamic content ----------------
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, a"); if (!t) return;
  const d = t.dataset;
  if (d.action === "sign-in") signIn();
  else if (d.action === "sign-out") state.fb.signOut(state.auth);
  else if (d.action === "retry-upload") state.retry?.();
  else if (d.action === "close-playbook") $("#capOut").innerHTML = "";
  else if (d.action === "toggle-steps") toggleSteps();
  else if (d.action === "listen") speakText(state.help?.message, t);
  else if (d.help) toggleHelp(+d.help);
  else if (d.proof !== undefined) toggleProof(+d.proof);
  else if (d.proofPick !== undefined) $("#proofFile").click();
  else if (d.hint) askHint(+d.hint, t);
  else if (d.voice) askByVoice(t, +d.voice);
  else if (d.coachStep) startCoach(t, +d.coachStep);
  else if (d.action === "coach-end") state.coach?.stop();
  else if (d.action === "coach-screen") toggleCoachScreen(t);
  else if (d.viewPb) viewPlaybook(d.viewPb);
  else if (d.delPb) deletePlaybook(d.delPb, t);
  else if (d.copyGap) copyGap(+d.copyGap);
  else if (d.improve) openImprove(d.improve, t);
  else if (d.sugApply) applyImprove(d.sugApply, t);
  else if (d.sugDismiss) applyImprove(d.sugDismiss, t, true);
  else if (d.gotoImprove) state.improveTarget = d.gotoImprove;   // the link then opens the Library
  else if (d.kmCell) kmCell(...d.kmCell.split(":").map(Number), t);
  else if (d.kmAsk) kmAskForm(...d.kmAsk.split(":").map(Number));
  else if (d.kmSend) kmSend(...d.kmSend.split(":").map(Number), t);
  else if (d.kmBrief !== undefined) kmBrief(t);
  else if (d.kmListen !== undefined) speakText(state.kmBrief, t);
  else if (d.themeSet) {   // tapping the active mode (the only one visible on phones) moves to the next
    const modes = ["auto", "light", "dark"];
    setTheme(t.getAttribute("aria-pressed") === "true" ? modes[(modes.indexOf(d.themeSet) + 1) % 3] : d.themeSet);
  }
  else if (d.resetSample !== undefined) resetSample(t);
  else if (d.recheck) recheckPlan(d.recheck, t);
  else if (d.restart) restartPlan(d.restart, t);
  else if (d.roleSet) setRole(d.roleSet);
  else if (d.assign) assignPlan(d.assign, $("#assignEmail").value.trim());
  else if (d.assignMe) assignPlan(d.assignMe, state.me.email);
  else if (d.assignEdit !== undefined) { $("#assign-form").hidden = false; $("#assignEmail").focus(); }
  else if (d.flow) $(`#${d.flow}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  else if (d.fillGap) openFill(+d.fillGap);
  else if (d.fillFile) { state.fillTarget = state.lastPlan.gaps[+d.fillFile].topic; $("#fillFile").click(); }
  else if (d.fillYt) fillYouTube(+d.fillYt, t);
  else if (d.fillLink) fillLink(+d.fillLink, $(`#fillLib-${d.fillLink}`).value);
  else if (d.askGap) openAskForm(+d.askGap);
  else if (d.agentRun) runAgent(d.agentRun);
  else if (d.askSend) sendAsk(+d.askSend);
  else if (d.viewPlan) viewPlan(d.viewPlan);
  else if (d.deletePlan) deletePlan(d.deletePlan, t);
  else if (d.reqRecord) startRequest(d.reqRecord);
  else if (d.reqDecline) openDecline(d.reqDecline);
  else if (d.reqDeclineSend) sendDecline(d.reqDeclineSend);
  else if (d.reqCancel !== undefined) { state.activeReq = null; $("#reqActive").innerHTML = ""; }
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
