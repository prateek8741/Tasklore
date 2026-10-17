"""The sample team judges see when they first sign in: a Platform team onboarding John, a new Python developer.

Three playbooks are recorded, three of the role's core tasks are not (one already requested from an expert), and
John is two days in: he got stuck twice, once on a step he later finished. That is enough for every feature to
show real data: coverage and gaps, the knowledge map, "Learn from your new hires" and John's own plan.
Everything here has fixed IDs and `sample: True`, so a reset puts back exactly this and nothing can duplicate.
"""

DAY = 86400
TEAM = {
    "priya": {"uid": "sample-priya", "email": "priya@example.com", "name": "Priya (Tech lead)"},
    "arjun": {"uid": "sample-arjun", "email": "arjun@example.com", "name": "Arjun (Senior engineer)"},
    "manager": {"uid": "sample-manager", "email": "", "name": "Sample team"},
}
NEW_HIRE = "john@example.com"
TEAM_ROLES = [("priya@example.com", "expert"), ("arjun@example.com", "expert"), ("ravi@example.com", "expert"),
              (NEW_HIRE, "newhire")]
PLAN_ID = "sample-plan-john"

PLAYBOOKS = {
    "sample-pb-env": ("priya", {
        "title": "Set up the Python dev environment",
        "goal": "Get the service running and its tests passing on your own laptop, the same way every engineer on the team does.",
        "role_hint": "Python Developer", "tools": ["uv", "Python 3.12", "VS Code", "pre-commit", "Docker"],
        "skills": ["uv virtual environments", "pre-commit hooks", "Docker Compose", "Environment variables"],
        "estimated_minutes": 30, "narration_language": "English",
        "steps": [
            {"title": "Clone the repo with SSH", "timestamp": "00:05",
             "action": "Run `git clone git@github.com:acme/orders-api.git` and open the folder in VS Code.",
             "why": "The team uses SSH keys, so HTTPS clones ask for a password on every push."},
            {"title": "Install the right Python with uv", "timestamp": "00:32",
             "action": "Run `uv python install 3.12`, then `uv sync` in the repo folder to create .venv with every dependency.",
             "why": "uv reads the exact versions from uv.lock, so your laptop matches CI and production."},
            {"title": "Copy the example settings", "timestamp": "01:10",
             "action": "Copy `.env.example` to `.env`. Leave the database URL as it is; it points at the local Docker database.",
             "why": "Real secrets never go in the repo. The example file has safe local values."},
            {"title": "Start the database", "timestamp": "01:38",
             "action": "Run `docker compose up -d db`, then `uv run alembic upgrade head` to create the tables.",
             "why": "The service won't start without its tables, and the migrations are the only way we create them."},
            {"title": "Turn on pre-commit", "timestamp": "02:15",
             "action": "Run `uv run pre-commit install`. From now on ruff and the type checks run before every commit.",
             "why": "CI runs the same checks; catching them locally saves a red pull request."},
            {"title": "Run it", "timestamp": "02:40",
             "action": "Run `uv run pytest`, then `uv run fastapi dev` and open http://localhost:8000/health.",
             "why": "Green tests and a healthy endpoint prove the whole setup works before you change anything."},
        ],
        "gotchas": ["Running `pip install` instead of `uv sync`: you get different versions than CI.",
                    "Forgetting `docker compose up -d db`: every test fails with 'connection refused'.",
                    "Committing your .env file. It is in .gitignore; don't force-add it."],
        "success_criteria": ["`uv run pytest` shows all tests passed",
                             "http://localhost:8000/health answers {\"status\": \"ok\"}",
                             "`git commit` runs the pre-commit hooks"],
    }),
    "sample-pb-pr": ("priya", {
        "title": "Open a pull request that passes CI",
        "goal": "Get a change reviewed and merged without breaking main.",
        "role_hint": "Python Developer", "tools": ["Git", "GitHub", "GitHub Actions", "ruff"],
        "skills": ["Branch naming", "Conventional commits", "Reading CI logs", "Code review etiquette"],
        "estimated_minutes": 20, "narration_language": "English",
        "steps": [
            {"title": "Branch from the latest main", "timestamp": "00:04",
             "action": "Run `git switch main && git pull`, then `git switch -c feat/ORD-123-short-name` with the ticket number.",
             "why": "Branches from an old main hit merge conflicts, and the ticket number links the PR to Jira."},
            {"title": "Run the checks before pushing", "timestamp": "00:40",
             "action": "Run `uv run ruff check --fix .`, `uv run ruff format .` and `uv run pytest`.",
             "why": "These are exactly the CI checks. Fixing them locally takes seconds; in CI it takes a 6-minute round trip."},
            {"title": "Push and open the PR", "timestamp": "01:12",
             "action": "Push with `git push -u origin HEAD`, open the link GitHub prints, and fill in the PR template: what, why, how to test.",
             "why": "Reviewers approve faster when they know how to test it."},
            {"title": "Read a red check", "timestamp": "01:50",
             "action": "If a check fails, click Details, scroll to the first red line, fix it locally and push again. Never re-run without a change.",
             "why": "Re-running hides flaky tests and wastes CI minutes."},
            {"title": "Ask for review", "timestamp": "02:30",
             "action": "Request one reviewer from the CODEOWNERS list and post the PR link in #platform-reviews.",
             "why": "One owner plus a channel post gets a review the same day."},
        ],
        "gotchas": ["Pushing straight to main: it is protected and the push is rejected.",
                    "A ruff version different from CI's: always run it through `uv run`, never a global ruff."],
        "success_criteria": ["All checks on the PR show a green tick",
                             "The PR description has What, Why and How to test",
                             "At least one approving review"],
    }),
    "sample-pb-pytest": ("arjun", {
        "title": "Write and run tests with pytest",
        "goal": "Add tests for new code the way this codebase expects, so CI keeps catching regressions.",
        "role_hint": "Python Developer", "tools": ["pytest", "FastAPI TestClient", "coverage"],
        "skills": ["pytest fixtures", "Testing FastAPI endpoints", "Test coverage"],
        "estimated_minutes": 25, "narration_language": "English",
        "steps": [
            {"title": "Find the matching test file", "timestamp": "00:06",
             "action": "Tests mirror the code: `app/orders/service.py` is tested in `tests/orders/test_service.py`.",
             "why": "Everyone knows where to look, and pytest finds files named test_*.py automatically."},
            {"title": "Use the shared fixtures", "timestamp": "00:35",
             "action": "Take `client` and `db_session` as test arguments; they come from `tests/conftest.py`.",
             "why": "The fixtures give each test a clean database, so tests never depend on each other."},
            {"title": "Test the endpoint, not the internals", "timestamp": "01:20",
             "action": "Call `client.get('/orders/1')` and assert on the status code and the JSON body.",
             "why": "Tests that use the public API survive refactors."},
            {"title": "Run one test while you work", "timestamp": "02:02",
             "action": "Run `uv run pytest tests/orders -k get_order -x` for a fast loop, then the whole suite before pushing.",
             "why": "The full suite takes 2 minutes; one test takes 2 seconds."},
            {"title": "Check the coverage", "timestamp": "02:35",
             "action": "Run `uv run pytest --cov=app --cov-report=term-missing` and look at the Missing column for your file.",
             "why": "CI fails a PR that lowers total coverage below 85%."},
        ],
        "gotchas": ["Creating your own database session in a test instead of using `db_session`: data leaks into other tests.",
                    "Asserting only the status code: a 200 with the wrong body still passes."],
        "success_criteria": ["pytest output shows all tests passed", "Coverage of the changed file is 85% or more"],
    }),
}

ROLE_DESCRIPTION = ("Python Developer joining the Platform team. Builds FastAPI services, writes tests with pytest, "
                    "opens and reviews pull requests on GitHub, and deploys to staging with Docker.")

ROLE_TASKS = [
    {"task": "Set up the local Python dev environment", "importance": 3, "playbook_id": "sample-pb-env",
     "why_needed": "Every other task starts from a working local setup.", "suggested_recorder": "Tech lead", "needed_by_day": 1},
    {"task": "Open a pull request that passes CI", "importance": 3, "playbook_id": "sample-pb-pr",
     "why_needed": "The role opens and reviews pull requests on GitHub every day.", "suggested_recorder": "Tech lead", "needed_by_day": 2},
    {"task": "Write and run tests with pytest", "importance": 2, "playbook_id": "sample-pb-pytest",
     "why_needed": "The role writes tests with pytest for every change.", "suggested_recorder": "Senior engineer", "needed_by_day": 2},
    {"task": "Deploy a service to staging with Docker", "importance": 3, "playbook_id": "",
     "why_needed": "The role deploys to staging with Docker; a wrong deploy blocks the whole team's testing.",
     "suggested_recorder": "DevOps engineer", "needed_by_day": 3},
    {"task": "Run a database migration with Alembic", "importance": 2, "playbook_id": "",
     "why_needed": "FastAPI services change their tables through migrations, and a bad one can lose data.",
     "suggested_recorder": "Senior engineer", "needed_by_day": 4},
    {"task": "Debug an error from the production logs", "importance": 2, "playbook_id": "",
     "why_needed": "New developers join the on-call rotation after their first month.",
     "suggested_recorder": "On-call engineer", "needed_by_day": 5},
]

ITEMS = [
    {"day": 1, "kind": "setup", "playbook_id": "", "title": "Get GitHub, Slack and VPN access",
     "detail": "Ask IT for the GitHub org invite, the #platform channels and VPN, then add your SSH key to GitHub.",
     "stuck_example": "GitHub says Permission denied (publickey) when I clone"},
    {"day": 1, "kind": "learn", "playbook_id": "sample-pb-env", "title": "Learn: set up the dev environment",
     "detail": "Watch Priya's recording and follow along on your laptop.",
     "stuck_example": "uv sync says Python 3.12 is not installed"},
    {"day": 1, "kind": "practice", "playbook_id": "sample-pb-env", "title": "Practice: run the service locally",
     "detail": "Get the tests green and the /health endpoint answering on your own machine.",
     "stuck_example": "Every test fails with connection refused"},
    {"day": 2, "kind": "learn", "playbook_id": "sample-pb-pr", "title": "Learn: open a pull request that passes CI",
     "detail": "Watch how the team branches, runs the checks and asks for review.",
     "stuck_example": "I don't know which reviewer to pick"},
    {"day": 2, "kind": "practice", "playbook_id": "sample-pb-pr", "title": "Practice: fix a good-first-issue and open a PR",
     "detail": "Pick an issue labelled good-first-issue, fix it and get the PR's checks green.",
     "stuck_example": "CI fails on ruff but the code runs fine locally"},
    {"day": 3, "kind": "learn", "playbook_id": "sample-pb-pytest", "title": "Learn: write tests with pytest",
     "detail": "Watch Arjun's walkthrough of the fixtures and how to test an endpoint.",
     "stuck_example": "My test passes alone but fails in the full suite"},
    {"day": 3, "kind": "practice", "playbook_id": "sample-pb-pytest", "title": "Practice: add tests for the /orders endpoint",
     "detail": "Write tests for GET /orders/{id}, including the 404 case, and keep coverage above 85%.",
     "stuck_example": "Coverage report doesn't show my new file"},
    {"day": 3, "kind": "meet", "playbook_id": "", "title": "Shadow the DevOps engineer on a staging deploy",
     "detail": "Nobody has recorded this yet. Sit with the DevOps engineer for the afternoon deploy and take notes.",
     "stuck_example": "The deploy is waiting for an approval I can't give"},
    {"day": 4, "kind": "meet", "playbook_id": "", "title": "Pair with a senior engineer on an Alembic migration",
     "detail": "Not recorded yet. Pair on a real migration: generate it, review it, run it on staging.",
     "stuck_example": "alembic revision --autogenerate creates an empty file"},
    {"day": 5, "kind": "meet", "playbook_id": "", "title": "Walk through production logs with the on-call engineer",
     "detail": "Not recorded yet. Trace one real error from the alert to the line of code.",
     "stuck_example": "I can't find the request ID in the logs"},
    {"day": 5, "kind": "practice", "playbook_id": "sample-pb-pr", "title": "Practice: ship your first PR to staging",
     "detail": "Get your good-first-issue PR approved and merged, then watch it reach staging.",
     "stuck_example": "My PR has been waiting for review for a day"},
]


def build(now: float) -> dict:
    """Every sample document, as {collection: {doc_id: doc}}. Coverage and gaps are filled in by the API."""
    ago = lambda days, hours=0: now - days * DAY - hours * 3600
    playbooks = {pid: {**pb, "created_at": ago(9, i), "created_by": TEAM[who], "source": {"type": "sample"}, "sample": True}
                 for i, (pid, (who, pb)) in enumerate(PLAYBOOKS.items())}
    log = [
        {"at": ago(2, 6), "item_index": 0, "outcome": "done", "note": "", "by": NEW_HIRE},
        {"at": ago(2, 5), "item_index": 1, "outcome": "stuck", "by": NEW_HIRE,
         "note": "uv sync says Python 3.12 is not installed, but python3 --version shows 3.12",
         "hint": "uv manages its own Pythons. Run `uv python install 3.12` first (step 2), then `uv sync` again."},
        {"at": ago(2, 4), "item_index": 1, "outcome": "done", "note": "", "by": NEW_HIRE},
        {"at": ago(2, 2), "item_index": 2, "outcome": "done", "verified": True, "note": "(screenshot check passed)", "by": NEW_HIRE},
        {"at": ago(1, 5), "item_index": 3, "outcome": "done", "note": "", "by": NEW_HIRE},
        {"at": ago(0, 3), "item_index": 4, "outcome": "stuck", "by": NEW_HIRE,
         "note": "CI fails on ruff with 'I001 import block is unsorted' but ruff passes on my laptop",
         "hint": "Your laptop may have a different ruff. Run it through `uv run ruff check --fix .` so you use the version CI uses."},
    ]
    status = ["done", "done", "done", "done", "stuck"] + [""] * (len(ITEMS) - 5)
    plan = {"role": "Python Developer", "role_description": ROLE_DESCRIPTION, "days": 5,
            "summary": "Day 1 gets John's laptop running the service; days 2 and 3 are real pull requests and tests. "
                       "Deploys, migrations and production logs aren't recorded yet, so John shadows the people who know them.",
            "role_tasks": [dict(t) for t in ROLE_TASKS], "items": [dict(i) for i in ITEMS],
            "progress": {"status": status, "current": 4, "log": log, "updated_at": ago(0, 3)},
            "assigned_to": NEW_HIRE, "assigned_at": ago(3), "created_at": ago(3, 1), "created_by": TEAM["manager"], "sample": True}
    requests = {"sample-req-staging": {
        "plan_id": PLAN_ID, "plan_role": "Python Developer", "assigned_to": NEW_HIRE,
        "topic": "Deploy a service to staging with Docker", "why_needed": ROLE_TASKS[3]["why_needed"], "needed_by_day": 3,
        "importance": 3, "expert_email": "ravi@example.com", "status": "open",
        "created_by": TEAM["manager"], "created_at": ago(1, 2), "sample": True}}
    return {"playbooks": playbooks, "plans": {PLAN_ID: plan}, "requests": requests}
