# Nurby Agentic Q&A Layer. Design v1

Status. Draft for team review before Phase 1 build.
Owner. Backend team.
Audience. Junior engineers picking up Phase 1 tickets, plus product + privacy reviewers.

This document specifies the design of an agentic question-answering surface on top of Nurby. The user types a natural-language question ("Did Daddy eat today?"), the system plans, queries indexed observations, optionally falls back to running ad-hoc VLM analysis against raw stored footage for concepts that were never indexed, synthesizes a cited answer, and streams progress live. The agent is read-only in Phase 1. The agent is bounded by per-request and per-user budgets at all times.

The "agent loop" itself (tool-use, observe-think-act-observe, exit on budget cap) is standard. The interesting choices are domain-specific. Tool surface, privacy invariants, hallucination control, cost ceilings, identity grounding, time grounding, eval methodology. This doc names those choices explicitly. Every choice is tagged `Decision.` Every rejected alternative is tagged `Rejected.` Every unresolved question is tagged **OPEN.**

---

## 1. Goal and non-goals

### 1.1 Goal

Deliver a natural-language question-answering surface that lets a household user ask any reasonable question about what their cameras have seen, and get a grounded, cited answer in under 30 seconds for typical queries. The system must be honest about uncertainty, refuse to hallucinate, respect existing per-person and per-camera privacy controls, and stay inside a hard cost ceiling. The household must trust the answer enough to make a small decision on it (did the dog go out, did dad take his pills, did the package arrive).

### 1.2 Non-goals

Phase 1 is not a general assistant. It does not answer questions outside the camera domain, does not take action on the household's behalf, does not maintain long-term memory across sessions, does not learn from corrections, and does not federate across households. It is not a replacement for Rules. Rules fire automatically on events; the agent answers ad-hoc queries. It is not a replacement for the existing keyword search at `/api/search` (services/api/routes/search.py). It sits beside it and the search endpoint stays the primary low-latency lookup surface for trivial keyword queries.

---

## 2. User-facing UX

### 2.1 Surface

A new top-level "Ask Nurby" page in the frontend. Chat-style. Single text input at the bottom, conversation history scrolling above. While an investigation is running, a collapsible "trace" panel shows live tool calls + intermediate results so the user understands what the agent is doing (and so we can debug). Each final answer carries a "Why did Nurby say this?" link that opens the full audit trace.

### 2.2 Example interactions

#### Example A. Indexed-concept lookup (fast path).

```
User. Did anyone visit today?

[trace, streamed live]
  plan. Resolve "today" in household timezone -> 2026-05-24 00:00 to now.
        Look up named-person observations in that window.
  tool_call. Query_observations(time_from=..., time_to=..., kinds=[person])
  tool_result. 14 observations, 3 distinct persons. Mom (07:12), Mailman (10:45), Neighbor Jane (15:30).

Final.
Three people visited today.
- Mom was first seen at 07:12 (Front Door) [obs:abc..., obs:def...]
- Mailman at 10:45 (Driveway) [obs:ghi...]
- Neighbor Jane at 15:30 (Front Door) [obs:jkl...]
```

#### Example B. Unindexed-concept lookup (ad-hoc VLM fallback).

```
User. Did Daddy eat today?

[trace]
  plan. "Daddy" is ambiguous, ask user OR auto-resolve to starred Person.
        Eating is not an indexed concept. Need to. (a) find when Daddy was at the
        kitchen camera today, (b) run VLM on those clips with question
        "Is the person eating?"
  tool_call. Resolve_person(name="Daddy")
  tool_result. Matched Person "Dad" (display_name), id=p_001
  tool_call. Query_observations(person_id=p_001, time_from=..., camera_role="kitchen")
  tool_result. 3 observations. 08:14 (12 min), 12:30 (8 min), 19:05 (22 min)
  tool_call. Analyze_clip_with_vlm(observation_id=..., question="Is the person eating? Look for food, utensils, hand-to-mouth motion.")
  tool_result. {confidence: 0.92, answer: "yes", evidence: ["bowl on table", "spoon in hand at 19:08"], frame_ts: [19:07, 19:08, 19:11]}
  tool_call. Analyze_clip_with_vlm(observation_id=..., question="Is the person eating?") (08:14 clip)
  tool_result. {confidence: 0.71, answer: "yes", evidence: ["coffee mug", "toast on plate"], frame_ts: [08:15]}

Final.
Yes. Dad ate at least twice today.
- 08:14 in the Kitchen. Coffee and toast (high confidence) [clip:..., frames 08:15]
- 19:05 in the Kitchen. Dinner with a bowl and spoon (high confidence) [clip:..., frames 19:07-19:11]
Midday clip at 12:30 was inconclusive.
```

#### Example C. No-evidence failure case.

```
User. Did the cat go outside last night?

[trace]
  plan. "last night" = 2026-05-23 18:00 to 2026-05-24 06:00 local. Look for
        cat detections on outdoor cameras in that window. If none, look for
        cat detections on indoor cameras near outdoor-adjacent doors.
  tool_call. Query_observations(object_label="cat", scene_mode="outdoor", time_from=..., time_to=...)
  tool_result. 0 results.
  tool_call. Query_observations(object_label="cat", time_from=..., time_to=...)
  tool_result. 2 indoor observations at 22:14, 02:30 on Living Room cam.

Final.
I did not find any outdoor camera evidence of the cat going outside last night.
The cat was seen indoors at 22:14 and 02:30 on the Living Room camera.
Caveats. I cannot see through walls, and your back door does not have a camera that I have access to. If a camera is offline I will not catch its events.
```

#### Example D. Multi-turn follow-up.

```
User. How about the day before?

[trace]
  plan. Reuse prior context. Subject = cat going outside. Re-resolve "day before"
        relative to original "last night" anchor = 2026-05-22 18:00 to 2026-05-23 06:00.
  ...same tools as Example C...

Final.
Yes. The cat was seen on the Backyard camera at 20:43 for about 4 minutes, then
again at 21:02. [obs:..., obs:...]
```

### 2.3 UX rules

- Stream every tool call name + one-line result summary as it happens.
- Final answer always carries citations. Every claim must point to one of `observation_id`, `recording_id`, `journey_id`, or `analyzer_frame_ts`.
- If confidence is below 0.6 for the load-bearing evidence, prefix the sentence with "I think" not "Yes."
- If the agent has to ask a clarifying question, ask one, do not auto-guess. Send a single `clarification` message and stop the loop.
- Budget-exceeded answers always include partial findings, never a silent truncation.

---

## 3. System architecture

```
+--------------------------------------------------------------+
|  Frontend (Next.js) "Ask Nurby" page                         |
|  text input | streaming trace panel | answer w/ citations    |
+-----------------------------+--------------------------------+
                              |  POST /api/agent/ask
                              |  WS /ws/agent/{run_id}
                              v
+--------------------------------------------------------------+
|  FastAPI                                                     |
|  services/agent/routes.py   (REST + WS handler)              |
+--------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------+
|  Agent driver  services/agent/driver.py                      |
|  - plan/think/act loop                                       |
|  - budget enforcement (tokens, turns, VLM calls)             |
|  - emits trace events to WS pub/sub                          |
+----------+-----------+--------------------+------------------+
           |           |                    |
           |           |                    v
           |           |        +----------------------------+
           |           |        |  Tool registry             |
           |           |        |  services/agent/tools/*.py |
           |           |        +-----+----------+-----------+
           |           |              |          |
           |           |              v          v
           |           |        +----------+ +-------------------+
           |           |        | DB read  | | VLM analyzer      |
           |           |        | queries  | | (frames + clip)   |
           |           |        +----------+ +---------+---------+
           |           |                                |
           |           |                                v
           |           |                        +-----------------+
           |           |                        | Privacy gate    |
           |           |                        | (blur+redact)   |
           |           |                        +--------+--------+
           |           |                                 |
           |           |                                 v
           |           |                        +-----------------+
           |           |                        | VLM providers   |
           |           |                        | (OpenAI, etc.)  |
           |           |                        +-----------------+
           |           v
           |    +----------------------+
           |    |  AgentRun audit log  |
           |    |  (Postgres table)    |
           |    +----------------------+
           v
+--------------------------------------------------------------+
|  WS pub/sub backed by Redis  (channel agent:{run_id})        |
+--------------------------------------------------------------+
```

Decision. The driver is a stateless function that takes an `AgentRun.id` and progresses one turn at a time. State lives in the AgentRun row plus a Redis hash keyed by run id. This lets us survive a backend restart mid-investigation (the next worker picks up the row whose status is `running`).

Rejected. In-process state only. Loses the run on any restart. Unacceptable because long-running VLM analysis can span 30+ seconds.

---

## 4. Tool taxonomy

The original brief listed 12 candidate tools. We ship 5 in Phase 1, trading per-tool simplicity for fewer round-trips through the loop, which reduces orchestration token cost and reduces the surface area the LLM has to reason about. A small, rich registry consistently outperforms a large, granular one in production agent systems.

### 4.1 Tools shipped in Phase 1

| Name | Side effect | Purpose |
|---|---|---|
| `get_household_context` | read | Returns cameras, named persons, system timezone, current time. Once-per-run. |
| `query_observations` | read | Filtered + ranked lookup over Observation, Transcript, Conversation, Summary, Incident, Journey. |
| `resolve_person` | read | Fuzzy match a name to a Person row, with disambiguation candidates. |
| `analyze_clip_with_vlm` | read (but expensive) | Ad-hoc VLM analysis of a Recording or Observation clip against a freeform question. |
| `request_clarification` | none | Special tool. Ends the loop and surfaces a clarification question to the user. |

### 4.2 Tool specifications

#### 4.2.1 `get_household_context`

Purpose. Bootstraps the agent with the same orientation a new household member would get. Cameras, who lives here, what time zone, what time it is now. Always called first in Phase 1 (driver injects the result as the first observation so the LLM does not have to choose to call it).

Params schema.
```json
{ "type": "object", "properties": {}, "additionalProperties": false }
```

Response schema.
```json
{
  "type": "object",
  "required": ["now_iso", "system_timezone", "cameras", "persons"],
  "properties": {
    "now_iso": {"type": "string", "format": "date-time"},
    "system_timezone": {"type": "string"},
    "cameras": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "name", "scene_mode"],
        "properties": {
          "id": {"type": "string", "format": "uuid"},
          "name": {"type": "string"},
          "location_label": {"type": ["string","null"]},
          "scene_mode": {"type": "string", "enum": ["indoor","outdoor"]},
          "timezone": {"type": ["string","null"]},
          "role_hint": {"type": ["string","null"]}
        }
      }
    },
    "persons": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "display_name"],
        "properties": {
          "id": {"type": "string", "format": "uuid"},
          "display_name": {"type": "string"},
          "relationship": {"type": ["string","null"]},
          "is_starred": {"type": "boolean"},
          "privacy_blur": {"type": "boolean"}
        }
      }
    }
  }
}
```

Cost. ~1 DB query, ~300 output tokens worst case.
Idempotency. Pure read; cached for 60s per household.
Today's behavior. Reads from `Camera`, `Person`, `AppSetting` (system_timezone). `role_hint` is a new derived field, see section 9.

#### 4.2.2 `query_observations`

Purpose. The agent's primary data lens. Wraps the existing `services.search.query` helpers and the per-kind list endpoints (services/api/routes/observations.py, transcripts.py, conversations.py, journeys.py, incidents.py, recordings.py, audio.py). Returns a normalized result list across kinds so the LLM does not have to know about every row type.

Params schema.
```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "kinds": {
      "type": "array",
      "items": {"type": "string", "enum": [
        "observation","transcript","conversation","summary",
        "incident","journey","audio_detection","recording"
      ]},
      "default": ["observation"]
    },
    "text_query": {"type": ["string","null"], "description": "Free-text. Hits vector + keyword search."},
    "person_id": {"type": ["string","null"], "format": "uuid"},
    "object_label": {"type": ["string","null"], "description": "YOLO class, e.g. cat"},
    "audio_label": {"type": ["string","null"], "description": "e.g. baby_cry, glass_break"},
    "camera_ids": {"type": ["array","null"], "items": {"type":"string","format":"uuid"}},
    "scene_mode": {"type": ["string","null"], "enum": ["indoor","outdoor"]},
    "time_from": {"type": ["string","null"], "format": "date-time"},
    "time_to": {"type": ["string","null"], "format": "date-time"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
  }
}
```

Response schema.
```json
{
  "type": "object",
  "required": ["results", "truncated"],
  "properties": {
    "truncated": {"type": "boolean"},
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["kind","id","started_at","summary"],
        "properties": {
          "kind": {"type":"string"},
          "id": {"type":"string"},
          "started_at": {"type":"string","format":"date-time"},
          "ended_at": {"type":["string","null"],"format":"date-time"},
          "camera_id": {"type":["string","null"]},
          "camera_name": {"type":["string","null"]},
          "summary": {"type":"string"},
          "persons": {"type":"array","items":{"type":"string"}},
          "objects": {"type":"array","items":{"type":"string"}},
          "has_clip": {"type":"boolean"},
          "confidence": {"type":["number","null"]}
        }
      }
    }
  }
}
```

Cost. 1-3 DB queries (one per kind selected). 0 LLM tokens.
Idempotency. Pure read; safe to retry. Driver dedupes identical calls within a run (section 13).
Side effects. None.
Today's behavior. Wraps `search_observations`, `search_transcripts`, `search_conversations`, `search_summaries` from services/search/query.py. Adds direct Journey/Incident/AudioDetection lookups not currently combined in `/search/union`.

Rejected alternative. Separate tools per row kind (list_observations, list_transcripts, list_journeys, list_incidents, list_audio_detections, list_recordings). Rejected because the LLM ends up calling 4 of them in sequence for any non-trivial question, blowing the turn budget. Combining them behind one tool with a `kinds` filter is strictly cheaper and keeps the LLM's choice space small. The structured filters preserve all the targeting power.

#### 4.2.3 `resolve_person`

Purpose. Fuzzy name lookup with disambiguation. The LLM passes a freeform name from the user's question; the tool returns 0, 1, or multiple candidate Person rows with match scores. The LLM decides whether to ask the user, pick the highest-confidence match, or proceed with all candidates.

Params schema.
```json
{
  "type": "object",
  "required": ["name"],
  "properties": { "name": {"type": "string"} },
  "additionalProperties": false
}
```

Response schema.
```json
{
  "type": "object",
  "required": ["matches"],
  "properties": {
    "matches": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["person_id","display_name","score","match_reason"],
        "properties": {
          "person_id": {"type":"string","format":"uuid"},
          "display_name": {"type":"string"},
          "relationship": {"type":["string","null"]},
          "score": {"type":"number","minimum":0,"maximum":1},
          "match_reason": {"type":"string","enum":["exact","case_insensitive","substring","relationship","alias"]}
        }
      }
    }
  }
}
```

Cost. 1 DB query.
Idempotency. Pure read.
Side effects. None.
Today's behavior. Today there is no alias table; matching is exact via `display_name` in queries. See section 8 for the alias decision.

Rejected alternative. Auto-pick the best match silently. Too unsafe when "Daddy" matches both "Dad" and "Granddad". The LLM must see the candidates and route the decision (ask user vs run for all).

#### 4.2.4 `analyze_clip_with_vlm`

Purpose. Run an ad-hoc VLM call against frames extracted from a Recording or Observation clip with a focused question. This is the escape hatch for any concept that was never indexed by the perception pipeline (eating, sleeping, holding object X, wearing color Y). The hard part is not the call. It is sampling, redaction, schema enforcement, hallucination control. Section 5 covers this tool in depth.

Params schema.
```json
{
  "type": "object",
  "required": ["question","target"],
  "properties": {
    "question": {"type": "string", "maxLength": 500},
    "target": {
      "oneOf": [
        {"type":"object","required":["observation_id"],"properties":{"observation_id":{"type":"string","format":"uuid"}}},
        {"type":"object","required":["recording_id"],"properties":{"recording_id":{"type":"string","format":"uuid"}}},
        {"type":"object","required":["camera_id","time_from","time_to"],"properties":{
          "camera_id":{"type":"string","format":"uuid"},
          "time_from":{"type":"string","format":"date-time"},
          "time_to":{"type":"string","format":"date-time"}
        }}
      ]
    },
    "max_frames": {"type":"integer","minimum":1,"maximum":8,"default":4}
  }
}
```

Response schema. See section 5.2.

Cost. 1 VLM call per invocation. ~4 frames default at 720p ~= 1500-4000 input tokens depending on provider, plus ~300 output. Charge against the per-request VLM budget cap.
Idempotency. Pure read at the data layer, but expensive. Cache key = (target, normalized_question, frame_sample_strategy). See section 5.4.
Side effects. None to data. Logs to AgentRun.vlm_calls.

#### 4.2.5 `request_clarification`

Purpose. Special-cased "tool" that terminates the run with a question for the user instead of an answer.

Params schema.
```json
{
  "type": "object",
  "required": ["question","reason"],
  "properties": {
    "question": {"type":"string","maxLength":300},
    "reason": {"type":"string","enum":["ambiguous_person","ambiguous_time","outside_scope","insufficient_evidence"]},
    "candidates": {"type":"array","items":{"type":"string"}}
  }
}
```

Side effects. Marks AgentRun.status = `awaiting_clarification`; emits a `clarification` WS event; releases driver loop.

### 4.3 Tools we deliberately did NOT include in Phase 1

| Candidate | Why deferred |
|---|---|
| `get_camera_snapshot` (live frame) | Phase 1 is historical Q&A. Live snapshots invite "what is happening right now" which collides with the live dashboard already. Reconsider in Phase 3 for "is the kid awake right now". |
| `play_audio_clip_segment` | No good model surface for audio Q&A beyond existing transcript text. Transcripts are queryable via `query_observations`. |
| `cluster_lookup` (face/body cluster browse) | Power-user feature; the agent does not need to expose unnamed clusters in answers (would leak unconsented identities). |
| `trigger_recording` / `fire_rule` / `send_telegram` | Write tools. Out of Phase 1 by policy (section 15). |
| `regenerate_summary` | Already a UI action; not appropriate for agent to mutate. |
| `image_similarity_search` | Requires CLIP indexing the pipeline does not have yet. Phase 3. |
| `get_storage_stats` | Off-domain. The agent should not be answering questions about disk usage. |

---

## 5. The VLM analyzer

This is the single most important component of the agentic layer because it is the only path to answer questions about concepts the perception pipeline did not pre-index. It is also the most expensive, the most dangerous (hallucination, privacy), and the most likely to be misused by a chatty LLM that wants to "look at every clip just to be sure."

### 5.1 Frame sampling strategy

Inputs. A target (observation, recording, or camera+time-range), a question, a `max_frames` cap.

Decision tree.

1. If target is an Observation and the Observation has `clip_path` set, use that clip.
2. Else if target is a Recording id, use `recordings[id].file_path` resolved via the same helper at services/api/routes/recordings.py `_resolve_recording_path`.
3. Else if target is camera+time-range, find the Recording row(s) overlapping the window. If multiple, stitch (section 5.5).
4. Compute clip duration `D`.
5. Frame sampling:
   - If `D <= 5s`. take 2 frames evenly spaced (1/3, 2/3).
   - If `5s < D <= 30s`. take `max_frames` frames evenly spaced.
   - If `D > 30s`. take 1 keyframe per `D/max_frames` seconds, BUT prefer frames with high motion (use existing Observation rows in the window as motion anchors. Each Observation marks a "something happened here" moment).
6. Always include the Observation thumbnail (`thumbnail_path`) as the first frame when available. It has already been blurred for privacy and is free.

Decision. Default sampler is "even-spaced with Observation-anchored bias for clips >30s, capped at 4 frames." Rationale. Four frames at 720p fit comfortably in every provider's vision context, cost is bounded, and Observation-anchored sampling means we are looking at the moments the perception pipeline already flagged as interesting.

Rejected. Scene-change detection (pyscenedetect or histogram-diff). Too slow on cold clips, and the existing Observation rows are a cheaper "interesting moments" proxy.

Rejected. Always use Observation thumbnails only. Insufficient temporal coverage for questions like "did he take a bite" which requires multiple frames in sequence.

Implementation note. Use `ffmpeg -ss <t> -frames:v 1` rather than decoding with PyAV in Python. Faster, no extra dependency beyond what `services/perception/` already needs.

### 5.2 Question prompt template and response schema

Template (sent as system prompt).
```
You are a vision analysis tool for a household security camera system.
You will see one or more frames from a single camera clip.
Answer the user's question about what is visible in the frames.

Rules.
- If the frames are insufficient to answer, say so. Do not guess.
- Cite which frame index supports each piece of evidence.
- Confidence is your honest probability that your answer is correct given the
  visible evidence. Below 0.6 means you are not sure.
- If the question is about a person identity, only answer if there is
  unambiguous visual evidence. Otherwise return cannot_tell.

Question. {user_question}

Camera context. {camera_name}, {scene_mode}, {location_label}
Frames provided. {N} frames at timestamps {t1, t2, ...}
```

Mandatory response schema (enforced via the provider's structured-output feature where available; OpenAI `response_format=json_schema`, Anthropic tool-use schema, Gemini `responseSchema`, Ollama JSON mode).
```json
{
  "type": "object",
  "required": ["answer","confidence","evidence","cannot_tell"],
  "properties": {
    "answer": {"type": "string", "maxLength": 500},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "evidence": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["frame_index","what"],
        "properties": {
          "frame_index": {"type":"integer","minimum":0},
          "what": {"type":"string","maxLength":200}
        }
      }
    },
    "cannot_tell": {"type":"boolean"},
    "cannot_tell_reason": {"type":["string","null"]}
  }
}
```

`cannot_tell=true` is a first-class outcome. The agent must treat it as "no evidence", not as a negative answer.

### 5.3 Hallucination gate

Confidence policy in the agent driver.

| confidence | cannot_tell | Driver treats as |
|---|---|---|
| any | true | Negative evidence. "No determination from this clip." Do not retry the same clip with the same question. |
| < 0.4 | false | Discard. Treat as cannot_tell. |
| 0.4 - 0.6 | false | Keep but tag as "weak". Final answer must phrase as "I think" or "possibly". |
| > 0.6 | false | Strong. May phrase as definitive in the final answer. |
| > 0.9 | false | Strong. Same as above; do not add hedging. |

Phrasing in the synthesis prompt is enforced by passing the strength tags as structured input to the final composition call. The synthesis model does not see raw confidence numbers, only "strong" / "weak" / "none" tags per evidence item.

Decision. The agent never tells the user "no" based on a single `cannot_tell`. it tries up to 2 more analyzer calls with different frame samples or a reframed question before concluding "no evidence found." Caps at the per-request VLM budget regardless.

### 5.4 Caching

**Decision (locked by product).** Cache lives at the FRAME level, not the household / user / question level. Every successful VLM analyzer call writes a row to a new `vlm_frame_analysis` table keyed by `(observation_id OR recording_segment_hash, question_hash, provider_id, model)`. Rows persist for the life of the underlying media — no TTL.

Schema sketch.

```
vlm_frame_analysis (
  id uuid PK,
  observation_id uuid FK -> observations.id ON DELETE CASCADE,
  recording_id uuid FK -> recordings.id ON DELETE CASCADE (nullable; one side set),
  question_hash varchar(64),   -- sha256 of normalized question text
  question_text text,          -- raw, for audit + debugging
  provider_id uuid FK -> providers.id ON DELETE SET NULL,
  model varchar(128),
  response_json jsonb,         -- the structured analyzer response
  confidence float,
  cost_tokens_in int,
  cost_tokens_out int,
  cost_cents int,
  created_at timestamptz default now(),
  UNIQUE (observation_id, question_hash, provider_id, model)  -- partial unique
)
```

Rationale. If we burned tokens analyzing frame F for "is the person eating?", that judgment about F is permanent. Any future user, in any future question, that finds itself looking at frame F for an eating-related question gets the cached answer for free. The cache key is the FRAME + QUESTION, not the household + question. This is the asymmetry. Work is per-frame; the same frame answers many questions.

Lookup. Before each analyzer call, the analyzer checks the table. Cache hit = the analyzer skips the VLM, returns the cached JSON, and the AgentRun records `cached: true` in the audit trail.

Question normalization for the hash. Lowercase, strip punctuation, collapse whitespace. Still exact-string. No semantic cache (see below).

Cross-user note. Cache rows are household-scoped because the underlying observations + recordings are household-scoped; there is no cross-household leakage. Within a household, any user benefits from any prior user's analyzer work.

Rejected. Semantic (embedding-similarity) question cache. Two reasons. (1) cheap cache key matters more than recall here because cache misses are tolerable while a false hit ("did dad eat?" served for "did dad sleep?") is catastrophic. (2) embedding-cache lookups themselves cost an embedding call.

Rejected. TTL-based cache. Frames do not change; their VLM answer does not need to expire. Provider model upgrades are addressed by keying on `provider_id + model`, so a new model produces a new row alongside the old.

Cache invalidation. Single rule. Cache rows die with their media. Foreign key `ON DELETE CASCADE` from both `observations.id` and `recordings.id`. When the retention loop deletes a Recording mp4, all `vlm_frame_analysis` rows for it vanish in the same transaction. When perception eventually prunes old Observations (Phase 2 retention), their cache rows go with them. AgentRuns that cite a now-deleted cache row keep their textual citation but lose the playback + re-verify link.

### 5.5 Long-clip stitching

When a logical event spans multiple Recording rows (the ingestion service rolls files every ~5 minutes), the analyzer must treat them as one clip.

Decision. The analyzer accepts a `camera_id + time_from + time_to` target. It looks up all Recording rows whose `[started_at, ended_at]` intersects the window, sorts by start time, samples frames from each in proportion to its intersection length with the window, and merges into one ordered list capped at `max_frames`. Frame indices in the VLM response remain global to the merged list.

Rejected. Ffmpeg concat then sample. Adds I/O overhead and re-encode risk. Per-file `ffmpeg -ss` is faster.

---

## 6. Privacy gates

The agent's ad-hoc VLM path bypasses the perception pipeline at services/perception/pipeline.py, which today is the only place that applies privacy redaction before frames leave the box. The agent MUST re-apply the same redaction layers before any frame bytes are sent to a VLM provider, whether cloud or local.

### 6.1 Mandatory pre-VLM redaction pipeline

In order, every time:

1. **Per-camera privacy zones.** Apply `PrivacyZone` rows for the source camera via `apply_privacy_blur` from services/perception/privacy.py. Filter to zones with `active=true` and (`source='manual'` OR `locked=true` OR `last_seen_at` within `stale_after_seconds`).
2. **Per-Person privacy_blur.** For every face detected in the frame whose embedding matches a `Person.privacy_blur=true`, blur the face bbox. Implementation. Reuse the existing face-recognition pipeline's blur step. Sourced via the same code path as the recording-blur worker.
3. **Nudity safety floor.** Always run NudeNet blur on agent frames, regardless of the `nudity_blur` AppSetting. The agent is not allowed to disable this. Cloud providers' TOS uniformly require it.
4. **Audit stamp.** Stamp the frame metadata with `{"redaction_applied": [...]}` and log to `AgentVLMCall.redaction_log`.

### 6.2 Local vs cloud redaction policy

Decision. Apply identical redaction whether the target VLM provider is cloud (OpenAI, Anthropic, Gemini) or local (Ollama). Rationale. (1) the user's expectation of "blurred" is set by the perception pipeline; the agent must not silently relax it; (2) consistency makes debugging tractable; (3) Ollama models running locally still potentially log frames to a debug directory, and the household member who configured the camera privacy expects it everywhere.

Rejected. Skip redaction for local Ollama. Saves a few ms of CPU but breaks the invariant.

### 6.3 Audit

Every `analyze_clip_with_vlm` call writes an `AgentVLMCall` row containing:
- `vlm_call_id` (uuid pk)
- `agent_run_id` (fk)
- `target` (json, the resolved Recording/Observation/window)
- `question` (text)
- `frame_count`
- `frame_timestamps` (json)
- `redaction_log` (json) e.g. `{"privacy_zones": 2, "blurred_person_ids": ["p1","p2"], "nudenet_regions": 0}`
- `provider_id`, `provider_name`, `model_name`
- `input_tokens`, `output_tokens`, `cost_cents`
- `response` (full structured response)
- `cached` (bool)
- `latency_ms`
- `created_at`

The user-facing "Why did Nurby say this?" view (section 11) renders the redaction_log so the household can verify their privacy settings were honored.

### 6.4 Hard blocks

The agent MUST refuse to run `analyze_clip_with_vlm` when:
- The target Recording's source camera has any `PrivacyZone` row that failed to load (DB error). Fail closed.
- The target Recording's `blur_status != "done"` and the camera has any Person with `privacy_blur=true`. Wait until the post-blur worker finishes, or fail.
- The active VLM provider is misconfigured (no api_key, no base_url). Fail with explicit error to the user.

---

## 7. Cost and budget

### 7.1 Hard caps per request

| Cap | Default | AppSetting key |
|---|---|---|
| Input tokens (orchestration LLM) | 30000 | `agent_max_input_tokens_per_run` |
| Output tokens (orchestration LLM) | 4000 | `agent_max_output_tokens_per_run` |
| Tool turns | 12 | `agent_max_tool_turns_per_run` |
| VLM analyzer calls | 6 | `agent_max_vlm_calls_per_run` |
| Wall-clock seconds | 60 | `agent_max_wall_seconds_per_run` |

### 7.2 Per-user per-day budget

| Cap | Default | AppSetting key |
|---|---|---|
| Tokens per user per day | 500000 | `agent_user_daily_token_budget` |
| Cost per user per day (USD cents) | 200 | `agent_user_daily_cost_cents_budget` |
| Runs per user per hour | 60 | `agent_user_hourly_run_budget` |

### 7.3 Per-org alarm

A household-wide cost alarm fires a Notification (severity=warning) at 80% of `agent_household_daily_cost_cents_alarm` (default 1000), and a critical alarm + auto-pause at 100%.

Storage. All budget keys live in `AppSetting` (shared/models.py AppSetting). New keys added to the `DEFAULTS` map in shared/app_settings.py during Phase 1.

### 7.4 Failure mode when exceeded

When any cap is hit mid-run:

1. Driver stops the loop.
2. Driver requests one final synthesis pass against the LLM with whatever evidence is collected so far, prompted to "summarize partial findings and explicitly say you ran out of budget."
3. Final answer is returned with `status="budget_exceeded"`, `partial=true`.
4. The user sees the partial answer with a banner. "I ran out of budget while investigating. Findings so far. ..."
5. AgentRun row is marked accordingly.

Never. Silently truncate without telling the user.

### 7.5 Cost accounting

Cost in cents is computed per provider per call using a small price table (`services/agent/pricing.py`). Updated by hand when provider prices change; not auto-fetched. Per-call cost lands on `AgentVLMCall.cost_cents` and `AgentRun.total_cost_cents`. The orchestration LLM cost is the larger of the two for chatty questions; the VLM cost dominates for analyzer-heavy questions.

---

## 8. Identity and time grounding

### 8.1 Name resolution

Algorithm (in `resolve_person` tool):

1. Lowercase + strip the input name.
2. Exact match on `Person.display_name` (case-insensitive). Score 1.0.
3. Exact match on `Person.relationship` (case-insensitive, e.g. "Dad"). Score 0.95.
4. Substring of `display_name` (e.g. "Dan" matches "Daniel"). Score 0.7.
5. Edit-distance <= 2 of `display_name` (e.g. "Sara" vs "Sarah"). Score 0.6.
6. Alias map lookup (see below). Score 0.85.

Return all candidates with score >= 0.6. The LLM ranks and decides.

Decision. Add a new `Person.aliases` JSON column (list of strings) in Phase 1, migrated as an additive column with default `[]`. UI surface to edit aliases is deferred to Phase 2; in Phase 1 the column is editable via PATCH /api/persons/{id} only. The agent treats relationship + display_name + aliases as the matchable space.

Rejected. Maintain a separate `PersonAlias` table. Overkill for what is effectively a 1-to-N list per Person. JSON column is simpler and matches the existing pattern (e.g. `Camera.detection_models`).

Disambiguation flow. If `resolve_person` returns 2+ candidates with scores within 0.1 of each other, the LLM should call `request_clarification` with reason=`ambiguous_person` and candidates=`[display_name list]`. The LLM is instructed in its system prompt to never pick silently between equally-scored candidates.

### 8.2 Time resolution

Sources. Household timezone (AppSetting `system_timezone`) is the primary anchor. Per-camera `Camera.timezone` overrides when a question is about a specific camera in a different time zone (vacation cabin scenario).

Algorithm (in driver, before passing the question to the LLM, AND inside `query_observations` when the LLM passes natural-language time hints).

1. Get household tz. Fall back to UTC if unset.
2. Get `now` in household tz.
3. Map phrases:
   - "today" -> [today 00:00, now]
   - "yesterday" -> [yesterday 00:00, yesterday 23:59:59]
   - "this morning" -> [today 06:00, today 12:00]
   - "this afternoon" -> [today 12:00, today 17:00]
   - "this evening" -> [today 17:00, today 22:00]
   - "tonight" / "last night" -> [yesterday 18:00, today 06:00]
   - "last weekend" -> [last Saturday 00:00, last Sunday 23:59]
   - "last week" -> [Monday of previous week 00:00, Sunday of previous week 23:59]
   - "an hour ago" / "5 minutes ago" -> sliding window of given length anchored at now
4. If the question references a specific camera and that camera has its own `Camera.timezone`, recompute the window in that camera's tz.

Decision. Time resolution happens twice. Once eagerly in the driver (so the LLM's first orientation includes resolved windows for any time phrases in the question), and once on demand inside `query_observations` when the LLM passes a freeform `time_phrase` parameter (added to the schema in Phase 2; Phase 1 only accepts ISO datetimes).

Decision. Reject ambiguous times. If the user says "earlier" with no anchor and no prior turn context, call `request_clarification` with reason=`ambiguous_time`.

### 8.3 Why a hard time grounding step matters

The cost of a misresolved "yesterday" is a 24h-shifted answer, which is silently wrong and undetectable to the user. Time bugs are the most embarrassing class of failure in production camera Q&A and they justify a separate algorithm rather than trusting the LLM.

---

## 9. Multi-camera reasoning

### 9.1 How the agent picks cameras

The LLM sees the full camera list from `get_household_context` and infers which cameras are relevant. Eg. "did Dad eat" -> filter to indoor cameras with a kitchen-y label or role hint. "Did the cat go outside" -> filter to `scene_mode=outdoor`.

### 9.2 Camera role hints

Today there is no formal "role" column on Camera. The `location_label` field is free-text and not guaranteed semantic.

Decision. Add a derived `role_hint` field computed at query time in `get_household_context`, populated by a tiny keyword classifier over `Camera.name` and `Camera.location_label`. Bucket into: `kitchen`, `living_room`, `bedroom`, `bathroom`, `entry`, `driveway`, `yard`, `garage`, `office`, `nursery`, `other`. Bucket "other" if no keyword matches.

Rejected. Adding a `Camera.role` enum column. Schema churn for a bootstrap heuristic; user can override per-camera by editing `location_label`. We can promote to a real column in Phase 2 if the heuristic underperforms.

Rejected. Asking the user to label every camera with a role on first run. Too much friction.

**OPEN.** Should the role classifier be VLM-driven (one-shot prompt over the camera name + most-recent thumbnail) at first-boot to be more robust than keyword matching? Recommendation. Yes, in Phase 2. Phase 1 ships the keyword classifier.

### 9.3 Cross-camera journeys

The `Journey` table (shared/models.py Journey) already stitches a single subject across cameras. The agent surfaces this via `query_observations(kinds=["journey"], person_id=...)`. The LLM is told in its system prompt that a Journey row is the cheapest way to answer "where did X go".

---

## 10. Streaming protocol

### 10.1 Channel

Decision. New WebSocket route `GET /ws/agent/{run_id}?token=...`. Does not reuse the existing `/ws` broadcast channel because (1) `/ws` is a household-wide event firehose and the agent's trace is per-user; (2) per-run subscriptions let the frontend tab refresh and rejoin a specific run.

Backing. Redis pub/sub channel `agent:{run_id}`. Driver publishes; the WS handler subscribes when a client connects. Last 100 events also persisted to `AgentRunEvent` (so a reconnect can replay from the cursor; see 10.3).

### 10.2 Message types

All messages JSON, with required fields `type`, `run_id`, `ts`, `seq` (monotonic int per run).

| type | payload fields | when emitted |
|---|---|---|
| `plan` | `summary` | After first LLM turn that produces a plan |
| `tool_call_start` | `tool`, `params`, `call_id` | Just before a tool runs |
| `tool_call_result` | `call_id`, `summary`, `truncated_payload` | When a tool returns |
| `vlm_call_start` | `target`, `question`, `call_id` | Specialization of tool_call_start for analyzer |
| `vlm_call_result` | `call_id`, `answer`, `confidence`, `cannot_tell` | Specialization for analyzer |
| `partial_text` | `delta` | Token streaming from the synthesis LLM call |
| `final_answer` | `text`, `citations`, `status`, `partial` | Loop terminated successfully or with partial |
| `clarification` | `question`, `reason`, `candidates` | Loop terminated awaiting user |
| `budget_warning` | `which_cap`, `used`, `cap` | Soft warning at 80% of any cap |
| `error` | `code`, `message`, `retryable` | Loop terminated with hard error |

The `truncated_payload` on `tool_call_result` is a short summary (first 5 rows for query tools, first 200 chars for analyzer). The frontend can expand it by hitting `GET /api/agent/runs/{id}/tool_calls/{call_id}` for the full row.

### 10.3 Reconnect and resume

Decision. The WS endpoint accepts a `?after_seq=<int>` query param. On connect the handler replays missed `AgentRunEvent` rows in order, then attaches the live Redis subscriber. If the run terminated while disconnected, the handler emits the final-state event and closes.

Decision. If a run has been idle for >5 minutes without any new event AND its status is `running`, it is presumed dead and the next connect attempt sees `status=failed, reason=stalled`. Dead-run detection runs in a small janitor task in the API process.

---

## 11. Audit and eval

### 11.1 AgentRun schema

New table `agent_runs`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK users | |
| `question` | Text | Verbatim user input |
| `question_hash` | String(64) | sha256 of normalized question. Dedupe + cache lookup |
| `parent_run_id` | UUID FK agent_runs nullable | For multi-turn follow-ups (section 14) |
| `plan` | JSON nullable | First LLM plan text |
| `tool_calls` | JSON | List of {call_id, tool, params, result_summary, latency_ms, error} |
| `vlm_calls` | JSON | List of {call_id, target, question, confidence, cached, cost_cents} |
| `total_input_tokens` | int | |
| `total_output_tokens` | int | |
| `total_cost_cents` | int | |
| `final_answer` | Text nullable | |
| `final_status` | String(32) | running, completed, awaiting_clarification, budget_exceeded, failed, stalled |
| `failure_reason` | String(255) nullable | |
| `citations` | JSON | List of {kind, id, claim_idx} |
| `started_at` | DateTime | |
| `ended_at` | DateTime nullable | |
| `latency_ms` | int nullable | |

Companion table `agent_vlm_calls` for the heavier VLM-call audit (schema in 6.3).

Companion table `agent_run_events` for the trace replay (schema. `run_id`, `seq`, `ts`, `type`, `payload`).

### 11.2 User-facing audit page

`/agent/runs/{id}` route in the frontend. Renders.

- The original question.
- The plan.
- A timeline of tool calls + analyzer calls with collapsible payloads.
- For each analyzer call. The blurred frames as thumbnails, the question sent, the response received, the redaction log.
- The final answer with citation links.
- Cost summary.

Linked from every final answer via "Why did Nurby say this?".

### 11.3 Eval suite

Location. `tests/agent_fixtures/*.yaml`.

Format.
```yaml
id: cat_outside_last_night
question: "Did the cat go outside last night?"
fixture_state: small_household_v1   # name of a DB seed
expected_substrings_any_of:
  - "indoor"
  - "did not"
  - "no outdoor"
must_cite_kinds: ["observation"]
forbid_substrings:
  - "definitely"
  - "I'm sure"
max_tool_calls: 6
max_vlm_calls: 2
expected_final_status: completed
```

CI strategy. Nightly job runs the full suite against a pinned fixture DB (loaded from `scripts/seed_demo_data.py` plus a curated supplement). The orchestration LLM is the real LLM call (cached on disk per `(model, prompt) -> response` so reruns are deterministic until prompts change). The VLM analyzer in CI is mocked. It returns canned structured responses keyed by `(recording_id, question_hash)` from a `tests/agent_fixtures/vlm_responses/*.yaml` file.

Decision. The 30 initial fixture questions span. 6 indexed-concept questions, 8 unindexed-VLM-required questions, 6 person-disambiguation questions, 4 time-resolution edge cases, 3 no-evidence cases, 3 out-of-scope cases. Each fixture is owned by a named team member who is on the hook to update it when the household DB schema changes.

Pass criterion. Phase 1 ships when >= 27/30 pass.

---

## 12. Model routing

### 12.1 Orchestration model

The model running the tool-use loop. Picks tools, drafts plans, decides when to stop, composes synthesis.

**Decision (locked by product).** The user picks the orchestration model PER QUESTION. The chat input ships a model selector chip beside the send button, defaulting to whatever the user last picked. The platform never makes this choice on the user's behalf. No silent default; the selector must be visible. Selector options are drawn from the household's configured Providers (auth.tsx fetches `/api/providers`) filtered to those whose `kind` advertises tool-use support (claude family, gpt family, gemini family, ollama models with native tool-use). Selector remembers the last pick in localStorage per user.

UI requirement. The selected model + provider name is rendered on the AgentRun audit trace so users see exactly which model answered each question, and so cost accounting attributes correctly.

Rejected. Hard-coded "Claude Sonnet 4.x default" was the original recommendation; product overruled because Nurby's positioning is provider-agnostic and choosing for the user contradicts that. We do not gate on a provider we picked.

Rejected. Always use the household's existing VLM provider. The VLM provider chain is configured for vision and may be a model that handles tool-use poorly; forcing it would degrade orchestration quality silently.

### 12.2 Synthesis model (final answer composition)

Decision. Same model as orchestration. Do NOT escalate to Opus for synthesis in Phase 1. Reason. Doubles cost on every run for marginal quality gain on a 1-paragraph answer. Reconsider in Phase 2 if eval shows synthesis is the weak link.

### 12.3 VLM analyzer model

Decision. Resolve in this order per call. (1) source camera's `Camera.vlm_provider_id` if set, (2) the global active provider returned by `services.perception.vlm.get_active_provider`. This reuses the existing chain so a household that picked Gemini for one camera keeps using Gemini for analyzer calls on that camera's footage. Same model as the perception pipeline = same TOS exposure, same cost profile, no surprise to the user.

### 12.4 Cheap path for single-fact questions

Decision. No cheap-path shortcut in Phase 1. Every request goes through the full agent loop. Rationale. Detection of "trivial" questions ("is the camera online", "when was the last motion") is itself an LLM call that costs ~as much as a Sonnet tool-use turn, and the wins on rare trivial questions do not pay back the complexity. Reconsider in Phase 2 with telemetry on actual question distribution.

Rejected. Route everything looking like "when did" or "how many" to a single SQL-generating Haiku call. Hard to bound for safety; SQL surface is too dangerous even read-only against the household DB.

---

## 13. Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| LLM loops on same tool call with same args | Driver tracks `(tool, sha256(params))` per run; second identical call returns a cached result; third trips a hard error "tool loop detected" and the synthesis runs on what we have. |
| Tool returns empty | First empty result is a normal observation. Two consecutive empty results across distinct tools triggers a soft "no evidence so far" hint to the LLM. |
| VLM 5xx | Retry once with exponential backoff (reuse `services.perception.llm_errors.call_with_retry`). On second failure, surface as a `vlm_call_result` with `error=true` and let the agent continue. |
| VLM rate limit | Treat as 5xx but with longer backoff. Mark `error.retryable=true` so the agent can re-queue later if it has budget. |
| User abandons mid-investigation (WS closes) | Driver continues to completion regardless (so the audit row finalizes). Result lands in AgentRun. User can revisit via history. After 60s wall-clock cap the loop terminates anyway. |
| Concurrent identical questions from same user | Dedupe via `question_hash` lookup. If a run with the same hash is `running` and started < 30s ago, return its run_id and join the same WS channel. |
| Off-domain question ("what's the weather") | LLM is system-prompted to call `request_clarification(reason="outside_scope")` instead of trying tools. |
| Adversarial / destructive question | LLM has no write tools to call. Even so, system prompt explicitly forbids interpreting questions as commands. "delete all observations" returns a polite decline with no tool calls. |
| LLM produces malformed JSON / tool args | Driver validates against the tool's JSON schema. On failure, returns the validation error as a tool_call_result and lets the LLM retry; counts against the tool turn budget. |
| Recording file missing on disk | `analyze_clip_with_vlm` returns `{error: "clip_missing", retryable: false}`. LLM should not retry. |

---

## 14. Conversation memory

### 14.1 Within-session multi-turn

A user follow-up like "how about yesterday" must inherit the subject ("the cat going outside") from the previous turn without re-asking.

Decision. New row in agent_runs with `parent_run_id` set to the previous run. The driver, when starting a run that has a parent, injects the parent's question + final answer + last 3 turns of tool results into the new run's initial system context. Capped at 2000 input tokens of prior context to avoid blowing the budget.

Decision. The frontend chat composer always sends `parent_run_id` of the previous turn when the user types a follow-up within 5 minutes; users can "start fresh" with an explicit button that drops the parent link.

Rejected. Reusing the existing `Conversation` model (shared/models.py Conversation). That model is for audio transcripts grouped by speech gaps. Semantically unrelated and would confuse anyone reading the schema.

### 14.2 Session TTL

A chat session expires after 30 minutes of inactivity. Expiry just drops the `parent_run_id` linkage in new messages; old runs remain queryable indefinitely.

---

## 15. Action vs observation distinction

Phase 1 is strictly read-only. Every tool is annotated with `side_effect: "read" | "write"` and the registry refuses to register a write tool unless explicitly allowed by an AppSetting (default disabled).

Future write tools (Phase 3 only). `add_event_note`, `acknowledge_event`, `snooze_rule`. None of these mutate camera or person data; all are user-confirmation-required.

Policy.

- Write tools MUST emit a `confirmation_required` event to the chat instead of executing immediately.
- The user clicks "Confirm" in the chat UI which POSTs back to the driver with the confirmation token.
- The driver then runs the write tool and emits a `tool_call_result` with the outcome.
- AgentRun audit captures the confirmation timestamp and the user_id of the confirmer.

Hard prohibition. The agent will never have access to camera config writes, person deletes, rule edits, or recording deletes. Those stay UI-only forever.

---

## 16. Phasing

### Phase 1. Foundation (must-have)

Scope.

- Tool registry with 5 tools above.
- Agent driver with budget enforcement, dedupe, audit logging.
- Streaming WS endpoint with resume.
- AgentRun + AgentVLMCall + AgentRunEvent tables (Alembic migration).
- "Ask Nurby" frontend page with trace panel and citation rendering.
- Audit page "Why did Nurby say this?".
- 30-fixture eval suite, nightly CI.
- Privacy redaction pipeline in analyzer (sections 6.1, 6.2, 6.4).
- AppSetting budget knobs (section 7).
- Person.aliases column.
- Camera role_hint keyword classifier.
- Documentation. /docs/agent-design.md (this), /docs/agent-runbook.md (ops), /docs/agent-prompts.md (the actual system prompts checked in for review).

Deliverables checklist.

- [ ] Migration `add_agent_tables`
- [ ] services/agent/driver.py
- [ ] services/agent/tools/{context,query,resolve_person,analyzer,clarify}.py
- [ ] services/agent/redaction.py
- [ ] services/agent/pricing.py
- [ ] services/agent/budgets.py
- [ ] services/agent/routes.py
- [ ] services/agent/ws.py
- [ ] frontend/src/app/ask/page.tsx (chat surface)
- [ ] frontend/src/app/agent/runs/[id]/page.tsx (audit surface)
- [ ] tests/test_agent_driver.py
- [ ] tests/agent_fixtures/*.yaml (30 fixtures)
- [ ] tests/agent_fixtures/vlm_responses/*.yaml
- [ ] CI workflow `.github/workflows/agent-eval.yml`
- [ ] Runbook + prompts docs

Exit criterion. >= 27/30 eval fixtures pass on three consecutive nightly runs. P95 wall-clock under 30s for non-analyzer questions, under 60s for analyzer questions. Zero P0 privacy bugs in audit logs after 1 week of internal dogfood.

### Phase 2. Polish + memory (should-have)

Scope.

- Conversation memory across turns (parent_run_id wiring + chat composer).
- Identity disambiguation UI (alias editor on Person detail page).
- VLM-driven camera role classifier (replaces keyword classifier).
- Expanded tools. `get_camera_snapshot` (live), `query_audio_in_detail` (full transcript text fetch).
- Cheap-path heuristic if telemetry justifies it.
- Eval suite expansion to 80 fixtures.
- Semantic question cache investigation (build a prototype, A/B against exact-string).
- Cost dashboard in /settings.

Exit criterion. >= 72/80 eval pass. Per-question p50 cost < 1 cent.

### Phase 3. Action + advanced retrieval (nice-to-have)

Scope.

- Image embeddings (CLIP) for visual concepts never described in text. Backfill thumbnails into a new `image_embeddings` table.
- Write tools with confirmation flow (`add_event_note`, `acknowledge_event`, `snooze_rule`).
- Multi-user shared agent sessions (one household member can see another's in-flight run if the same camera scope).
- Federated/voice surface (Telegram bot ask, voice ask via the audio_only mic surface).

Exit criterion. CLIP image search wins eval delta of >= 5 fixtures over text-only baseline; write tools have zero unauthorized mutations across 1 month of dogfood.

---

## 17. Open questions

Resolved by product. Recorded here as the source of truth that supersedes earlier sections.

1. **RESOLVED. Question caching policy.** Cache lives at the FRAME level, eternal, keyed by `(observation_id OR recording_id, question_hash, provider_id, model)`. Not per-household, not per-user, not per-question-string. Section 5.4 holds the authoritative spec. Rationale. Work is per-frame; the answer about a frame does not expire while the frame still exists. Foreign keys cascade so cache dies with its media. Within a household, all users benefit from any prior user's analyzer work.

2. **RESOLVED. Redacted analyzer thumbnails persist for the life of the underlying media.** Stored alongside `vlm_frame_analysis` rows under `thumbnails/agent/<run_id>/<frame_idx>.jpg`. Cascade delete with media. Audit page can show the exact frame the model saw, including the redaction overlays, which is critical for trust + debug.

3. **RESOLVED. Token charging follows the per-user daily budget from day one.** `AppSetting.agent_daily_token_budget_per_user` default 500_000 tokens, `agent_daily_cost_cents_per_user` default 500 cents. Warn at 80%, soft-block at 100% with friendly message + admin override. Self-hosted users can raise the cap freely in settings. Hosted billing is out of scope for v1.

4. **RESOLVED. Retention vs analyzer.** Retention is the source of truth. Cache rows are bound to their media via `ON DELETE CASCADE`. If the user asks about a window whose recording was already evicted by retention, the analyzer tool returns `{error: "clip_evicted"}` and the agent reports honestly that the footage was no longer available. Section 5.4 carries this in the schema; section 6.4 keeps it in the hard-blocks list.

5. **RESOLVED. Visual privacy and audio privacy are independent in v1.** Visual `privacy_blur=true` redacts frames before VLM; transcripts are NOT redacted in v1. Ship `Person.audio_redact` boolean migration as part of v1 schema (defaults false) so v2 can flip it on without another migration; tool layer reads it now and is a no-op until v2 surfaces it. Audit page tags transcript citations with `audio_redact_available: false` so the gap is visible.

6. **RESOLVED. Orchestration model.** User picks per question via a model selector chip in the chat input. The platform never sets a silent default. Selector is fed by `/api/providers` filtered to those advertising tool-use. Last pick is remembered in localStorage per user. Section 12.1 holds the authoritative spec.

7. **RESOLVED. No separate global gate.** Every tool filters results through `UserCameraAccess` automatically; viewers see and ask about the cameras they already have visual access to. Admins have the full surface. The agent does NOT introduce its own permission concept.

8. **RESOLVED. Stream synthesis tokens, atomic tool results.** Matches the Cursor pattern users expect. Tool calls + tool results land as discrete WS frames so they render as collapsible cards in the trace; only the final synthesis paragraph streams token-by-token so the user sees progress on the answer text.

9. **DEFERRED to Phase 3.** Telegram `/ask` is high value but the v1 surface is the web chat at `/ask`. Phase 3 wires the same agent driver to a Telegram entry point. No design changes needed in v1 to enable later.

10. **RESOLVED. Audit page visibility.** All household admins see every AgentRun's audit page, not only the asker. The query asker sees their own runs by default in their personal Q&A history. Admins get a household-wide view at `/agent/admin/runs` for oversight. Builders should gate `/agent/runs/{id}` on `current_user.role == "admin" OR current_user.id == run.user_id`.

11. **RESOLVED. Cloud VLM TOS modal.** Not required. The user opted into cloud VLM exposure when they configured the Provider during initial setup. The agent reuses the existing provider configuration verbatim; no separate consent flow is needed for the agent surface. The doc's section 18.1 risk wording should be softened (operator-disclosure, not first-use-modal).

---

## 18. Risks and non-obvious gotchas

### 18.1 Cloud VLM provider TOS

OpenAI, Anthropic, and Google each have data-use clauses that vary. Household footage may or may not be covered under "consumer" vs "enterprise" agreements. The agent increases the rate of cloud VLM calls compared to baseline perception.

Consent posture (locked by product). Configuring a cloud Provider in Nurby is the user's consent surface. No first-use modal is added for the agent because the agent reuses the existing provider chain that the user already opted into during setup. Cloud exposure has the same shape as today's perception VLM calls; the agent just makes more of them.

Mitigation that we still ship. (a) the existing privacy redaction pipeline blurs Persons flagged `privacy_blur=true` before frames leave the box; (b) audit trail records exactly which provider received which frame for every agent run, so a household admin can review; (c) docs recommend Ollama for households uncomfortable with cloud exposure. We do NOT gate the agent behind a separate consent click.

### 18.2 Liability of automated synthesis

"Nurby said dad ate dinner, but actually he choked silently and the camera missed it." The agent must never imply 100% certainty about safety-critical questions. The hallucination gate (5.3) is the technical answer. The product answer is a permanent footer disclaimer on every agent answer. "Nurby answers from camera evidence and can be wrong. Do not rely on Nurby for safety-critical monitoring."

### 18.3 Cost ceiling is mandatory

A chatty user can drive an unbounded number of agent runs. Without per-user per-day caps the worst-case household VLM bill is unbounded. Section 7 caps are not optional. They ship in the first migration.

### 18.4 Eval drift

Fixture ground truth depends on the seeded DB state, which depends on the demo data scripts at scripts/seed_demo_data.py. When that script changes (which it does as new features land) the fixtures may silently rot. Mitigation. Version the fixture set against the seed script version. CI fails if the seed version on disk does not match the fixture version's expected seed version. Each fixture file has a `fixture_state: small_household_v1` header (section 11.3) that names the required seed.

### 18.5 The agent will misuse the analyzer

Without guard rails, an LLM will call `analyze_clip_with_vlm` ten times on the same clip with slightly reworded questions because it is uncertain. The dedupe in 13 plus the 6-call per-run cap is what prevents this. Watch this metric in production; if VLM calls per run climbs above 4 on average, tighten the system prompt.

### 18.6 Privacy zones can be stale

`PrivacyZone` rows have a `stale_after_seconds` freshness gate (default 60s). The agent might be analyzing footage from yesterday whose privacy zones were last refreshed during recording. Decision. For analyzer calls, treat any non-`locked`, non-`manual` privacy zone whose `last_seen_at` is before the clip's `started_at - 60s` as still-active for the clip if the clip is within 24h of the row's last seen. Older. Fall back to NudeNet + face blur only. Document this limitation in the audit log.

### 18.7 Multi-user race on AgentRun

Two household members can simultaneously ask similar questions. The driver dedupes only within the same user (section 13). A cross-user dedupe is possible but risks leaking one user's question text to another user's audit page. Decision. No cross-user dedupe in Phase 1.

### 18.8 Ollama responses do not always honor JSON mode

Local models (especially older moondream / llava versions) sometimes ignore the JSON-mode hint and emit a chat reply. The analyzer must parse defensively. On JSON parse failure, log the raw response, treat the analyzer call as `cannot_tell=true` with a `parse_failure` reason, and let the agent fall through to other evidence.

---

## 19. Glossary

**Agent.** The runtime entity that takes a user question and produces an answer by calling tools in a loop. In code. One execution of `services/agent/driver.run(question)`.

**Tool.** A typed callable registered in the agent's tool registry with a JSON-schema-validated input and output. The LLM picks tools by name; the driver executes them and feeds the result back.

**Tool call.** One invocation of a tool. Has a call_id, params, result, latency, cost.

**Agent run.** One end-to-end execution. Maps 1:1 to an `agent_runs` row. Contains many tool calls.

**Analyzer.** Shorthand for `analyze_clip_with_vlm`. The expensive tool that runs an ad-hoc VLM call against historical footage.

**Evidence citation.** A `(kind, id)` pair attached to a final-answer claim, where `kind` is one of observation, recording, journey, vlm_call. The frontend renders citations as clickable links.

**Semantic cache.** A cache that retrieves by embedding similarity instead of exact key match. Phase 1 does NOT use one; section 5.4 explains why.

**Confidence threshold.** The numeric cutoff (0.4, 0.6) used by the driver to classify analyzer responses as discard, weak, or strong (section 5.3).

**Redaction log.** The per-VLM-call record of which privacy mechanisms applied to the frames before they left the box. Stored on `agent_vlm_calls.redaction_log`.

**Budget cap.** A hard numeric ceiling on tokens, dollars, tool turns, VLM calls, or wall-clock seconds. Configured in AppSetting. Always enforced.

**Role hint.** The derived bucket (kitchen, living_room, etc.) classified from a camera's name and location_label, exposed to the LLM via `get_household_context`. Heuristic in Phase 1, VLM-driven in Phase 2.

**Side effect.** A tool's annotation as `read` or `write`. Phase 1 ships only `read` tools. Write tools require user confirmation in chat.

**Partial answer.** A final answer that was forced to terminate early due to a budget cap. Always labelled `partial=true` in the AgentRun row and in the UI.

**Parent run.** The AgentRun that immediately preceded the current run in a multi-turn chat. Used to inherit subject context for follow-ups like "how about yesterday".

**Stalled run.** A run whose status is `running` but has emitted no events for >5 minutes. Janitor marks it failed.

**Fixture.** A YAML test case in tests/agent_fixtures/ describing a question, the expected DB seed, and assertions on the final answer. Run nightly in CI.

**Disambiguation.** The flow where `resolve_person` returns multiple candidates and the LLM calls `request_clarification` to ask the user which one they meant.

**House timezone.** The IANA timezone string in `AppSetting.system_timezone`. Falls back to the perception host's locale when unset. The default anchor for relative time phrases like "today".

---

## Appendix A. File layout to be created

```
services/agent/
  __init__.py
  driver.py            # the agent loop
  routes.py            # POST /api/agent/ask, GET /api/agent/runs/{id}
  ws.py                # WS /ws/agent/{run_id}
  budgets.py           # cap enforcement
  pricing.py           # provider pricing table
  redaction.py         # pre-VLM blur pipeline
  prompts.py           # system prompts (checked in)
  tools/
    __init__.py
    registry.py
    context.py         # get_household_context
    query.py           # query_observations
    resolve_person.py
    analyzer.py        # analyze_clip_with_vlm
    clarify.py         # request_clarification
  models.py            # AgentRun, AgentVLMCall, AgentRunEvent (re-exported into shared/models.py)
tests/
  test_agent_driver.py
  test_agent_redaction.py
  test_agent_budgets.py
  agent_fixtures/
    *.yaml
    vlm_responses/
      *.yaml
frontend/src/app/
  ask/
    page.tsx
    components/Trace.tsx
    components/Answer.tsx
  agent/runs/[id]/page.tsx
docs/
  agent-design.md      # this
  agent-runbook.md     # ops
  agent-prompts.md     # system prompts dump
```

## Appendix B. Migration sketch

One Alembic migration `add_agent_tables`.

- create `agent_runs`
- create `agent_vlm_calls`
- create `agent_run_events`
- add column `persons.aliases JSON DEFAULT '[]'::json NOT NULL`
- insert AppSetting rows for all `agent_*` budget keys (idempotent upserts in migration body)

No data backfill. No destructive changes. Safe to roll forward and back.

## Appendix C. Out-of-scope confirmation

The following are deliberately NOT part of this design, to bound Phase 1.

- Voice input for questions.
- Telegram bot ask command.
- Email-summarization scheduling.
- Cross-household federation.
- Fine-tuning any model on household data.
- Learning from user corrections.
- A "training mode" UI.
- LLM-driven camera role classification (deferred to Phase 2).
- CLIP image embeddings (deferred to Phase 3).
- Write tools of any kind (deferred to Phase 3).

End of document.
