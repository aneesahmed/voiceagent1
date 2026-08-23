"""app/basic_auth_middleware.py -- app-wide HTTP Basic Auth, gating every
route (pages, REST API, the /audio websocket) except the handful Twilio,
Meta, and Railway call directly and can't attach custom credentials to.

Raw ASGI middleware rather than Starlette's BaseHTTPMiddleware because it
needs to also cover the /audio and /twilio/media-stream websocket
handshakes, which BaseHTTPMiddleware (HTTP-request-only) can't see.
Disabled entirely (no-op) when BASIC_AUTH_USERNAME/PASSWORD aren't both
set in .env -- see config.py's basic_auth_enabled -- so this is opt-in and
doesn't break an existing deployment that hasn't configured it yet.
"""
import base64
import binascii
import hmac

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

# Called directly by Twilio/Meta's servers (webhooks) or Railway's own
# healthcheck -- none of these can send our Basic Auth credentials, so
# they must stay open regardless of BASIC_AUTH_* being configured.
PUBLIC_PATHS = {
    "/health",
    "/twilio/voice",
    "/twilio/media-stream",
    "/twilio/browser-call-voice",
    "/whatsapp/webhook",
}


def _is_authorized(scope: Scope) -> bool:
    header_value = next(
        (v for k, v in scope["headers"] if k == b"authorization"),
        None,
    )
    if header_value is None or not header_value.startswith(b"Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[len(b"Basic ") :]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    return hmac.compare_digest(username, settings.BASIC_AUTH_USERNAME) and hmac.compare_digest(
        password, settings.BASIC_AUTH_PASSWORD
    )


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if not settings.basic_auth_enabled or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        if _is_authorized(scope):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await receive()  # consume the pending "websocket.connect" event
            await send({"type": "websocket.close", "code": 4001})
            return

        response = Response(
            content="Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Voice Agent"'},
        )
        await response(scope, receive, send)
