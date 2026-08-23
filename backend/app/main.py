"""app/main.py -- FastAPI app: HTTP endpoints for text-only testing, plus
the /audio websocket for real-time voice.

Endpoints:
    GET  /health   -- liveness check
    POST /chat     -- text-only chat, for testing chat_engine.py without
                      needing audio or a browser at all
    WS   /audio    -- real-time voice: client streams raw PCM16 frames
                      continuously (even while the assistant is replying,
                      to support barge-in), sends
                      {"event": "end_of_turn"} when done speaking. Server
                      streams the reply (PCM16 frames followed by
                      {"event": "reply_end"}), or {"event": "interrupted"}
                      if the caller barged in at any point.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.basic_auth_middleware import BasicAuthMiddleware
from app.call_engine import greet, process_turn
from app.chat_engine import ChatEngine
from app.integrations import twilio_client_calling, twilio_voice, whatsapp
from app.kb_routes import router as kb_router
from app.patients_db import init_db
from app.patients_routes import router as patients_router
from app.personas import DEFAULT_PERSONA_KEY, PERSONA_REGISTRY, get_persona
from app.settings_routes import router as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("initializing patients database...")
    await asyncio.to_thread(init_db)
    yield


app = FastAPI(title="Voice AI Agent Platform", lifespan=lifespan)

# Frontend runs on a different port during dev (Vite default 5173) -- allow it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to the real frontend origin before production
    allow_methods=["*"],
    allow_headers=["*"],
)

# App-wide HTTP Basic Auth -- no-op unless BASIC_AUTH_USERNAME/PASSWORD are
# both set in .env (see config.py). Added after CORS so it runs outermost
# (Starlette wraps last-added middleware around everything else), rejecting
# unauthenticated requests before they reach any route logic.
app.add_middleware(BasicAuthMiddleware)


# --- consistent { "data": ..., "error": ... } envelope for every error, per
# the patient-registration API spec (app/patients_routes.py) ---

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"data": None, "error": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # FastAPI computes exc.errors() internally (we can't re-call pydantic's
    # .errors(include_context=False) ourselves), and it can include a 'ctx'
    # dict holding the raw exception object -- not JSON-serializable. Strip
    # the non-serializable keys before responding.
    errors = [{k: v for k, v in err.items() if k not in ("ctx", "url")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"data": None, "error": errors})


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- self-contained browser test page (vanilla JS, no npm/build step) ---
# See CLAUDE.md decision #19: the separate frontend/ (Vite+React) app hit a
# broken local npm toolchain (missing Windows shims for tsc/vite), so this
# single static page -- served directly by FastAPI at "/" -- became the
# primary way to test a call end-to-end. Mirrors frontend/src/CallAdapter.ts's
# exact /audio protocol and thresholds; frontend/ is untouched and still
# works once its npm install is fixed.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
async def landing():
    """Home page: 3 tiles (Configuration & Setup, Web Testing, Twilio Testing)."""
    return FileResponse(_STATIC_DIR / "landing.html")


@app.get("/web-testing")
async def web_testing_page():
    """Browser mic call UI + patients panel -- was served at '/' before this became the landing page."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/twilio-test")
async def twilio_test_page():
    """Browser WebRTC test caller -- see app/integrations/twilio_client_calling.py."""
    return FileResponse(_STATIC_DIR / "twilio_test_call.html")


@app.get("/settings-ui")
async def settings_page():
    """Credentials/setup UI -- see app/settings_routes.py."""
    return FileResponse(_STATIC_DIR / "settings.html")


# --- persona registry, for the frontend's persona-picker landing page ---

class PersonaOut(BaseModel):
    key: str
    label: str
    description: str
    available: bool


@app.get("/personas", response_model=list[PersonaOut])
async def personas():
    return [
        PersonaOut(key=p.key, label=p.label, description=p.description, available=p.available)
        for p in PERSONA_REGISTRY.values()
    ]


# --- text-only chat endpoint, for testing the engine without audio ---

class ChatRequest(BaseModel):
    message: str
    persona: str = DEFAULT_PERSONA_KEY
    # Mimics Twilio Caller ID (ANI) for testing over plain HTTP -- see
    # ChatEngine's caller_ani param and CLAUDE.md decision #21.
    caller_number: str | None = None


class ChatResponse(BaseModel):
    reply: str
    end_call: bool = False


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Fresh ChatEngine per request -- no multi-turn memory over plain
    HTTP yet. Good enough for testing single-turn KB grounding before the
    UI exists; /audio below keeps real per-call session memory."""
    persona = get_persona(req.persona)
    if persona is None or not persona.available:
        raise HTTPException(status_code=400, detail=f"Persona '{req.persona}' is not available")

    engine = ChatEngine(persona=req.persona, caller_ani=req.caller_number)
    reply = engine.generate_reply(req.message)
    return ChatResponse(reply=reply.text, end_call=reply.end_call)


# --- real-time voice websocket ---

app.include_router(kb_router)
app.include_router(patients_router)
app.include_router(settings_router)
app.include_router(twilio_voice.router)
app.include_router(twilio_client_calling.router)
app.include_router(whatsapp.router)


@app.websocket("/audio")
async def audio_ws(ws: WebSocket):
    await ws.accept()
    call_id = id(ws)

    persona_key = ws.query_params.get("persona", DEFAULT_PERSONA_KEY)
    persona = get_persona(persona_key)
    if persona is None or not persona.available:
        logger.info("[%s] rejecting call: persona '%s' is not available", call_id, persona_key)
        await ws.send_text(json.dumps({"event": "error", "message": f"Persona '{persona_key}' is not available"}))
        await ws.close(code=4004)
        return

    # Browser test UI asks for this up front and passes it here to mimic
    # Twilio Caller ID (ANI) -- see CLAUDE.md decision #21.
    caller_number = ws.query_params.get("caller_number") or None
    engine = ChatEngine(persona=persona_key, caller_ani=caller_number, call_id=call_id)
    audio_buffer = bytearray()
    interrupt_event = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_index = 0  # 0 = greeting, 1/2/3... = each subsequent caller turn -- logged so a pasted log reads sequentially

    logger.info(
        "[%s] call connected (persona=%s, caller_number=%s)", call_id, persona_key, caller_number
    )

    async def send_frame(data: bytes) -> None:
        await ws.send_bytes(data)

    async def send_event(event: dict) -> None:
        logger.info("[%s] -> client event: %s", call_id, event)
        await ws.send_text(json.dumps(event))
        if event.get("event") == "call_ended_by_agent":
            # The agent used the end_call tool (see chat_engine.py) and its
            # farewell has already finished playing -- actually hang up.
            await ws.close(code=1000)

    # The agent speaks first -- run as a turn_task exactly like a normal
    # reply so the receive loop's existing barge-in handling (below)
    # applies to the greeting for free.
    turn_task = asyncio.create_task(
        greet(
            engine=engine,
            call_id=call_id,
            interrupt_event=interrupt_event,
            send_frame=send_frame,
            send_event=send_event,
            turn_index=turn_index,
        )
    )

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.disconnect":
                logger.info("[%s] client disconnected (code=%s)", call_id, message.get("code"))
                break

            if message.get("bytes") is not None:
                audio_buffer += message["bytes"]

                if turn_task is not None and not turn_task.done():
                    if not interrupt_event.is_set():
                        interrupt_event.set()
                        logger.info("[%s] barge-in detected, interrupting current turn", call_id)
                continue

            if message.get("text") is not None:
                try:
                    event = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("[%s] received malformed text frame: %r", call_id, message["text"])
                    continue

                logger.info("[%s] event received: %s", call_id, event)

                if event.get("event") == "end_of_turn":
                    if turn_task is not None and not turn_task.done():
                        logger.info("[%s] end_of_turn ignored, a turn is already in progress", call_id)
                        continue

                    if not audio_buffer:
                        logger.info("[%s] end_of_turn with no buffered audio, ignoring", call_id)
                        continue

                    audio_to_process = bytes(audio_buffer)
                    audio_buffer.clear()
                    interrupt_event = asyncio.Event()
                    turn_index += 1
                    turn_task = asyncio.create_task(
                        process_turn(
                            engine=engine,
                            call_id=call_id,
                            audio_bytes=audio_to_process,
                            interrupt_event=interrupt_event,
                            send_frame=send_frame,
                            send_event=send_event,
                            turn_index=turn_index,
                        )
                    )

    except WebSocketDisconnect:
        logger.info("[%s] call disconnected", call_id)
    finally:
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        logger.info("[%s] call ended", call_id)
