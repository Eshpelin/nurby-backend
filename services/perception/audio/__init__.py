"""Audio transcription pipeline. Phase 1 of the audio plan.

Capture pulls PCM off the dual-output ffmpeg pipe, VAD chunks it into
speech segments, the STT router dispatches each segment to a provider,
the hallucination filter drops obvious false positives, and the write
path inserts transcripts and broadcasts WS events.

Imports stay lazy so importing this package never pulls heavy
dependencies (faster-whisper, silero) into processes that will not run
audio.
"""
