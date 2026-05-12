"""Audio transcription + tagging pipeline.

Capture pulls PCM off the dual-output ffmpeg pipe, VAD chunks it into
speech segments, the STT router dispatches each segment to a provider,
the hallucination filter drops obvious false positives, and the write
path inserts transcripts and broadcasts WS events.

The PANNs AudioSet tagger lives in :mod:`.tagger`. We re-export its
public surface so ``from services.perception import audio as audio_cls``
keeps working from callers that pre-date the package layout (e.g.
``services/ingestion/audio_worker.py``).

Imports stay lazy so importing this package never pulls heavy
dependencies (faster-whisper, silero, panns_inference) into processes
that will not run audio.
"""

from services.perception.audio.tagger import (  # noqa: F401
    AUDIOSET_LABEL_MAP,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    WINDOW_SECONDS,
    classify,
    count_clap_peaks,
    is_available,
)
