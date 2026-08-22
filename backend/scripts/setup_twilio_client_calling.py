"""scripts/setup_twilio_client_calling.py -- one-time setup: creates the
Twilio TwiML Application the browser WebRTC test caller dials into (see
app/integrations/twilio_client_calling.py). Safe to re-run -- updates the
existing app (by friendly_name) instead of creating duplicates.

The Settings panel's "Create/Update TwiML App" button does the same thing
at runtime (app/settings_routes.py) -- this script is for the CLI/one-off
case, both call the same app.integrations.twilio_client_calling.ensure_twiml_app().

Usage (from backend/):
    uv run python scripts/setup_twilio_client_calling.py

Prints the resulting TwiML App SID -- copy it into TWILIO_TWIML_APP_SID in
.env and restart the backend (or just use the Settings panel instead).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.integrations.twilio_client_calling import ensure_twiml_app  # noqa: E402


def main() -> None:
    missing = [
        name
        for name, value in [
            ("TWILIO_ACCOUNT_SID", settings.TWILIO_ACCOUNT_SID),
            ("TWILIO_API_KEY_SID", settings.TWILIO_API_KEY_SID),
            ("TWILIO_API_KEY_SECRET", settings.TWILIO_API_KEY_SECRET),
            ("PUBLIC_BASE_URL", settings.PUBLIC_BASE_URL),
        ]
        if not value
    ]
    if missing:
        print(f"Missing required .env vars: {', '.join(missing)}")
        sys.exit(1)

    sid = ensure_twiml_app(settings.PUBLIC_BASE_URL)
    print(f"TwiML App ready: {sid}")
    print(f"\nSet this in backend/.env:\nTWILIO_TWIML_APP_SID={sid}")


if __name__ == "__main__":
    main()
