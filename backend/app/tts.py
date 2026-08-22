"""app/tts.py -- Gemini text-to-speech.

Gemini TTS returns 24kHz PCM; every transport in this project (web pseudo-
call today, others later) expects 8kHz/16-bit/mono, so this resamples
before handing audio back.

Caches synthesized audio by exact text: the LLM often repeats the same
phrasing verbatim for early, tightly-scripted turns (the greeting, "what's
your date of birth", "what's your gender", etc. -- see
app/prompts/templates/patient_intake.yaml's fairly directive wording for
these), so a cache hit skips both the Gemini TTS call and its ~1-3s
latency entirely. Later, more personalized turns (reading back a specific
address, say) just won't repeat verbatim and simply miss the cache -- no
harm, no special-casing needed. This does NOT let us skip the LLM call
itself: only the LLM knows what to say next given the conversation so
far (what's already collected, corrections, resuming a dropped call,
etc.) -- caching starts only once we already have that exact text in hand.
"""
import logging
import struct
from collections import OrderedDict

from google.genai import types

from app.config import settings
from app.gemini_client import get_client

logger = logging.getLogger(__name__)

_CACHE_MAX_ENTRIES = 200
_cache: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()


def _resample_24k_to_8k(pcm16_bytes: bytes) -> bytes:
    """Gemini TTS returns 24kHz PCM; exact 3:1 ratio down to 8kHz, so a
    simple averaging decimation is enough -- no resampling library needed."""
    n = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm16_bytes[: n * 2])
    out = []
    for i in range(0, n - n % 3, 3):
        out.append((samples[i] + samples[i + 1] + samples[i + 2]) // 3)
    return struct.pack(f"<{len(out)}h", *out)


def _synthesize_uncached(text: str) -> bytes:
    response = get_client().models.generate_content(
        model=settings.TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=settings.TTS_VOICE
                    )
                )
            ),
        ),
    )
    pcm_24k = response.candidates[0].content.parts[0].inline_data.data
    return _resample_24k_to_8k(pcm_24k)


def synthesize(text: str) -> bytes:
    """Gemini TTS -> raw 8kHz/16-bit/mono PCM bytes, ready to stream
    straight over the websocket to the browser. Cached by (voice, exact
    text) -- see module docstring for why this is worth doing and what it
    doesn't try to do."""
    key = (settings.TTS_VOICE, text)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)  # LRU: mark as recently used
        logger.info("tts cache hit (%d bytes, %d cached phrases)", len(cached), len(_cache))
        return cached

    audio = _synthesize_uncached(text)

    _cache[key] = audio
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)  # evict least-recently-used

    return audio
