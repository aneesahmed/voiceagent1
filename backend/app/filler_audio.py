"""app/filler_audio.py -- a soft, non-verbal "please wait" cue, played
while a real Gemini call (LLM reply and/or TTS synthesis) is still in
flight, so the caller never sits in dead air for the ~1-8s that can take.

History: originally spoken TTS phrases ("One moment, let me type that
up, please."), then a procedurally-generated flat drone chord (described
as robotic), then a procedurally-generated piano-like melodic phrase
(CLAUDE.md decision #29). This version plays a real pre-recorded hold-music
clip (`app/assets/hold_music.pcm`) instead -- supplied directly by the
user, already trimmed/faded/leveled for this purpose, so no runtime audio
processing is needed. The clip is raw PCM in the project's canonical
8kHz/16-bit/mono format (converted once offline from the source MP3), so
it's just read from disk -- no decode library needed at runtime, keeping
the deployed app's dependency footprint unchanged (CLAUDE.md decision #23
and the broader "no external audio asset" note this replaces).

Clip is trimmed to 10s. This is only a safety ceiling, not a target --
`_stream_audio` (call_engine.py) sends the whole blob to the client
near-instantly with no real-time pacing, and the client cuts playback
short the moment `filler_stop` arrives (i.e. as soon as the real reply is
ready), so the clip's length never blocks the conversation; a shorter
clip just means a smaller bundled asset.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ASSET_PATH = Path(__file__).parent / "assets" / "hold_music.pcm"

_cache: bytes | None = None


def _load() -> bytes:
    return _ASSET_PATH.read_bytes()


def warm_cache() -> None:
    """Loads the clip up front. Safe to call more than once. Pure file
    read -- no network call, so this can never fail at server startup for
    a network reason (a missing/corrupt asset file would still raise)."""
    global _cache
    if _cache is None:
        _cache = _load()
    logger.info("hold-music filler cache warmed (%d bytes)", len(_cache))


def _get() -> bytes:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def get_filler_audio() -> bytes:
    """Returns PCM audio (project's canonical 8kHz/16-bit/mono) for the
    mid-turn "please wait" cue."""
    return _get()


def get_opening_filler_audio() -> bytes:
    """Same cue, played right when a call connects while the real
    greeting is generated -- see call_engine.greet()."""
    return _get()
