"""app/config.py -- centralized settings, loaded once from .env.

Every other module imports `settings` from here rather than reading
os.environ directly, so there's exactly one place that knows about env
var names and defaults.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
KB_DIR = BASE_DIR / "kb"

# Placeholder text the Settings panel (app/settings_routes.py) writes to
# .env for a field instead of leaving it blank when someone clears it --
# a human glancing at .env sees a reminder of what belongs there instead
# of an unexplained empty value. Loading below treats a value that
# exactly matches its field's placeholder as unset, so this round-trips
# correctly: clear in the UI -> placeholder written to .env -> restart ->
# still reads as empty/not-configured, not as a literal credential.
PLACEHOLDERS: dict[str, str] = {
    "GEMINI_API_KEY": "your gemini api key",
    "TWILIO_ACCOUNT_SID": "your twilio account sid",
    "TWILIO_AUTH_TOKEN": "your twilio auth token",
    "TWILIO_API_KEY_SID": "your twilio api key sid",
    "TWILIO_API_KEY_SECRET": "your twilio api key secret",
    "TWILIO_PHONE_NUMBER": "your twilio phone number",
}


def _env(key: str) -> str:
    value = os.getenv(key, "")
    return "" if value == PLACEHOLDERS.get(key) else value


class Settings:
    # -- Gemini API --
    GEMINI_API_KEY: str = _env("GEMINI_API_KEY")

    CHAT_MODEL: str = os.getenv("CHAT_MODEL", "gemini-2.5-flash")
    STT_MODEL: str = os.getenv("STT_MODEL", "gemini-2.5-flash")
    TTS_MODEL: str = os.getenv("TTS_MODEL", "gemini-2.5-flash-preview-tts")
    TTS_VOICE: str = os.getenv("TTS_VOICE", "Aoede")

    # -- Knowledge base --
    KB_DIR: Path = KB_DIR

    # -- Patient registration database (SQLite -- see app/patients_db.py) --
    DB_PATH: Path = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "patients.db")))

    # -- Server --
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # -- Audio --
    AUDIO_SAMPLE_RATE: int = 8000  # every transport in this project uses 8kHz/16-bit/mono PCM

    # -- Twilio (phone) integration -- optional, unset until a real Twilio
    # account/number is configured. See app/integrations/twilio_voice.py.
    TWILIO_ACCOUNT_SID: str = _env("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = _env("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: str = _env("TWILIO_PHONE_NUMBER")
    # Publicly reachable base URL (e.g. a Cloudflare/ngrok tunnel in dev)
    # Twilio uses to reach /twilio/voice, /twilio/media-stream, and (for the
    # browser test caller) /twilio/browser-call-voice.
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")

    # -- Twilio API Key (Account > Keys & Credentials > API keys & tokens)
    # + a TwiML App -- used only by the browser WebRTC test caller (see
    # app/integrations/twilio_client_calling.py) to generate short-lived
    # Access Tokens. Not the same as TWILIO_ACCOUNT_SID/AUTH_TOKEN above.
    TWILIO_API_KEY_SID: str = _env("TWILIO_API_KEY_SID")
    TWILIO_API_KEY_SECRET: str = _env("TWILIO_API_KEY_SECRET")
    TWILIO_TWIML_APP_SID: str = os.getenv("TWILIO_TWIML_APP_SID", "")

    # -- WhatsApp Business (Meta Cloud API) integration -- optional, unset
    # until a real Meta app/number is configured. See
    # app/integrations/whatsapp.py.
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

    @property
    def twilio_configured(self) -> bool:
        # PUBLIC_BASE_URL is no longer required here -- it's derived
        # per-request now (see app/request_utils.py); this only gates on
        # the caller having actually set up Twilio credentials at all.
        return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN)

    @property
    def twilio_client_calling_configured(self) -> bool:
        return bool(
            self.TWILIO_ACCOUNT_SID
            and self.TWILIO_API_KEY_SID
            and self.TWILIO_API_KEY_SECRET
            and self.TWILIO_TWIML_APP_SID
            and self.TWILIO_PHONE_NUMBER
        )

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.WHATSAPP_ACCESS_TOKEN and self.WHATSAPP_PHONE_NUMBER_ID)


settings = Settings()

if not settings.GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )