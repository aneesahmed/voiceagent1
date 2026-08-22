"""app/gemini_client.py -- shared, lazily-(re)created Gemini client.

chat_engine.py/stt.py/tts.py all previously built their own
`genai.Client(api_key=settings.GEMINI_API_KEY)` once at import time, which
meant updating GEMINI_API_KEY at runtime (e.g. from the Settings panel,
see app/settings_routes.py) would silently keep using the stale key until
a full process restart. get_client() instead caches by key value and
transparently rebuilds when settings.GEMINI_API_KEY changes, so a saved
Settings-panel update takes effect on the very next call/transcription.
"""
from google import genai

from app.config import settings

_client: genai.Client | None = None
_client_key: str | None = None


def get_client() -> genai.Client:
    global _client, _client_key
    if _client is None or _client_key != settings.GEMINI_API_KEY:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
        _client_key = settings.GEMINI_API_KEY
    return _client
