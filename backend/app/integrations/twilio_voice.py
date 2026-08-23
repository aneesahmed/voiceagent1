"""app/integrations/twilio_voice.py -- Twilio phone-call integration.

Bridges a real phone call (a Twilio number or SIP trunk) to the same
conversation engine used by the browser pseudo-call, via Twilio's Media
Streams (https://www.twilio.com/docs/voice/media-streams). Twilio streams
8kHz/mono mu-law audio -- exactly this project's canonical sample rate
(see CLAUDE.md decision #6), just mu-law-companded instead of linear PCM16,
so mu-law<->PCM16 codec functions are the only transport-specific piece
here. Everything downstream (STT/ChatEngine/TTS, and turn/barge-in logic)
runs through the same app/call_engine.process_turn used by /audio.

NOT CONFIGURED YET (per project decision -- see settings.twilio_configured):
this needs a real Twilio account, a phone number or SIP trunk, and
PUBLIC_BASE_URL set to a publicly reachable HTTPS/WSS URL (e.g. an ngrok
tunnel in dev, since Twilio cannot reach localhost). Until TWILIO_* /
PUBLIC_BASE_URL are set, POST /twilio/voice replies with a TwiML message
saying so instead of connecting a stream. See the frontend's Integrations
panel for the exact setup steps (buy/port a number, point its Voice
webhook at this endpoint, set env vars, restart).

Unlike the browser client, a real caller has no JS running client-side to
detect silence -- so this module does its own lightweight RMS-based
end-of-turn and barge-in detection directly on the incoming PCM16 stream,
mirroring the thresholds frontend/src/CallAdapter.ts uses.
"""
import asyncio
import base64
import json
import logging
import struct
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.call_engine import greet, process_turn
from app.chat_engine import ChatEngine
from app.config import settings
from app.patients_schemas import normalize_us_phone
from app.personas import DEFAULT_PERSONA_KEY
from app.request_utils import derive_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["twilio"])

# --- mu-law (G.711) <-> 16-bit linear PCM -------------------------------
# Python dropped the stdlib audioop module in 3.13 (same reason tts.py
# hand-rolls its own resampling -- see CLAUDE.md decision #6), so this is a
# small from-scratch implementation of the standard G.711 encode/decode.

_BIAS = 0x84
_CLIP = 32635
_DECODE_TABLE = [0, 132, 396, 924, 1980, 4092, 8316, 16764]


def _linear_to_mulaw(sample: int) -> int:
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    sample = min(sample, _CLIP) + _BIAS
    exponent = min((sample >> 7).bit_length(), 7)
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _mulaw_to_linear(byte: int) -> int:
    byte = ~byte & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = _DECODE_TABLE[exponent] + (mantissa << (exponent + 3))
    return -sample if sign else sample


def pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    n = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    return bytes(_linear_to_mulaw(s) for s in samples)


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    samples = [_mulaw_to_linear(b) for b in mulaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def _pcm16_rms(pcm_bytes: bytes) -> float:
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


# Mirrors frontend/src/CallAdapter.ts's thresholds, in int16 units instead
# of normalized floats (float threshold 0.02 * 32768 =~ 655) -- bumped up
# from that browser-mic value for real phone audio specifically, which has
# a genuinely higher noise floor (mu-law quantization noise, carrier-
# injected comfort noise, line static) than a clean digital mic input.
SILENCE_RMS_THRESHOLD = 900
SILENCE_DURATION_S = 1.0
BARGE_IN_DURATION_S = 0.3
# How long RMS must stay ABOVE threshold before we commit to "the caller
# started talking" at all -- mirrors BARGE_IN_DURATION_S's sustained-
# activity gate, which speech-start detection was missing entirely (any
# single loud frame -- a click, a pop, a brief line noise spike -- used to
# flip has_spoken=True immediately, which could turn into a whole
# unwanted turn once things went quiet again for SILENCE_DURATION_S).
SPEECH_START_DURATION_S = 0.2


# --- TwiML webhook -------------------------------------------------------

@router.post("/voice")
async def voice_webhook(request: Request) -> Response:
    if not settings.twilio_configured:
        logger.warning("Twilio /voice webhook hit but TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN are not configured")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say>The voice assistant is not configured yet.</Say></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    # Twilio POSTs the caller's number as `From` (standard Caller ID/ANI --
    # no add-on needed). Threaded through as a <Stream> <Parameter> so the
    # websocket handler below can read it back out of the "start" event's
    # customParameters -- see CLAUDE.md decision #21.
    form = await request.form()
    caller_number = str(form.get("From") or "")

    # Derived from this very request rather than a hardcoded
    # PUBLIC_BASE_URL -- self-correcting across a local tunnel, Cloud Run,
    # or any future host (see CLAUDE.md decision #23/app/request_utils.py).
    stream_url = (
        derive_base_url(request)
        .replace("https://", "wss://")
        .replace("http://", "ws://")
        + "/twilio/media-stream"
    )
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect><Stream url=\"" + stream_url + "\">"
        f'<Parameter name="callerNumber" value="{caller_number}" />'
        "</Stream></Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


# --- Media Streams websocket ---------------------------------------------

@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    call_id = f"twilio:{id(ws)}"
    # Constructed once the "start" event arrives with the caller's number
    # (see below) -- currently persona is always "patient_intake", see
    # app/personas.py.
    engine: ChatEngine | None = None

    audio_buffer = bytearray()
    interrupt_event = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_index = 0  # 0 = greeting, 1/2/3... = each subsequent caller turn
    stream_sid: str | None = None

    has_spoken = False
    silence_started_at: float | None = None
    barge_in_started_at: float | None = None
    speech_started_at: float | None = None

    logger.info("[%s] call connected", call_id)

    async def send_frame(pcm_bytes: bytes) -> None:
        if stream_sid is None:
            return
        payload = base64.b64encode(pcm16_to_mulaw(pcm_bytes)).decode("ascii")
        await ws.send_text(json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}))

    async def send_event(event: dict) -> None:
        # send_frame streams a whole reply's frames to Twilio near-instantly
        # (no real-time pacing server-side, see call_engine._stream_audio) --
        # Twilio buffers and plays them back at real speed regardless of
        # whether our server has since moved on. On a genuine barge-in,
        # our server stops sending *more* frames, but without an explicit
        # "clear" event Twilio would keep playing whatever it had already
        # buffered. "clear" is Twilio's Media Streams mechanism for
        # exactly this -- discards any buffered/unplayed audio for this
        # stream immediately, so the caller's interruption actually cuts
        # the agent off instead of talking over a few more seconds of it.
        logger.info("[%s] turn event: %s", call_id, event)
        if event.get("event") == "interrupted" and stream_sid is not None:
            await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
        if event.get("event") == "call_ended_by_agent":
            await ws.close(code=1000)

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.disconnect":
                logger.info("[%s] client disconnected", call_id)
                break

            if message.get("text") is None:
                continue

            data = json.loads(message["text"])
            event = data.get("event")

            if event == "start":
                stream_sid = data["start"]["streamSid"]
                caller_number = normalize_us_phone(
                    data["start"].get("customParameters", {}).get("callerNumber", "")
                ) or None
                engine = ChatEngine(persona=DEFAULT_PERSONA_KEY, caller_ani=caller_number, call_id=call_id)
                logger.info(
                    "[%s] stream started (sid=%s, caller_number=%s)", call_id, stream_sid, caller_number
                )
                # The agent speaks first -- same turn_task mechanism a
                # normal reply uses, so the barge-in detection below
                # (RMS-threshold on incoming media while turn_task is in
                # flight) applies to the greeting for free.
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

            elif event == "media":
                if engine is None:
                    continue  # media before start shouldn't happen per Twilio's protocol; skip defensively
                pcm_chunk = mulaw_to_pcm16(base64.b64decode(data["media"]["payload"]))
                rms = _pcm16_rms(pcm_chunk)
                now = time.monotonic()

                if turn_task is not None and not turn_task.done():
                    # Assistant is talking/thinking -- only treat sustained
                    # voice activity as a genuine barge-in, not line noise.
                    if rms > SILENCE_RMS_THRESHOLD:
                        if barge_in_started_at is None:
                            barge_in_started_at = now
                        elif now - barge_in_started_at >= BARGE_IN_DURATION_S:
                            if not interrupt_event.is_set():
                                interrupt_event.set()
                                logger.info("[%s] barge-in detected, interrupting current turn", call_id)
                            audio_buffer += pcm_chunk
                    else:
                        barge_in_started_at = None
                    continue

                audio_buffer += pcm_chunk

                if rms > SILENCE_RMS_THRESHOLD:
                    # Require sustained voice activity before committing to
                    # "the caller started talking" -- a single loud frame
                    # (a click, a pop, a brief line noise spike) shouldn't
                    # be enough on its own, same reasoning as the barge-in
                    # gate above.
                    if speech_started_at is None:
                        speech_started_at = now
                    elif not has_spoken and now - speech_started_at >= SPEECH_START_DURATION_S:
                        has_spoken = True
                    silence_started_at = None
                elif has_spoken:
                    if silence_started_at is None:
                        silence_started_at = now
                    elif now - silence_started_at >= SILENCE_DURATION_S:
                        has_spoken = False
                        silence_started_at = None
                        speech_started_at = None
                        audio_to_process = bytes(audio_buffer)
                        audio_buffer.clear()
                        interrupt_event = asyncio.Event()
                        barge_in_started_at = None
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
                else:
                    # Quiet, and we never committed to has_spoken -- reset
                    # so an isolated noise spike doesn't linger and add up
                    # with a later, unrelated one.
                    speech_started_at = None

            elif event == "stop":
                logger.info("[%s] stream stopped", call_id)
                break

    except WebSocketDisconnect:
        logger.info("[%s] disconnected", call_id)
    finally:
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        logger.info("[%s] call ended", call_id)
