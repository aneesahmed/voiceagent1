"""app/request_utils.py -- derives this server's own public base URL from
the incoming request, instead of relying on a hardcoded PUBLIC_BASE_URL.

Why: PUBLIC_BASE_URL used to be the only source for building the Twilio
Media Stream URL and the webhook URL shown on /settings-ui. That's brittle
across environments -- a local Cloudflare/ngrok tunnel URL changes every
restart, and a Cloud Run URL isn't even known until after deploy (and can
change between revisions/custom-domain setups). Whenever this code is
running a request from Twilio (or a browser hitting /settings-ui) at all,
that request already arrived over the correct public hostname -- Cloud
Run and every tunnel we've used both forward the real external Host (and
terminate TLS in front of us, hence checking X-Forwarded-Proto rather
than trusting request.url.scheme, which reflects our own plain-HTTP
uvicorn process). So deriving per-request is self-correcting with zero
config, and settings.PUBLIC_BASE_URL becomes an optional override for
contexts with no request at all (the CLI setup script) or the rare case
where forwarded headers aren't trustworthy.
"""
from fastapi import Request

from app.config import settings


def derive_base_url(request: Request) -> str:
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip("/")

    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return settings.PUBLIC_BASE_URL.rstrip("/")  # no request context worth trusting -- caller must handle ""

    # Cloud Run, Cloudflare Tunnel, and ngrok all terminate TLS in front of
    # our plain-HTTP uvicorn process and forward this header; only trust
    # request.url.scheme (which would say "http") if there's truly nothing
    # else to go on.
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{proto}://{host}"
