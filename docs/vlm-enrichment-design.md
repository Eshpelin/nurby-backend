# Idle-time VLM enrichment (versioned multi-pass)

Status: design. Not yet implemented.

## Goal

Spare VLM capacity is wasted. A self-hosted box with a local model sits
idle most of the night, while the daytime captions it produced under load
are often thin (one rushed sentence, or none at all because the backlog
shed the frame). The idea: when the live feed is quiet and the VLM has
nothing urgent to do, walk back over already-captured frames and run
**additional passes** over them to extract detail the first pass missed.
Keep the hardware busy and well-used instead of idle.

Every pass is recorded as its own versioned record against the frame
(pass 1, pass 2, pass 3, ...), so nothing is overwritten and the history
of what the model saw, and when, is auditable and reversible.

## What exists today to build on

- `services/perception/vlm.py` — `VLMClient`, `get_active_provider()`. async httpx, retry/backoff.
- `services/perception/vlm_queue.py` — Redis-backed per-camera backlog (50/cam), a priority lane, pHash dedupe, telemetry, `vlm_late` flagging.
- `services/perception/audio/vlm_enrichment.py` — precedent: re-feeds a stored thumbnail to the VLM with extra context and patches the description. But it is event-driven (audio), single-layer (overwrites `vlm_description`), not versioned.
- `Observation` columns: `vlm_description`, `primary_vlm_description` (original before a single refine), `refined_by_provider_name`, `refined_at`, `vlm_late`, `vlm_enqueued_at`, `thumbnail_path`, `description_embedding`.
- Frame availability: the analyzed frame is saved to disk as the observation thumbnail. Older frames beyond that are not retained, so enrichment operates on the stored thumbnail, not arbitrary raw video (unless a recording overlaps, in which case adjacent frames can be sliced with ffmpeg).

The gap: no idle scheduler, no multi-lens passes, no versioned pass storage.

## Storage. versioned passes

New table `observation_vlm_passes` (append-only):

| column | type | note |
|---|---|---|
| id | uuid pk | |
| observation_id | uuid fk -> observations | indexed |
| pass_no | int | 1, 2, 3, ... monotonic per observation |
| lens | string(32) | `live`, `attributes`, `temporal`, `anomaly`, `reduce` |
| prompt_version | string(16) | so prompt changes are traceable |
| provider_name | string(64) | |
| model | string(128) | |
| description | text | the prose this pass produced |
| attributes | json | structured fields. objects, colors, text/plates read, counts, time-of-day cues |
| confidence | float \| null | self-reported or derived |
| superseded | bool | true once a later reduce reconciles it |
| created_at | timestamptz | |

`Observation.vlm_description` stays as the **authoritative current best**
(what the UI, search, and rules read). It points at whichever pass the
reduce step blesses. Passes are the layered audit trail behind it.

Backfill: the existing live caption becomes `pass_no=1, lens='live'` for
every observation that has one, so history starts complete.

Retention: passes age out with their observation (cascade), and a per-pass
cap (keep at most N passes per observation, default 5) bounds growth.

## When it runs. idle detection + budget

Enrichment must never compete with live work.

- **Trigger:** any of (a) a configured quiet window (e.g. 01:00–05:00 local), (b) the live VLM backlog has been ~empty for N seconds across all cameras, (c) optional manual "enrich now" button.
- **Priority:** enrichment jobs go in the **lowest** priority lane of the existing queue and are preempted instantly by any live frame. A live keyframe always wins.
- **Budget:** reuse the STT-budget pattern (Redis hourly counter). `vlm_enrichment_budget_minutes_per_hour`, default modest. On a GPU box raise it. On CPU keep it low. Also pause on high CPU/thermal/battery if those signals are available.
- **Cadence:** process oldest-thinnest first so a busy night still improves the most-deficient records.

## What it enriches. candidate selection

Rank candidates by need, do not re-chew rich ones:

- observations whose only caption is short / generic / `vlm_late`
- observations with no VLM caption at all (backlog shed them under load)
- incidents/journeys missing a summary
- frames a cheap signal flagged interesting (motion spike, unknown face, unusual hour) that never got a VLM pass
- skip anything with `pass_no >= max_passes` or enriched within the last cooldown window

## Multi-pass. each pass a different lens

Passes are not the same prompt repeated. Each asks a different question,
and later passes get more context.

1. **`live`** (pass 1, already exists): fast "what's happening".
2. **`attributes`**: exhaustive extraction. every object, read any text / plates / signage, clothing colors, people count, time-of-day cues. Writes structured `attributes`, which directly improves search and rules.
3. **`temporal`**: feed the adjacent frames (sliced from an overlapping recording if present) so the model reasons about motion and intent. approached or left, loitering, carrying something out.
4. **`anomaly`**: a safety/oddity lens. "anything unusual or worth flagging that earlier passes missed."
5. **`reduce`**: reconcile all passes into one authoritative description + confidence, set `Observation.vlm_description` to it, mark superseded passes.

Stop early when a pass adds no new entities (delta-based), or at `max_passes`.

## Agentic follow-ups (what makes it more than a cron)

The reduce step can emit follow-up work based on what it found:
- a partially-read plate -> queue a higher-res crop or neighbor frames to complete it
- an unknown face -> bump it up the clustering queue
- a flagged anomaly -> raise an event / notify per existing rules

This is the plan -> act -> observe loop, not just batch reprocessing.

## Anti-hallucination

Re-asking invites invention (in manual testing the model confidently
reported a "Christmas tree" and a "drone" that may not have been there).
Guards:
- passes are **append-only**; a later pass never silently rewrites an earlier one.
- the `reduce` step is conservative. it only promotes a detail to authoritative if it appears consistently or with high confidence.
- optional **verify** pass: a second model/prompt tries to refute a new claim before it is promoted (reuse the agent's adversarial-verify pattern). Majority-refute kills it.
- keep the original `live` caption forever so any enrichment is reversible.

## Privacy + retention

- respect per-person privacy blur and consent. do not enrich frames of people who opted out.
- never enrich data already slated for deletion (inside the retention window only).
- enrichment is opt-in per install, off by default, same as other privacy-sensitive features.

## Phasing

- **v2.0** idle backfill of thin/missing captions. one extra `attributes` pass, lowest priority, budget-capped, versioned storage + backfill of pass 1. Proves the scheduler, the table, and the provenance.
- **v2.1** structured `attributes` feeding search and rules. a "VLM history" view in the observation detail UI showing the passes.
- **v2.2** `temporal` adjacent-frame reasoning + agentic follow-ups (complete a plate, chase a face).
- **v2.3** `reduce` + `verify` reconciliation to fight hallucination.

## API / UI surface

- `GET /api/observations/{id}/vlm-passes` -> the ordered passes for a frame.
- Observation detail UI gains a collapsible "VLM history" showing pass_no, lens, model, time, and the text/attributes each produced.
- Settings: enable toggle, quiet window, budget minutes/hour, max passes.

## Open questions

- Promote-vs-append: should `reduce` ever correct the authoritative caption, or only ever append and let the UI show the latest? (leaning: append always, reduce sets the authoritative pointer, original live caption preserved.)
- Frame source for `temporal`: only when an overlapping recording exists, or also retain a short ring buffer of recent frames per camera for enrichment.
- Cross-observation enrichment: should a pass be allowed to look at neighboring observations (same person/vehicle) for context, or stay frame-local in v2.
