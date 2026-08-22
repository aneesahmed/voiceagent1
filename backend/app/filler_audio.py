"""app/filler_audio.py -- a soft, non-verbal "please wait" cue, played
while a real Gemini call (LLM reply and/or TTS synthesis) is still in
flight, so the caller never sits in dead air for the ~1-8s that can take.

Previously this synthesized short spoken phrases ("One moment, let me
type that up, please.") via Gemini TTS, then a held drone-like chord
(three flat sine waves with a slow tremolo -- described, accurately, as
sounding robotic). This version is a light, piano-like melodic phrase
instead: each note gets a real attack/decay envelope and a few decaying
harmonics rather than a flat sustained tone, played as a gentle repeating
sequence rather than one static chord -- see CLAUDE.md decision #29.

Generated procedurally (no external audio asset, no TTS call -- so this
never costs a Gemini call at all, unlike the old spoken filler which
still needed one TTS synthesis to build the cached clip).
"""
import logging
import math
import struct

from app.config import settings

logger = logging.getLogger(__name__)

_DURATION_S = 25.0  # a safety ceiling, not a target -- filler_stop (see
# call_engine.py) cuts this short the instant the real reply is ready, so
# this only ever gets fully used if something is unusually slow; cheap to
# make generous.
_FADE_S = 0.4  # overall clip fade in/out, so starting/looping never clicks

# A gentle, slightly wistful little phrase in C major pentatonic --
# deliberately simple and repetitive, since this is background "please
# wait" music that's almost never heard in full, not a piece meant to be
# listened to closely. (freq Hz, beat offset within one phrase repeat).
_NOTE_SEQUENCE = [
    (523.25, 0.0),  # C5
    (659.25, 0.9),  # E5
    (783.99, 1.8),  # G5
    (659.25, 2.7),  # E5
    (587.33, 3.6),  # D5
    (783.99, 4.5),  # G5
]
_PHRASE_DURATION_S = 5.4  # one loop of the pattern above

_NOTE_DECAY_S = 1.1  # exponential decay time-constant -- piano notes ring out, not sustain flat
_NOTE_TAIL_S = _NOTE_DECAY_S * 4  # let a note ring out to near-silence before dropping it
_ATTACK_S = 0.012  # fast but not instant, avoids a click at note-on
_HARMONICS = [(1.0, 1.0), (2.0, 0.35), (3.0, 0.12), (4.0, 0.05)]  # (multiple of fundamental, relative amplitude)
_AMPLITUDE = 0.16  # quiet -- this plays instead of/under speech, not over it

_cache: bytes | None = None


def _add_note(buf: list[float], rate: int, start_s: float, freq: float) -> None:
    start_sample = int(start_s * rate)
    tail_samples = int(_NOTE_TAIL_S * rate)
    buf_len = len(buf)

    for i in range(tail_samples):
        idx = start_sample + i
        if idx >= buf_len:
            break
        t = i / rate
        envelope = math.exp(-t / _NOTE_DECAY_S)
        attack = min(1.0, t / _ATTACK_S)
        value = sum(amp * math.sin(2 * math.pi * freq * mult * t) for mult, amp in _HARMONICS)
        buf[idx] += value * envelope * attack


def _generate_tone() -> bytes:
    rate = settings.AUDIO_SAMPLE_RATE
    total_samples = int(rate * _DURATION_S)
    buf = [0.0] * total_samples

    phrase_start = 0.0
    while phrase_start < _DURATION_S:
        for freq, beat_offset in _NOTE_SEQUENCE:
            note_start = phrase_start + beat_offset
            if note_start < _DURATION_S:
                _add_note(buf, rate, note_start, freq)
        phrase_start += _PHRASE_DURATION_S

    samples = [0] * total_samples
    for i in range(total_samples):
        t = i / rate
        fade_in = min(1.0, t / _FADE_S)
        fade_out = min(1.0, (_DURATION_S - t) / _FADE_S)
        value = buf[i] * _AMPLITUDE * fade_in * fade_out
        samples[i] = int(max(-1.0, min(1.0, value)) * 32767)

    return struct.pack(f"<{total_samples}h", *samples)


def warm_cache() -> None:
    """Generates the tone up front. Safe to call more than once. Pure
    computation -- no network call, so unlike the old TTS-based version
    this can never fail at server startup."""
    global _cache
    if _cache is None:
        _cache = _generate_tone()
    logger.info("processing-tone cache warmed (%d bytes)", len(_cache))


def _get() -> bytes:
    global _cache
    if _cache is None:
        _cache = _generate_tone()
    return _cache


def get_filler_audio() -> bytes:
    """Returns PCM audio (project's canonical 8kHz/16-bit/mono) for the
    mid-turn "please wait" cue."""
    return _get()


def get_opening_filler_audio() -> bytes:
    """Same cue, played right when a call connects while the real
    greeting is generated -- see call_engine.greet()."""
    return _get()
