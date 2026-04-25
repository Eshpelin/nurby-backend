"""Drop obvious whisper hallucinations.

Pure function. No I/O, no state. Trivially unit-testable. Reasons are
returned as short tags so metrics can group by cause.
"""

from __future__ import annotations

import re

from services.perception.audio.constants import (
    AUDIO_HALLUCINATION_AVG_LOGPROB_MIN,
    AUDIO_HALLUCINATION_BLOCKLIST,
    AUDIO_HALLUCINATION_MIN_DURATION_MS,
    AUDIO_HALLUCINATION_NO_SPEECH_PROB_MAX,
    AUDIO_HALLUCINATION_REPEAT_THRESHOLD,
)
from services.perception.audio.types import TranscriptResult

_PUNCT_RE = re.compile(r"[^\w\s']")
_BLOCKLIST = frozenset(p.lower() for p in AUDIO_HALLUCINATION_BLOCKLIST)


def _normalize(text: str) -> str:
    """Lower, strip punctuation, collapse whitespace. Used for blocklist
    membership only. The original text is preserved on the row.
    """
    s = _PUNCT_RE.sub(" ", text.lower())
    return " ".join(s.split())


def _max_consecutive_repeat(tokens: list[str]) -> int:
    """Longest run of the same token in a row. ``> threshold`` flags
    classic Whisper repetition loops like "the the the the the".
    """
    best = 1
    current = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            current += 1
            if current > best:
                best = current
        else:
            current = 1
    return best


def filter_hallucination(result: TranscriptResult) -> tuple[bool, str]:
    """Return ``(keep, reason)``.

    Filtered transcripts still get written with ``filtered=true`` for
    audit. The timeline read path excludes them. Reason strings are
    stable identifiers for the metrics dimension, so do not paraphrase
    when adjusting.
    """
    text = (result.text or "").strip()

    # Empty.
    if not text:
        return False, "empty"

    # Provider error short-circuit. Should be rare. router retries
    # before reaching the filter.
    if result.error:
        return False, "provider_error"

    # No-speech probability above threshold. Strongest signal.
    if (
        result.no_speech_prob is not None
        and result.no_speech_prob > AUDIO_HALLUCINATION_NO_SPEECH_PROB_MAX
    ):
        return False, "no_speech_prob"

    # Low average log-prob. The model is uncertain. Tends to coincide
    # with confabulated phrases.
    if (
        result.avg_logprob is not None
        and result.avg_logprob < AUDIO_HALLUCINATION_AVG_LOGPROB_MIN
    ):
        return False, "avg_logprob"

    # Too short and too few tokens. Single-syllable matches on noise.
    normalized = _normalize(text)
    tokens = normalized.split()
    if (
        result.duration_ms < AUDIO_HALLUCINATION_MIN_DURATION_MS
        and len(tokens) <= 1
    ):
        return False, "too_short"

    # Canonical hallucination phrases.
    if normalized in _BLOCKLIST:
        return False, "blocklist"

    # Token-level repetition loop.
    if (
        len(tokens) > AUDIO_HALLUCINATION_REPEAT_THRESHOLD
        and _max_consecutive_repeat(tokens) > AUDIO_HALLUCINATION_REPEAT_THRESHOLD
    ):
        return False, "repetition"

    return True, "ok"
