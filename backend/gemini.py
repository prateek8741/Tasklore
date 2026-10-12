"""Gemini client: works with an AI Studio API key or with Vertex AI on Google Cloud.

- GEMINI_API_KEY set           -> Gemini Developer API (AI Studio key)
- GOOGLE_CLOUD_PROJECT set     -> Vertex AI (uses the Cloud Run service account)
- neither                      -> MOCK mode (returns sample data, handy for frontend work)

GEMINI_MODELS is a comma-separated fallback list. Each call tries them in order,
so one model being retired or flaky does not take the app down.
"""
import io
import logging
import os
import time

from google import genai
from google.genai import types

log = logging.getLogger("gemini")

MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-flash-latest,gemini-3-flash-preview").split(",")
    if m.strip()
]

_client = None
_client_ready = False


class GeminiError(RuntimeError):
    """Every model in the fallback list failed; the API turns this into a readable 502."""


def get_client():
    global _client, _client_ready
    if _client_ready:
        return _client
    key = os.getenv("GEMINI_API_KEY", "").strip()  # a pasted key may carry a stray newline
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if key:
        _client = genai.Client(api_key=key)
    elif project:
        _client = genai.Client(
            vertexai=True,
            project=project,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        )
    _client_ready = True
    return _client


def is_mock() -> bool:
    return get_client() is None


def mode() -> str:
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "ai-studio-key"
    if os.getenv("GOOGLE_CLOUD_PROJECT"):
        return "vertex-ai"
    return "mock"


def generate(contents, schema=None, system: str | None = None):
    """Call Gemini with model fallback. Returns (model_used, response)."""
    client = get_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
    )
    last_err = None
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            return model, resp
        except Exception as e:  # try the next model
            log.warning("model %s failed: %s", model, e)
            last_err = e
    raise GeminiError(f"All Gemini models failed. Last error: {last_err}")


INLINE_VIDEO_LIMIT = 15 * 1024 * 1024  # bigger videos would push the request past Gemini's inline limit


def video_part(data: bytes, mime_type: str):
    """Small videos travel inside the request; bigger ones go through the Files API first."""
    if len(data) <= INLINE_VIDEO_LIMIT:
        return types.Part.from_bytes(data=data, mime_type=mime_type)
    client = get_client()
    f = client.files.upload(file=io.BytesIO(data), config=types.UploadFileConfig(mime_type=mime_type))
    deadline = time.time() + 120
    while f.state and f.state.name == "PROCESSING":  # Gemini prepares the video before it can be used
        if time.time() > deadline:
            raise GeminiError("Gemini took too long to process the video. Try a shorter recording.")
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state and f.state.name == "FAILED":
        raise GeminiError("Gemini could not read this video. Try recording again (WebM or MP4).")
    return f  # uploaded files are deleted by Gemini automatically after 48 hours
