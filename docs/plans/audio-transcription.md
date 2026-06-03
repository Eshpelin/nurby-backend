# Plan. Audio capture and transcription

Status. Draft. Ready for execution.
Owner. TBD.
Reviewer sign-off. Ahmed.
Last updated. 2026-04-23.

## 1. Goal

Capture audio from camera feeds and standalone microphones. Transcribe
continuously. Attach transcripts to the timeline. Make every layer opt-in per
camera. Support local (faster-whisper) and cloud (OpenAI, Gemini, AWS)
providers behind a pluggable interface.

## 2. Non-goals

- Speaker diarization (Phase 4 at earliest).
- Live captions on the live view (Phase 4).
- Edit UI for transcripts (schema-ready, no UI v1).
- iOS or Android native clients.

## 3. Architectural invariants

These must not be violated by any PR in any phase:

1. **One ffmpeg per camera.** Video and audio come from the same process
   with `-map` outputs. No second RTSP session.
2. **One clock.** Host wall-clock stamped at packet demux. Never camera PTS,
   never provider return time.
3. **No FK coupling between transcripts and observations.** Overlap join at
   read time only. GiST indexed.
4. **No fake audio-only observations.** Speech with no video event becomes a
   `transcript_event` row, not an `observation` row.
5. **Video path never blocks on audio.** All audio queues are bounded with
   drop-oldest backpressure.
6. **Feature flag gated.** `NURBY_AUDIO_ENABLED` controls whether any
   capture task spawns.

## 4. Privacy model

Four independent per-camera toggles:

1. `audio_capture_enabled`. Default off.
2. `audio_transcribe_enabled`. Default off. Requires #1.
3. `audio_store_raw`. Default off. Requires #1. Opus 24 kbps.
4. `transcript_store`. Default on when #2 is on. If off, transcript lives in
   memory only for VLM enrichment then discarded.

Plus:

- Audit log row on every toggle change (who, when, IP). Debugging, not legal.
- Transcript export endpoint (user convenience).
- Hard-delete endpoint (user convenience).

No consent dialog. Operator of the hardware is responsible for local
compliance.

## 5. Phase 0. Ingestion refactor (blocker for all audio work)

Ship this as its own PR. Audio side is a no-op.

5.1. Identify current ffmpeg spawn point in ingestion worker.
5.2. Refactor to a single ffmpeg process per camera with dual pipe output.
     `-map 0:v -f rawvideo pipe:3 -map 0:a? -f s16le -ar 16000 -ac 1 pipe:4`.
     Audio map is optional (`?`), camera without audio track still works.
5.3. Introduce `shared/clock.py` with a `stamp_now()` helper used by every
     demux-time stamping site. Document invariant in the module docstring.
5.4. Add GiST index migration for `observations`.
     ```sql
     CREATE INDEX observations_time_range_idx
     ON observations
     USING gist (camera_id, tstzrange(started_at, ended_at));
     ```
5.5. Add `NURBY_AUDIO_ENABLED` env flag read at startup. When false, skip
     audio pipe setup entirely but keep the dual-output ffmpeg shape.
5.6. Tests. Existing video ingestion tests must pass unchanged. New test.
     camera with no audio track still ingests video.

**Exit criteria.** Main branch has dual-output ffmpeg. All existing cameras
work. No audio code active.

## 6. Phase 1. Capture, transcribe, display

### 6.1. Schema

Migration `nnnn_audio_transcripts.py`.

```sql
CREATE TABLE audio_captures (
  id UUID PRIMARY KEY,
  camera_id UUID REFERENCES cameras(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL,
  duration_ms INT NOT NULL,
  file_path TEXT,                       -- null if store_raw=false
  codec TEXT NOT NULL DEFAULT 'opus',
  sample_rate INT NOT NULL,
  size_bytes BIGINT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON audio_captures (camera_id, started_at DESC);

CREATE TABLE transcripts (
  id UUID PRIMARY KEY,
  camera_id UUID REFERENCES cameras(id) ON DELETE CASCADE,
  audio_capture_id UUID REFERENCES audio_captures(id) ON DELETE SET NULL,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL,
  text TEXT NOT NULL,
  original_text TEXT,                   -- for future edit feature
  text_edited BOOLEAN DEFAULT false,
  language TEXT,
  provider TEXT NOT NULL,
  model TEXT,
  confidence FLOAT,
  no_speech_prob FLOAT,
  words JSONB,
  embedding vector(384),
  filtered BOOLEAN DEFAULT false,       -- hallucination-filter soft-delete
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON transcripts (camera_id, started_at DESC);
CREATE INDEX transcripts_time_range_idx
  ON transcripts USING gist (camera_id, tstzrange(started_at, ended_at));
CREATE INDEX ON transcripts USING ivfflat (embedding vector_cosine_ops);

CREATE TABLE audio_audit_log (
  id UUID PRIMARY KEY,
  camera_id UUID REFERENCES cameras(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  field TEXT NOT NULL,                  -- 'audio_capture_enabled' etc.
  old_value TEXT,
  new_value TEXT,
  ip TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE cameras
  ADD COLUMN audio_capture_enabled BOOLEAN DEFAULT false,
  ADD COLUMN audio_transcribe_enabled BOOLEAN DEFAULT false,
  ADD COLUMN audio_store_raw BOOLEAN DEFAULT false,
  ADD COLUMN transcript_store BOOLEAN DEFAULT true,
  ADD COLUMN audio_language TEXT DEFAULT 'en',
  ADD COLUMN audio_retention_days INT DEFAULT 7,
  ADD COLUMN transcript_retention_days INT DEFAULT 30,
  ADD COLUMN stt_provider_id UUID REFERENCES providers(id) ON DELETE SET NULL,
  ADD COLUMN stt_budget_minutes_per_hour INT DEFAULT 30;
```

### 6.2. Capture module (`services/perception/audio/`)

Numbered execution steps.

6.2.1. `capture.py`. Consume audio pipe from the dual-output ffmpeg from
       Phase 0. Emit PCM chunks with `capture_t` wall-clock stamp into a
       bounded `pcm_queue` (max 200).
6.2.2. `vad.py`. Silero-VAD. Consume `pcm_queue`. Emit speech segments
       bounded by. Min 500 ms, max 15000 ms, silence-close 800 ms. Output to
       `segment_queue` (max 50).
6.2.3. `hallucination_filter.py`. Pure function. Takes `TranscriptResult`,
       returns `(keep: bool, reason: str)`. Rules.
       - Drop if `no_speech_prob > 0.6`.
       - Drop if `avg_logprob < -1.0`.
       - Drop if text in blocklist (ship 30-phrase list).
       - Drop if any token repeats > 4x consecutively.
       - Drop if duration < 300 ms and token count == 1.
       Filtered segments still written with `filtered=true` for audit, not
       surfaced on timeline.
6.2.4. `stt.py`. Protocol + dispatcher:
       ```python
       class STTProvider(Protocol):
           kind: str
           async def transcribe(
               self, pcm: bytes, meta: SegmentMeta
           ) -> TranscriptResult: ...
       ```
6.2.5. `providers/faster_whisper_provider.py`. Local, default. RAM-aware
       model picker in settings UI.
6.2.6. `providers/mock_provider.py`. Fixture-based for CI.
6.2.7. `router.py`. Owns one task per camera. Wires capture → vad → worker
       pool → db write → ws broadcast. Handles toggle-off immediate
       teardown. Handles provider retry (3 attempts, exp backoff, 60 s
       cooldown after exhaustion).

### 6.3. Write path

Exact sequence on STT completion:

1. Apply hallucination filter.
2. If `transcript_store == false`, skip DB write. Pass transcript to in-memory
   enrichment buffer only.
3. Else insert `transcripts` row. `observation_id` left null.
4. Query overlapping observations via GiST range.
5. If any observation is within its enrichment window, schedule
   `call_later(0, enqueue_enrichment, obs_id)`.
6. WebSocket broadcast `transcript_created` with `{camera_id, id,
   started_at, text}`.

No polling anywhere.

### 6.4. Enrichment (deferred to Phase 2, stubbed in Phase 1)

Phase 1 timeline shows transcripts as their own cards. VLM enrichment of
observations lands in Phase 2.

### 6.5. API

6.5.1. `GET /api/timeline` extended to return merged sorted feed of
       observations and transcript_events. Pagination by `started_at`.
6.5.2. `GET /api/transcripts` with filters (camera_id, from, to, search).
6.5.3. `GET /api/transcripts/export.csv` (GDPR).
6.5.4. `DELETE /api/transcripts/{id}` (hard delete).
6.5.5. `DELETE /api/cameras/{id}/transcripts` (bulk).
6.5.6. `GET /api/audio/{capture_id}` with `?token=` query fallback. Streams
       Opus. Token-gated.
6.5.7. `PATCH /api/cameras/{id}/audio` body is the four toggles plus
       language and retention. Writes `audio_audit_log` row.

### 6.6. UI

6.6.1. `frontend/src/app/cameras/[id]/audio/page.tsx` or tab. Four toggles
       with plain-language labels. Consent confirmation dialog on first
       enable (text drafted by legal doc, link included).
6.6.2. `frontend/src/app/settings/page.tsx`. New Audio section. STT provider
       registry. Faster-whisper model picker with RAM badges. Global
       monthly budget setting.
6.6.3. Camera tile. Small mic icon when audio on. Red dot when storing raw.
6.6.4. Timeline. `TranscriptCard` component. Italic text, mic icon, click
       to play audio if `audio_capture_id` present.

### 6.7. Metrics

6.7.1. In-process counters and histograms. Plain Python (dict + deque rings).
       No external dependency.
6.7.2. `GET /api/admin/stats` returns JSON snapshot. Auth-gated to owner.
6.7.3. Settings UI "System health" panel polls the endpoint.
6.7.4. Counter backend is swappable. If ever needed, drop in
       `prometheus_client` without touching call sites.
6.7.5. Metrics to ship:
       - `stt_segments_total{provider,camera,status}`
       - `stt_latency_seconds{provider}` (p50, p95, p99 from a ring)
       - `stt_queue_depth{stage}`
       - `audio_pcm_drops_total{camera}`
       - `stt_hallucinations_filtered_total{reason}`
       - `audio_budget_used_minutes`

### 6.8. Testing

6.8.1. Unit. Hallucination filter rule-by-rule.
6.8.2. Unit. VAD boundary behavior on synthetic PCM.
6.8.3. Integration. Mock provider end-to-end. Capture → segment → mock STT →
       DB row → WS broadcast.
6.8.4. Integration. Toggle-off mid-segment drops in-flight work.
6.8.5. Integration. Overlap join returns transcript on matching observation.
6.8.6. Load. 20 synthetic cameras with continuous speech, 1 hour, verify no
       unbounded queue growth and p95 end-to-end < 3 s.

### 6.9. Exit criteria Phase 1

- Enable audio on MacBook cam, faster-whisper base.en. Speak "hello nurby".
  Transcript row appears on timeline within 3 s.
- Disable `transcript_store`. No DB rows, no timeline card.
- Disable `audio_store_raw`. No file on disk.
- Disable `audio_capture_enabled`. ffmpeg audio pipe torn down within 1 s.
- `/metrics` shows all counters.
- Feature flag off disables everything.

## 7. Phase 2. VLM enrichment, semantic search, video-correlated speaker ID

7.1. Observation enrichment task. On observation close, schedule
     `loop.call_later(3.0, enqueue_enrichment, obs_id)`. Worker pulls
     overlapping transcripts, builds VLM prompt with `heard_text`, runs
     VLM, writes `vlm_description`. Debounce. One run per 15 s per obs.
7.2. Late-arrival policy. If transcript arrives within 30 s after obs
     close, re-schedule enrichment once (respects 15 s cooldown).
7.3. Embedding backfill job. Background worker embeds transcripts where
     `tokens >= 4 AND duration >= 1 s AND embedding IS NULL`.
7.4. `services/search/query.py` extended to union-search observations and
     transcripts. Merge and rank. Tune cosine-distance threshold on real
     data.
7.5. Cost caps:
     - Per-camera `stt_budget_minutes_per_hour` already in schema. Enforce
       in router with a sliding-window counter.
     - Global `stt_monthly_budget_minutes` in settings. At 80%, WS warning
       banner. At 100%, switch all cameras to local-only or disable STT
       depending on user setting.
7.6. Digest generation reads transcripts too. Surface "what was heard" in
     the 24 h recap.
7.7. **Tier A speaker attribution. Video-correlated.**
     7.7.1. Schema additions.
            ```sql
            ALTER TABLE transcripts
              ADD COLUMN speaker_person_id UUID REFERENCES persons(id) ON DELETE SET NULL,
              ADD COLUMN speaker_confidence FLOAT,
              ADD COLUMN speaker_source TEXT;
            ```
     7.7.2. At transcript commit time, query face detections on same camera
            during `[t0, t1]`.
     7.7.3. If exactly one known person's face is present for ≥60% of the
            segment duration, set `speaker_person_id = that person`,
            `speaker_source = 'video'`, `speaker_confidence = coverage`.
     7.7.4. Otherwise mark `speaker_source = 'ambiguous'`, leave
            person_id null.
     7.7.5. Timeline card renders `Lynda. "can you grab the package"` when
            attributed, plain quote when ambiguous.

### 7.8. Exit criteria Phase 2

- Observation card shows VLM description that reflects heard speech.
- Search "someone mentioned package" returns relevant obs.
- Budget hit triggers banner and falls back as configured.
- Single-person-in-frame transcripts get attributed by name.

## 8. Phase 3. Cloud providers, rules, voice speaker ID

8.1. `providers/openai_whisper_provider.py`. Uses `gpt-4o-transcribe` or
     `whisper-1`.
8.2. `providers/gemini_audio_provider.py`. `generateContent` with inline
     audio data.
8.3. `providers/aws_transcribe_provider.py`. Batch mode for v1, streaming
     deferred.
8.4. Provider health check endpoint. One-off test call from settings UI.
8.5. Rule trigger `speech_contains`. Conditions. Keyword list, regex,
     semantic-match threshold. Fires existing action pipeline.
8.6. Rule trigger tests with mock provider.
8.7. Export and hard-delete hardening. Audit log entries on every deletion.
     Export returns zip of CSV + Opus files (respecting per-camera
     `audio_store_raw`).
8.8. **Tier B speaker attribution. Voice embeddings.**
     8.8.1. Schema additions.
            ```sql
            ALTER TABLE persons
              ADD COLUMN voice_embedding vector(192),
              ADD COLUMN voice_sample_count INT DEFAULT 0;

            CREATE TABLE voice_samples (
              id UUID PRIMARY KEY,
              person_id UUID REFERENCES persons(id) ON DELETE CASCADE,
              embedding vector(192) NOT NULL,
              source_transcript_id UUID REFERENCES transcripts(id) ON DELETE SET NULL,
              duration_ms INT,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            ```
     8.8.2. Voice embedder. ECAPA-TDNN via speechbrain. 192-d output.
            Runs on CPU, ~20 ms per segment.
     8.8.3. Auto-enrollment. When Tier A attributes a transcript with
            `speaker_confidence >= 0.8` and segment duration ≥ 2 s, extract
            voice embedding, append to `voice_samples`, refresh
            `persons.voice_embedding` as centroid of all samples.
     8.8.4. Inference. For each new transcript, compute embedding, cosine
            match against all `persons.voice_embedding` within this
            household. Accept top match if `cosine >= 0.7` and
            `margin >= 0.1` over second best.
     8.8.5. Fusion. If Tier A and Tier B agree, `speaker_source = 'fused'`,
            confidence = max(A, B). If disagree, prefer B (voice is more
            specific), log to `audio_audit_log` for debugging. If only one
            has a result, use it.
     8.8.6. Manual enrollment UI. People page gets "record 10 s voice
            sample" per person as an opt-in accelerator for cold-start
            attribution.

### 8.9. Exit criteria Phase 3

- Switch provider in UI, same flow works.
- Rule "notify me if someone says 'help'" fires on speech.
- Transcripts get attributed by voice even when face not visible in frame.
- Voice samples auto-accumulate from high-confidence video attributions.

## 9. Phase 4. Live, mic-only, multi-speaker diarization (scoping pass only)

Not planned in detail. Placeholders:

- Browser mic WHIP audio track.
- Standalone mic as `source_type = "audio_only"` camera.
- Streaming STT with partial results over WS.
- Multi-speaker diarization (pyannote) for segments where multiple voices
  overlap. Splits a single transcript row into multiple per-speaker rows.
- Mouth-open landmark delta as a third attribution signal.

Re-scope before starting.

## 10. Constants (tunable, single source of truth)

Place in `services/perception/audio/constants.py`.

```python
AUDIO_PCM_QUEUE_MAX = 200
AUDIO_SEGMENT_QUEUE_MAX = 50
AUDIO_VAD_MIN_SEG_MS = 500
AUDIO_VAD_MAX_SEG_MS = 15000
AUDIO_VAD_SILENCE_CLOSE_MS = 800
AUDIO_ENRICHMENT_DELAY_S = 3
AUDIO_LATE_TRANSCRIPT_WINDOW_S = 30
AUDIO_VLM_RERUN_COOLDOWN_S = 15
AUDIO_STT_WORKERS_LOCAL = 1
AUDIO_STT_WORKERS_CLOUD = 4
AUDIO_STT_RETRIES = 3
AUDIO_STT_COOLDOWN_S = 60
AUDIO_OPUS_BITRATE_KBPS = 24
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_HALLUCINATION_NO_SPEECH_PROB_MAX = 0.6
AUDIO_HALLUCINATION_AVG_LOGPROB_MIN = -1.0
AUDIO_MIN_TOKENS_FOR_EMBED = 4
AUDIO_MIN_DURATION_S_FOR_EMBED = 1.0
```

## 11. Resolved decisions (2026-04-23)

1. **Metrics.** In-process counters + `GET /api/admin/stats` JSON. No
   Prometheus dependency.
2. **Audit log actor.** `user_id = authenticated JWT user`. Store actor
   only, not owner.
3. **Consent dialog.** Dropped. Operator responsibility.
4. **faster-whisper default.** `small.en` on hosts ≥ 8 GB RAM, else
   `base.en` with a settings banner. Picker exposes tiny/base/small/medium
   per user preference.
5. **Speaker attribution.** Two tiers added to Phase 2 (video) and Phase 3
   (voice embeddings). Not deferred to Phase 4.

## 12. Files to create

```
services/perception/audio/__init__.py
services/perception/audio/capture.py
services/perception/audio/vad.py
services/perception/audio/hallucination_filter.py
services/perception/audio/stt.py
services/perception/audio/router.py
services/perception/audio/constants.py
services/perception/audio/providers/__init__.py
services/perception/audio/providers/faster_whisper_provider.py
services/perception/audio/providers/mock_provider.py
services/perception/audio/providers/openai_whisper_provider.py        # Phase 3
services/perception/audio/providers/gemini_audio_provider.py          # Phase 3
services/perception/audio/providers/aws_transcribe_provider.py        # Phase 3
services/api/routes/transcripts.py
services/api/routes/audio.py
alembic/versions/nnnn_add_audio_transcripts.py
alembic/versions/nnnn_observations_gist_index.py                      # Phase 0
shared/clock.py                                                       # Phase 0
frontend/src/app/cameras/[id]/audio/page.tsx
frontend/src/components/TranscriptCard.tsx
frontend/src/components/AudioToggleCard.tsx
frontend/src/components/STTProviderPicker.tsx
frontend/src/components/VoiceEnrollmentCard.tsx             # Phase 3
services/perception/audio/speaker_video.py                  # Phase 2
services/perception/audio/speaker_voice.py                  # Phase 3
services/api/routes/admin_stats.py
```

## 13. Files to modify

```
services/perception/worker.py               # dual-output ffmpeg, spawn audio
services/perception/vlm.py                  # accept heard_text             Phase 2
services/search/query.py                    # union search                  Phase 2
services/search/digest.py                   # include transcripts           Phase 2
services/rules/triggers.py                  # speech_contains               Phase 3
services/api/routes/cameras.py              # audio toggle patch
shared/models.py                            # AudioCapture, Transcript, AudioAuditLog
frontend/src/app/page.tsx                   # transcript cards, mic icons
frontend/src/app/settings/page.tsx          # STT provider section, budget
frontend/src/lib/api.ts                     # transcript endpoints
```

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Consumer cam RTSP session cap | Phase 0 dual-output ffmpeg |
| Clock drift | `shared/clock.py` invariant, reviewer-blocker |
| Whisper hallucinations | Mandatory filter, ship with provider |
| Cost runaway on cloud | Per-camera + global budgets, hard caps |
| Legal exposure in two-party states | Consent confirmation, notice checkbox, audit log, export, delete |
| Queue unbounded growth | Bounded queues with drop-oldest, metrics |
| Video blocked by audio | Never share a task, always separate queues |
| Transcript join perf at scale | GiST indexes from day one |

## 15. Definition of done

- All exit criteria for Phases 0 to 3 met.
- `docs/audio-consent.md` published.
- Metrics dashboard screenshot in PR.
- Load test report attached.
- Rollback plan documented (feature flag off + migration rollback tested).

---

## Change log

- 2026-04-23. Draft authored. Incorporates staff review decisions 1 to 16.
- 2026-04-23. Open decisions resolved. Prometheus dropped. Consent dropped.
  Speaker attribution promoted to Phases 2 and 3 with video + voice tiers.
  Default faster-whisper model `small.en`.
