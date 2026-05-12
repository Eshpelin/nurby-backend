"""Local audio event classifier.

Wraps `panns_inference.AudioTagging` (pretrained CNN14 on AudioSet, 527
classes) so the ingestion service can flag cries, screams, speech, glass
breaks, alarms and barks without a cloud call. Runs on CPU by default.
Heavyweight model. loaded lazily and shared across all audio workers.

Audio input. float32 numpy array, mono, sample rate 32000Hz (PANNs default).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger("nurby.perception.audio")

# Sample rate expected by PANNs (CNN14 was trained at 32k).
SAMPLE_RATE = 32000
# Length of each inference window in seconds.
WINDOW_SECONDS = 1.0
WINDOW_SAMPLES = int(SAMPLE_RATE * WINDOW_SECONDS)

# Map raw AudioSet class strings into our normalized event labels.
# Only classes we care about are listed. everything else is dropped.
# Raw strings come from the AudioSet ontology. lowercased on lookup.
AUDIOSET_LABEL_MAP: dict[str, str] = {
    "baby cry, infant cry": "baby_cry",
    "crying, sobbing": "crying",
    "screaming": "scream",
    "shout": "scream",
    "yell": "scream",
    "speech": "speech",
    "child speech, kid speaking": "speech",
    "conversation": "speech",
    "narration, monologue": "speech",
    "glass": "glass_break",
    "shatter": "glass_break",
    "breaking": "glass_break",
    "smoke detector, smoke alarm": "alarm",
    "fire alarm": "alarm",
    "alarm": "alarm",
    "siren": "alarm",
    "civil defense siren": "alarm",
    "dog": "bark",
    "bark": "bark",
    "howl": "bark",
    "gunshot, gunfire": "gunshot",
    "machine gun": "gunshot",
    "fusillade": "gunshot",
    "explosion": "gunshot",
    # Clap + snap. Surfaces as label="clap" so workflow rules can
    # count multiple claps within a short window (double, triple).
    "clapping": "clap",
    "hands": "clap",
    "applause": "clap",
    "finger snapping": "clap",
}

_lock = threading.Lock()
_tagger = None  # panns_inference.AudioTagging
_labels: list[str] | None = None
_disabled = False


def _load():
    """Lazy-load the CNN14 model. Subsequent calls are no-ops."""
    global _tagger, _labels, _disabled
    if _tagger is not None or _disabled:
        return
    with _lock:
        if _tagger is not None or _disabled:
            return
        try:
            from panns_inference import AudioTagging, labels  # type: ignore
            _tagger = AudioTagging(checkpoint_path=None, device="cpu")
            _labels = labels
            logger.info("Loaded PANNs CNN14 audio tagger with %d classes", len(labels))
        except Exception:
            logger.exception("Failed to load PANNs audio tagger. audio events disabled")
            _disabled = True


def classify(waveform: np.ndarray, min_score: float = 0.2) -> list[dict]:
    """Classify a 1s mono waveform. Returns sorted list of event dicts.

    Each dict. {"label": normalized, "raw_class": audioset_name, "score": 0..1}.
    Only mapped classes above min_score are returned.
    """
    _load()
    if _tagger is None or _labels is None:
        return []

    if waveform.ndim > 1:
        waveform = waveform.mean(axis=0)
    if waveform.dtype != np.float32:
        waveform = waveform.astype(np.float32)
    # panns_inference expects shape (1, samples)
    x = waveform[np.newaxis, :]

    try:
        clipwise_output, _ = _tagger.inference(x)
    except Exception:
        logger.exception("Audio inference failed")
        return []

    scores = clipwise_output[0]
    events: list[dict] = []
    for idx, score in enumerate(scores):
        if score < min_score:
            continue
        raw = _labels[idx]
        normalized = AUDIOSET_LABEL_MAP.get(raw.lower())
        if not normalized:
            continue
        events.append({
            "label": normalized,
            "raw_class": raw,
            "score": float(score),
        })
    # Dedup. keep highest score per normalized label.
    best: dict[str, dict] = {}
    for ev in events:
        cur = best.get(ev["label"])
        if cur is None or ev["score"] > cur["score"]:
            best[ev["label"]] = ev
    return sorted(best.values(), key=lambda e: -e["score"])


def count_clap_peaks(
    waveform: np.ndarray,
    sample_rate: int,
    min_separation_ms: int = 120,
    rel_threshold: float = 0.6,
    abs_floor: float = 0.05,
) -> int:
    """Count distinct clap transients inside a 1s window.

    PANNs classifies the whole 1s window as ``Clapping`` with one
    score, so two physical claps within the same second look like one
    event to the tagger. This is a cheap envelope-peak detector run on
    the same window. Counts rising-edge peaks above
    ``max(abs_floor, rel_threshold * peak_envelope)`` separated by at
    least ``min_separation_ms``.

    Returns at least 1 when called (caller already knows PANNs flagged
    a clap, so something is in there). Capped at 6 to avoid a noise
    burst counting like 30 claps.
    """
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=0)
    if waveform.size == 0:
        return 1
    # 20ms boxcar envelope. Smooths individual cycles, keeps clap
    # transients sharp.
    env_size = max(1, sample_rate // 50)
    envelope = np.convolve(
        np.abs(waveform.astype(np.float32)),
        np.ones(env_size, dtype=np.float32) / env_size,
        mode="same",
    )
    peak = float(envelope.max())
    if peak <= abs_floor:
        return 1
    threshold = max(abs_floor, rel_threshold * peak)
    above = envelope > threshold
    min_sep = max(1, sample_rate * min_separation_ms // 1000)
    count = 0
    last_peak_idx = -min_sep
    for i in range(1, len(above)):
        if above[i] and not above[i - 1] and (i - last_peak_idx) >= min_sep:
            count += 1
            last_peak_idx = i
    return max(1, min(6, count))


def is_available() -> bool:
    """True if the tagger is loaded and ready, or can still be tried."""
    return not _disabled
