"""Tunables for the audio transcription pipeline.

Single source of truth so every module reads the same numbers. Keeping
these in code (not env) is intentional. they shape correctness, not
deployment.
"""

from __future__ import annotations

# Bounded queue depths. Drop-oldest backpressure on overflow.
AUDIO_PCM_QUEUE_MAX = 200
AUDIO_SEGMENT_QUEUE_MAX = 50

# VAD segmentation bounds. Anything outside the window is split or padded.
AUDIO_VAD_MIN_SEG_MS = 500
AUDIO_VAD_MAX_SEG_MS = 15_000
AUDIO_VAD_SILENCE_CLOSE_MS = 800

# Enrichment scheduling. Phase 2 will use these. Documented now to keep
# the plan and the code in lockstep.
AUDIO_ENRICHMENT_DELAY_S = 3
AUDIO_LATE_TRANSCRIPT_WINDOW_S = 30
AUDIO_VLM_RERUN_COOLDOWN_S = 15

# STT worker pool sizing. Local CPU-bound, cloud network-bound.
AUDIO_STT_WORKERS_LOCAL = 1
AUDIO_STT_WORKERS_CLOUD = 4
AUDIO_STT_RETRIES = 3
AUDIO_STT_COOLDOWN_S = 60

# Audio file storage.
AUDIO_OPUS_BITRATE_KBPS = 24
AUDIO_SAMPLE_RATE_HZ = 16_000
AUDIO_CHANNELS = 1

# Hallucination filter thresholds.
AUDIO_HALLUCINATION_NO_SPEECH_PROB_MAX = 0.6
AUDIO_HALLUCINATION_AVG_LOGPROB_MIN = -1.0
AUDIO_HALLUCINATION_MIN_DURATION_MS = 300
AUDIO_HALLUCINATION_REPEAT_THRESHOLD = 4

# Embedding gates. Below these, semantic search returns noise.
AUDIO_MIN_TOKENS_FOR_EMBED = 4
AUDIO_MIN_DURATION_S_FOR_EMBED = 1.0

# Whisper-class hallucination blocklist. These are the canonical phrases
# the model invents on near-silence. Match case-insensitively after
# stripping punctuation. Keep short. low precision is fine here, we only
# drop on exact phrase match below.
AUDIO_HALLUCINATION_BLOCKLIST: tuple[str, ...] = (
    "thanks for watching",
    "thank you for watching",
    "thank you",
    "thanks for watching!",
    "please subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "see you next time",
    "see you in the next video",
    "see you in the next one",
    "bye",
    "bye bye",
    "okay",
    "ok",
    "you",
    "yeah",
    "uh",
    "um",
    "mm",
    "mhm",
    "hmm",
    "music",
    "[music]",
    "(music)",
    "applause",
    "[applause]",
    "(applause)",
    "silence",
    "[silence]",
    "background noise",
    "[background noise]",
)
