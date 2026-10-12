"""Login check: API calls (except /api/health) must carry a Firebase ID token.

Clients sign the user in with Google through Firebase Auth and send
`Authorization: Bearer <ID token>`. The web app does this; any other client can too.

AUTH_REQUIRED=1 -> tokens are required and verified (set on Cloud Run)
otherwise       -> local dev: requests without a token act as a 'dev' user
"""
import logging
import os

from fastapi import Header, HTTPException

log = logging.getLogger("auth")

AUTH_REQUIRED = os.getenv("AUTH_REQUIRED") == "1"
# Not GOOGLE_CLOUD_PROJECT: gemini.py uses that to switch to Vertex AI.
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")

DEV_USER = {"uid": "dev", "email": "dev@localhost", "name": "Local developer"}
_app = None


def _firebase_app():
    global _app
    if _app is None:
        import firebase_admin

        _app = firebase_admin.initialize_app(options={"projectId": PROJECT_ID} if PROJECT_ID else None)
    return _app


def current_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: returns {uid, email, name} of the signed-in user, or raises 401."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        if AUTH_REQUIRED:
            raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
        return DEV_USER

    from firebase_admin import auth

    try:
        claims = auth.verify_id_token(token, app=_firebase_app())
    except Exception as e:
        log.warning("token rejected: %s", e)
        raise HTTPException(401, "Invalid or expired sign-in, please sign in again",
                            headers={"WWW-Authenticate": "Bearer"}) from e
    return {"uid": claims["uid"], "email": claims.get("email", ""), "name": claims.get("name", "")}
