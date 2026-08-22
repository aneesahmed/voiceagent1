"""app/integrations/twilio_client_calling.py -- browser-based WebRTC test
caller for the Twilio integration, for testing without a real phone.

Twilio's inbound-call restrictions are nonexistent (any number, real or
virtual, can dial in -- trial-account limits only apply to *outbound*
calls Twilio places for you), but if no phone/VoIP app is handy, this
gives an alternative: a browser page using Twilio's Voice SDK places a
WebRTC call that Twilio bridges to a real PSTN call to our own Twilio
number, which then hits /twilio/voice exactly like a genuine inbound call
-- same TwiML, same Media Stream, same ChatEngine path. This is a test
utility only, separate from the actual patient-intake product surface.

Two pieces:
  - POST /twilio/access-token -- issues a short-lived Access Token (via
    the Twilio API Key, not the Account SID/Auth Token) that the browser
    SDK uses to register a Twilio.Device.
  - POST /twilio/browser-call-voice -- the TwiML App's Voice Request URL:
    answered when the Device places a call, and just dials our own Twilio
    number to bridge into the normal inbound flow.

Requires one-time setup (a TwiML App pointed at the second endpoint above)
-- see scripts/setup_twilio_client_calling.py.
"""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.rest import Client as TwilioRestClient

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["twilio-client-calling"])

TWIML_APP_FRIENDLY_NAME = "voiceagent-browser-test-caller"


def ensure_twiml_app(base_url: str) -> str:
    """Creates (or updates, if already created) the TwiML App the browser
    test caller dials into, via the Twilio API -- no console clicking
    needed. Shared by scripts/setup_twilio_client_calling.py (which has no
    request to derive base_url from, so it passes settings.PUBLIC_BASE_URL)
    and the Settings panel's "Create/Update TwiML App" button
    (app/settings_routes.py, which derives it from the request that hit
    /settings-ui -- see app/request_utils.py). Returns the App SID."""
    client = TwilioRestClient(settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET, settings.TWILIO_ACCOUNT_SID)
    voice_url = base_url.rstrip("/") + "/twilio/browser-call-voice"

    existing = next(
        (a for a in client.applications.list() if a.friendly_name == TWIML_APP_FRIENDLY_NAME),
        None,
    )
    if existing:
        app = existing.update(voice_url=voice_url, voice_method="POST")
    else:
        app = client.applications.create(
            friendly_name=TWIML_APP_FRIENDLY_NAME,
            voice_url=voice_url,
            voice_method="POST",
        )
    return app.sid


@router.post("/access-token")
async def access_token():
    if not settings.twilio_client_calling_configured:
        raise HTTPException(
            status_code=503,
            detail="Browser test caller isn't set up yet -- run scripts/setup_twilio_client_calling.py",
        )

    token = AccessToken(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_API_KEY_SID,
        settings.TWILIO_API_KEY_SECRET,
        identity="browser-test-caller",
        ttl=3600,
    )
    token.add_grant(VoiceGrant(outgoing_application_sid=settings.TWILIO_TWIML_APP_SID))
    return {"token": token.to_jwt()}


@router.post("/browser-call-voice")
async def browser_call_voice() -> Response:
    # callerId must be a number this account owns (or a verified number) --
    # using our own Twilio number both as caller and callee bridges this
    # WebRTC call straight into the normal /twilio/voice inbound path.
    number = settings.TWILIO_PHONE_NUMBER
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial callerId="{number}"><Number>{number}</Number></Dial></Response>'
    )
    return Response(content=twiml, media_type="application/xml")
