"""Gemini client: works with an AI Studio API key or with Vertex AI on Google Cloud.

- GEMINI_API_KEY set           -> Gemini Developer API (AI Studio key)
- GOOGLE_CLOUD_PROJECT set     -> Vertex AI (uses the Cloud Run service account)
- neither                      -> MOCK mode (returns sample data, handy for frontend work)

GEMINI_MODELS is a comma-separated fallback list. Each call tries them in order,
so one model being retired or flaky does not take the app down.
"""
import datetime
import io
import logging
import os
import time
import warnings

from google import genai
from google.genai import types

log = logging.getLogger("gemini")
warnings.filterwarnings("ignore", message=".*ephemeral token support is experimental.*")

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


# ---------- voice ----------
# Voice uses Gemini's dedicated speech models (none of them gemini-2.5-*).
TTS_MODELS = [m.strip() for m in os.getenv("GEMINI_TTS_MODELS", "gemini-3.8-flash-lite-tts,gemini-3.8-flash-tts").split(",") if m.strip()]
LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
VOICE = os.getenv("GEMINI_VOICE", "Kore")
_token_client = None  # Live API tokens are minted on the v1alpha API


def _voice_config():
    return types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)))


def speak(text: str) -> tuple[bytes, str]:
    """Text -> spoken audio (WAV). Any language Gemini TTS supports, Hindi included."""
    config = types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=_voice_config())
    last_err = None
    for model in TTS_MODELS:
        try:
            part = get_client().models.generate_content(model=model, contents=text, config=config).candidates[0].content.parts[0]
            return part.inline_data.data, part.inline_data.mime_type or "audio/wav"
        except Exception as e:
            log.warning("tts model %s failed: %s", model, e)
            last_err = e
    raise GeminiError(f"Couldn't create speech. Last error: {last_err}")


def live_token(system_instruction: str, minutes: int = 30) -> dict:
    """A single-use, short-lived token for one Live API session, opened by the browser directly.
    The coach's instructions and settings are locked into the token, so the browser can't change them,
    and the real API key never leaves the server."""
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        speech_config=_voice_config(),
        input_audio_transcription=types.AudioTranscriptionConfig(),   # live captions of what the new hire says
        output_audio_transcription=types.AudioTranscriptionConfig(),  # ... and of what the coach says
        # Without compression, audio + screen sessions stop after 2 minutes.
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )
    global _token_client
    if _token_client is None:   # keep the client alive: a discarded client closes its connection
        _token_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", "").strip(), http_options={"api_version": "v1alpha"})
    token = _token_client.auth_tokens.create(config=types.CreateAuthTokenConfig(
        uses=1, expire_time=now + datetime.timedelta(minutes=minutes), new_session_expire_time=now + datetime.timedelta(minutes=1),
        live_connect_constraints=types.LiveConnectConstraints(model=LIVE_MODEL, config=config)))
    return {"token": token.name, "model": LIVE_MODEL, "expires_in_s": minutes * 60}


TRANSCRIBE_SYSTEM = """Transcribe the speech in this audio exactly, in the language spoken (English, Hindi, Hinglish or other).
If there is no clear human speech (silence, noise, a tone, music), return an empty string. Never guess or invent words."""


def transcribe(data: bytes, mime_type: str) -> str:
    """Speech -> text. Deliberately given no task context, so it can't 'hear' a plausible question in noise."""
    from schemas import Transcript
    _, resp = generate([audio_part(data, mime_type), "Transcribe this."], schema=Transcript, system=TRANSCRIBE_SYSTEM)
    return (resp.parsed.text if getattr(resp, "parsed", None) else Transcript.model_validate_json(resp.text).text).strip()


def audio_part(data: bytes, mime_type: str):
    return types.Part.from_bytes(data=data, mime_type=mime_type)


def youtube_part(url: str):
    """Gemini watches a public YouTube video straight from its URL; nothing is downloaded."""
    return types.Part.from_uri(file_uri=url, mime_type="video/*")


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
