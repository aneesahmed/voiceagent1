"""app/settings_routes.py -- lets the Settings panel in the web UI manage
credentials instead of hand-editing backend/.env.

GET /settings returns the current state (secrets masked to a short
preview, never the full value) so the UI can show what's already set.
PUT /settings updates the in-memory `settings` singleton immediately (so
Twilio-related changes take effect on the very next request -- everything
in app/integrations/twilio_voice.py and twilio_client_calling.py reads
`settings.X` fresh per-request, nothing is cached at import time) and
persists to .env via app/env_file.py so it survives a restart. The one
exception is GEMINI_API_KEY: it's cached in a shared client (see
app/gemini_client.py) that also checks for staleness per-call, so it picks
up a change on the next transcription/reply without a restart either.

POST /settings/setup-twiml-app runs the same one-time Twilio setup as
scripts/setup_twilio_client_calling.py, from the UI, so the "browser test
caller" (/twilio-test) works without a terminal. Both this endpoint and
GET /settings's voice_webhook_url derive the public URL from the request
that's hitting this very endpoint (see app/request_utils.py) rather than
requiring PUBLIC_BASE_URL to be set -- self-correcting if this ever runs
somewhere whose URL isn't known ahead of time, like Cloud Run.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import PLACEHOLDERS, settings
from app.env_file import update_env_file
from app.integrations.twilio_client_calling import ensure_twiml_app
from app.request_utils import derive_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# name -> (is_secret, description). Order here is the order the UI shows them in.
FIELDS: dict[str, tuple[bool, str]] = {
    "GEMINI_API_KEY": (True, "Gemini API key (chat, speech-to-text, text-to-speech)"),
    # Account SID/API Key SID aren't secrets in the sense of granting access
    # on their own (Twilio's docs call them identifiers, not credentials),
    # but masking them anyway keeps a screen-shared/screenshotted Settings
    # page from casually exposing account-identifying values -- display-only,
    # doesn't change what's actually stored or how Twilio calls authenticate.
    "TWILIO_ACCOUNT_SID": (True, "Twilio Account SID (starts with AC)"),
    "TWILIO_AUTH_TOKEN": (True, "Twilio Auth Token (master secret, console.twilio.com account home)"),
    "TWILIO_API_KEY_SID": (True, "Twilio API Key SID (starts with SK) -- for the browser test caller"),
    "TWILIO_API_KEY_SECRET": (True, "Twilio API Key Secret -- for the browser test caller"),
    "TWILIO_PHONE_NUMBER": (False, "Your Twilio phone number, E.164 format e.g. +14843652103"),
    "PUBLIC_BASE_URL": (
        False,
        "Optional override -- normally auto-detected from each request (works behind Cloud Run, any tunnel, etc). Only set this if that detection is ever wrong.",
    ),
    "TWILIO_TWIML_APP_SID": (True, "TwiML App SID for the browser caller -- usually set by the button below, not by hand"),
}


def _preview(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


class SettingsUpdate(BaseModel):
    values: dict[str, str]


@router.get("")
async def get_settings(request: Request):
    fields = {}
    for name, (is_secret, description) in FIELDS.items():
        value = getattr(settings, name)
        fields[name] = {
            "set": bool(value),
            "secret": is_secret,
            "description": description,
            "value": None if (is_secret or not value) else value,
            "preview": _preview(value) if (is_secret and value) else None,
        }

    return {
        "fields": fields,
        "twilio_configured": settings.twilio_configured,
        "twilio_client_calling_configured": settings.twilio_client_calling_configured,
        "voice_webhook_url": derive_base_url(request) + "/twilio/voice",
        "detected_base_url": derive_base_url(request),
    }


@router.put("")
async def update_settings(payload: SettingsUpdate, request: Request):
    unknown = [k for k in payload.values if k not in FIELDS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown setting(s): {', '.join(unknown)}")

    env_updates = {}
    for key, raw_value in payload.values.items():
        value = raw_value.strip()
        setattr(settings, key, value)  # in-memory: genuinely empty when cleared, never the placeholder
        # .env: an explicit, self-documenting placeholder instead of a bare
        # `KEY=` a human would otherwise have to guess the meaning of --
        # config.py's loader recognizes it back as "unset" on next startup.
        env_updates[key] = value if value else PLACEHOLDERS.get(key, "")

    update_env_file(env_updates)
    logger.info("settings updated via UI: %s", ", ".join(payload.values.keys()))
    return await get_settings(request)


@router.post("/setup-twiml-app")
async def setup_twiml_app(request: Request):
    missing = [
        name
        for name in ("TWILIO_ACCOUNT_SID", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET")
        if not getattr(settings, name)
    ]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required setting(s) first: {', '.join(missing)}")

    try:
        sid = ensure_twiml_app(derive_base_url(request))
    except Exception as e:
        logger.exception("failed to create/update TwiML App")
        raise HTTPException(status_code=502, detail=f"Twilio API call failed: {e}")

    settings.TWILIO_TWIML_APP_SID = sid
    update_env_file({"TWILIO_TWIML_APP_SID": sid})
    return {"twiml_app_sid": sid}
