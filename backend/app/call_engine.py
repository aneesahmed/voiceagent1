"""app/call_engine.py -- transport-agnostic turn processing, shared by
every voice channel (browser /audio websocket, Twilio phone calls, and any
future channel). Every transport converts its own wire format at the
boundary and calls process_turn with plain 8kHz/16-bit/mono PCM -- this
project's canonical audio contract (see CLAUDE.md decision #6) -- plus two
callables: send_frame for outgoing audio and send_event for out-of-band
signaling (reply_end/interrupted/error). A transport that has no concept
of those events (e.g. Twilio) can make send_event a no-op/log call.
"""
import asyncio
import logging
from typing import Awaitable, Callable

from app.chat_engine import ChatEngine
from app.config import settings
from app.stt import transcribe
from app.tts import synthesize

logger = logging.getLogger(__name__)

FRAME_SIZE = 640  # 320 samples * 2 bytes = 40ms @ 8kHz

SendFrame = Callable[[bytes], Awaitable[None]]
SendEvent = Callable[[dict], Awaitable[None]]


async def _stream_audio(audio_bytes: bytes, send_frame: SendFrame, interrupt_event: asyncio.Event) -> bool:
    """Streams pre-synthesized PCM audio out in FRAME_SIZE chunks, checking
    interrupt_event and yielding between every frame. Returns False (and
    stops early, mid-frame) if interrupted."""
    for i in range(0, len(audio_bytes), FRAME_SIZE):
        if interrupt_event.is_set():
            return False
        await send_frame(audio_bytes[i : i + FRAME_SIZE])
        await asyncio.sleep(0)
    return True


async def _end_call_after_playback(audio_bytes: bytes, call_id: object, send_event: SendEvent) -> None:
    """Called when the model used the end_call tool (see chat_engine.py)
    -- waits for the just-streamed farewell audio to have actually
    finished playing before telling the transport to hang up. Streaming
    itself is near-instant server-side (see _stream_audio -- no real-time
    pacing, that happens client-side/on Twilio's relay), so closing the
    connection immediately after the last byte is sent would risk cutting
    the farewell off mid-sentence on a real phone call."""
    duration_s = len(audio_bytes) / (2 * settings.AUDIO_SAMPLE_RATE)
    await asyncio.sleep(duration_s + 0.5)  # small buffer for network/scheduling jitter
    logger.info("[%s] agent ended the call", call_id)
    await send_event({"event": "call_ended_by_agent"})


async def greet(
    *,
    engine: ChatEngine,
    call_id: object,
    interrupt_event: asyncio.Event,
    send_frame: SendFrame,
    send_event: SendEvent,
    turn_index: int = 0,
) -> None:
    """Runs as the very first "turn" of a call -- generates and streams the
    agent's opening line immediately on connect, as its own task just like
    process_turn, so the transport's receive loop is free to notice a
    caller talking over the greeting and treat it as a genuine barge-in
    (the existing turn_task-in-flight check handles that for free, no
    special-casing needed at the call site).

    No filler/hold-music cue -- that was removed (see CLAUDE.md decision
    #34) after it caused real problems on Twilio calls, so the caller just
    waits through the LLM+TTS round trip in silence before hearing the
    greeting. Simpler and more reliable than the filler mechanism it
    replaced, at the cost of a few seconds of dead air on a slow turn."""
    logger.info("[%s] ===== TURN %d (greeting) =====", call_id, turn_index)
    try:
        greeting = await asyncio.to_thread(engine.generate_greeting)
        logger.info("[%s] greeting: %r (end_call=%s)", call_id, greeting.text, greeting.end_call)

        if interrupt_event.is_set():
            logger.info("[%s] interrupted before greeting synthesis", call_id)
            return

        greeting_audio = await asyncio.to_thread(synthesize, greeting.text)
        logger.info("[%s] synthesized %d bytes of greeting audio", call_id, len(greeting_audio))

        if interrupt_event.is_set():
            logger.info("[%s] interrupted before greeting playback", call_id)
            return

        if not await _stream_audio(greeting_audio, send_frame, interrupt_event):
            logger.info("[%s] interrupted mid-greeting-stream", call_id)
            await send_event({"event": "interrupted"})
            return

        logger.info("[%s] greeting stream complete", call_id)
        await send_event({"event": "reply_end"})

        if greeting.end_call:
            await _end_call_after_playback(greeting_audio, call_id, send_event)

    except Exception:
        logger.exception("[%s] error while greeting", call_id)
        try:
            await send_event({"event": "error", "message": "internal error"})
        except Exception:
            pass


async def process_turn(
    *,
    engine: ChatEngine,
    call_id: object,
    audio_bytes: bytes,
    interrupt_event: asyncio.Event,
    send_frame: SendFrame,
    send_event: SendEvent,
    turn_index: int = 0,
) -> None:
    """Runs one caller turn (transcribe -> reply -> synthesize -> stream)
    as its own task, concurrently with the transport's receive loop, so
    the loop stays free to notice barge-in audio and set interrupt_event.
    Checked before each expensive stage and between every streamed frame,
    so an interrupt lands within one 40ms frame instead of waiting for the
    whole reply."""
    logger.info("[%s] ===== TURN %d =====", call_id, turn_index)
    try:
        logger.info("[%s] transcribing %d bytes of audio", call_id, len(audio_bytes))
        heard = await asyncio.to_thread(transcribe, audio_bytes)
        logger.info("[%s] caller said: %r", call_id, heard)

        if not heard:
            logger.info("[%s] empty transcript, skipping reply", call_id)
            return

        # Surfaced to the client's call log (transports with no visual log,
        # e.g. Twilio, just get an extra logger.info line via send_event).
        await send_event({"event": "transcript", "text": heard})

        if interrupt_event.is_set():
            logger.info("[%s] interrupted before reply generation", call_id)
            return

        reply = await asyncio.to_thread(engine.generate_reply, heard)
        logger.info("[%s] agent reply: %r (end_call=%s)", call_id, reply.text, reply.end_call)

        if interrupt_event.is_set():
            logger.info("[%s] interrupted before synthesis", call_id)
            return

        reply_audio = await asyncio.to_thread(synthesize, reply.text)
        logger.info("[%s] synthesized %d bytes of reply audio", call_id, len(reply_audio))

        if interrupt_event.is_set():
            logger.info("[%s] interrupted before reply playback", call_id)
            return

        if not await _stream_audio(reply_audio, send_frame, interrupt_event):
            logger.info("[%s] interrupted mid-reply-stream", call_id)
            await send_event({"event": "interrupted"})
            return

        logger.info("[%s] reply stream complete", call_id)
        await send_event({"event": "reply_end"})

        if reply.end_call:
            await _end_call_after_playback(reply_audio, call_id, send_event)

    except Exception:
        logger.exception("[%s] error while processing turn", call_id)
        try:
            await send_event({"event": "error", "message": "internal error"})
        except Exception:
            pass
