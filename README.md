# FirstRung

**Seniors show a task once. New hires get an onboarding plan that adapts to them.**

AI Builder Cup 2026 · Track: Future of Work & Enterprise Productivity

A senior records their screen while doing a real task and explaining it. Gemini turns the recording into a playbook. A manager describes a new hire's role, and FirstRung builds a day-by-day onboarding plan from the team's playbooks, **flags the knowledge nobody has recorded yet**, and adapts as the new hire works (hints when stuck, skips what they already know).

Live: https://firstrung-993fd.web.app

## Architecture (API-first)

```mermaid
flowchart LR
  U[Browser] -- Sign in with Google --> FA[Firebase Auth]
  U -- "/api/** + ID token" --> H[Firebase Hosting]
  H -- rewrite --> R[Cloud Run: FirstRung API<br/>FastAPI]
  R -- verify token --> FA
  R --> G[Gemini API<br/>gemini-flash-latest]
  R --> F[(Firestore)]
  S[Secret Manager<br/>Gemini API key] -. mounted at startup .-> R
  AR[Artifact Registry] -. image .-> R
  CB[Cloud Build] -- builds --> AR
```

The API is the product; the web app is only the first client. Every endpoint except `/api/health` requires a Firebase ID token (`Authorization: Bearer <token>`), so a Google Chat bot, mobile app or HR system can call the same endpoints after signing the user in.

| Endpoint | What it does |
| --- | --- |
| `GET /api/health` | Infra check, no login: Cloud Run is up, Gemini mode, Firestore reachable |
| `GET /api/me` | Login check: who is signed in (also saves the user profile) |
| `GET /api/hello` | Infra check: one real Gemini call, end to end |
| `POST /api/capture` | Screen recording (multipart `file`, optional `hint`) → structured playbook. The web app calls the Cloud Run URL directly for this one (it can take longer than Hosting's 60 s limit) |
| `GET /api/playbooks` | The team's playbook library, newest first |
| `GET /api/playbooks/{id}` · `DELETE /api/playbooks/{id}` | One playbook / remove it |
| `POST /api/plan` | `{role_description, days}` → onboarding plan, coverage %, **gap report** |
| `GET /api/plans` | Manager view: every plan with progress (% through, done, skipped, times stuck) |
| `GET /api/plans/{id}` | One plan with its saved progress, so the new hire resumes after a refresh or on another device |
| `POST /api/plans/{id}/recheck` | Re-match an existing plan's core tasks against the current library, after someone has recorded a gap. Coverage and gaps are recomputed; the plan and the new hire's progress are kept |
| `POST /api/session` | `{plan_id, item_index, outcome: done/stuck/already_know, note}` → the plan adapts and progress is saved. "Already know" skips the whole topic (every later item using the same playbook); "stuck" returns a hint grounded in that playbook's steps and gotchas, and tries a new angle if earlier advice didn't help |

Interactive API docs are at `/docs` on the Cloud Run URL.

### How gap detection works

1. Gemini reads the role description and lists the role's 5–10 core tasks, each with an importance (critical / regular / occasional), the day it is first needed, and who should record it.
2. For each task it names the library playbook that actually teaches it, or none. The API discards any playbook ID that doesn't exist.
3. **Coverage is computed by the API, not guessed by the model**: the importance-weighted share of core tasks that have a playbook. Uncovered tasks become the gap report, most important first, and the plan schedules a "shadow" session before each one is needed.
4. **Closing the loop**: a plan is a snapshot of the library when it was built. When someone records a missing task, "Re-check coverage" matches the plan against today's library (by job outcome, not exact wording). Tested: an Accounts Executive plan went from 12% to 31% after a Hindi-narrated invoice reconciliation was recorded.

### Languages

Seniors can narrate in English, Hindi or Hinglish; the playbook is always written in clear English and records the narration language. Tested with a Hindi-narrated recording of an invoice reconciliation: every step's "why" was taken from the Hindi narration.

## Repository layout

| Path | What |
| --- | --- |
| `backend/` | FastAPI app (`main.py`), Gemini client with model fallback (`gemini.py`), login check (`auth.py`), prompts, schemas, storage |
| `frontend/public/` | Web app, no build step: `index.html` (views), `styles.css` (light + dark themes), `app.js` (Google sign-in, API calls, screen recording, plan and new-hire views), `img/` (real product screenshots from real Gemini output, light + dark) |
| `infra/` | Terraform for all Google Cloud infrastructure |
| `firestore.rules` | Firestore security rules (deny all direct client access) |
| `build.sh`, `deploy.sh`, `cloudbuild.yaml` | Build the API image with Cloud Build and deploy |

## Run locally (no Google account needed)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
STATIC_DIR=../frontend/public uvicorn main:app --reload --port 8080
```

Open http://localhost:8080. With no key it runs in **MOCK mode** (sample data) and sign-in is off, so the frontend can be built without any cloud setup. For real Gemini locally, add `GEMINI_API_KEY=your_key` in front of the `uvicorn` command (never commit the key).

## Deploy to your own Google Cloud project

Prerequisites: `gcloud`, `terraform` (≥ 1.6) and `firebase` CLIs, and a Google Cloud project with billing and Firebase added.

```bash
gcloud auth login && firebase login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login           # lets Terraform act as you

cd infra
cp terraform.tfvars.example terraform.tfvars     # set project_id
terraform init
terraform apply -target=google_secret_manager_secret_iam_member.api_reads_gemini_key  # registry, APIs, empty secret
cd .. && ./build.sh                              # first API image
```

Then, once:

1. **Gemini key** from https://aistudio.google.com/apikey, stored without touching any file:
   ```bash
   read -rs "GEMINI_KEY?Paste your Gemini API key: "; echo
   printf '%s' "$GEMINI_KEY" | gcloud secrets versions add gemini-api-key --data-file=-
   unset GEMINI_KEY
   ```
2. **Google sign-in**: Firebase console → Authentication → Sign-in method → Google → Enable.
   (Kept out of Terraform so the OAuth client secret never lands in Terraform state.)

Finally:

```bash
cd infra && terraform apply && cd ..             # Cloud Run, Firestore, rules, IAM
./deploy.sh                                      # API image + web app
```

After that, `./deploy.sh api` ships backend changes and `./deploy.sh web` ships the web app. Infrastructure changes go in `infra/*.tf` followed by `terraform apply`.

### Security choices

- The Gemini key lives only in Secret Manager; Terraform creates the secret and its access rule, never the value.
- Cloud Run runs as a dedicated service account with only Firestore access and read access to that one secret. Images are built by a separate builder account.
- Firestore rules deny all direct client access; all data flows through the API, which verifies the Firebase ID token.
- The web app has no keys in its code: Firebase Hosting serves the public Firebase config at `/__/firebase/init.json`.

## Models

`GEMINI_MODELS` (set in `infra/cloud_run.tf`) is a fallback list, tried in order: `gemini-flash-latest,gemini-3-flash-preview`. `gemini-2.5-*` models are avoided because they shut down on 16 October 2026.

Note: a project linked to a billing account uses the Gemini API paid tier, which needs prepaid credits bought in AI Studio. Google Cloud free-trial credits don't cover Gemini API usage.

## Known limits (prototype)

- Videos are sent inline: keep recordings under ~25 MB (2–3 minutes).
- Requests through Firebase Hosting time out after 60 seconds; long video captures should call the Cloud Run URL directly.
- One shared workspace: every signed-in user sees the same playbook library and plans.
- One new hire per plan: progress is stored on the plan itself.

## Google tools used

Gemini API (video understanding, structured output, text generation) · Cloud Run · Cloud Build · Artifact Registry · Firestore · Firebase Authentication · Firebase Hosting · Secret Manager · Terraform (Google provider)
