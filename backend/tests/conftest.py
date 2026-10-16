"""Test setup: in-memory store, mock Gemini, no login (X-Dev-User picks the person). No network, no Gemini spend."""
import os
import sys

for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "USE_FIRESTORE", "AUTH_REQUIRED"):
    os.environ.pop(var, None)
os.environ["DEMO_MODE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from store import store  # noqa: E402

PLAN = "sample-plan-john"
STAGING = "Deploy a service to staging with Docker"   # gap with an open request (to ravi@)
ALEMBIC = "Run a database migration with Alembic"     # gap nobody was asked about yet


@pytest.fixture
def client():
    """A fresh sample team for every test."""
    for c in ("users", "invites", "agent_runs"):
        for doc_id in store.list(c):
            store.delete(c, doc_id)
    main.seed_sample_team()
    with TestClient(main.app) as c:
        yield c


def as_role(client, email: str, role: str) -> dict:
    """Headers for `email` acting as `role` (demo mode gives everyone every role)."""
    h = {"X-Dev-User": email}
    assert client.post("/api/me/role", json={"role": role}, headers=h).status_code == 200
    return h
