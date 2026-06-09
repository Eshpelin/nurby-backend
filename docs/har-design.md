# Human Action Recognition (HAR): engineering plan

Status: Phase 0 spike complete, **conditional GO** (see `docs/har-phase0-findings.md`).
Proceed to Phase 1; hold Phase 3 until throughput + occlusion are confirmed on real target
hardware. This is the build document: what we implement, how it touches the existing stack,
how it shows up on the dashboard, and a phased task list with acceptance criteria.

The architecture, stack-impact, dashboard, and edge-case sections were verified against the
actual code (ingestion `stream.py`/`manager.py`, `services/api/ws.py`, the frontend overlay
and feed components, the existing tables). Where the code contradicted an earlier assumption
the plan was corrected: HAR compute offload + global cap (3.1); the live surface is a text
strip, not bbox-pinned chips (6); reuse `FollowFeedPage`, not a new timeline page (6); keep a
track-anchored segment table distinct from keyframe-anchored `observation_actions` (5); and
privacy reveal/zone gating is a hard server-side rule (6).

v1 (single-frame VLM action classifier) shipped in `services/perception/actions.py` and is
gated on by default. It stays as the fallback until v-next proves out per camera.

---

## 1. Purpose and scope

Produce continuous, per-person action labels over time, robust to occlusion and to multiple
people in frame, and surface them to the operator on the camera dashboard. Actions come from
a closed vocabulary; the VLM adds open-world nuance and cross-checks the labels.

In scope for v-next: tracking + pose + skeleton action model in ingestion; fusion with the
VLM; `observation_actions` + a segment table; camera-scoped API + live WS; dashboard
surfaces (live overlay, activity timeline, enriched cards); operator config + test mode.

Out of scope for v-next: clinical gait analysis; custom per-tenant taxonomies; cross-site
federated training. Captured in section 9 as later work.

Closed vocabulary (`services/perception/actions.ACTIONS`, unchanged): `standing, walking,
sitting, lying_down, fallen, eating, drinking, sleeping, playing, interacting, unknown`,
plus a coarse `posture` and an open-world `detail` string.

---

## 2. Current state and its limits

Today: YOLO person boxes on motion keyframes (`detector.py`), ArcFace face -> `person_id`,
OSNet body re-id -> `body_cluster_id`, then for each recognised face we crop one person and
ask the VLM for `{action, posture, detail}`. Good for coarse daily signals (meal attended,
fell), but: temporally blind (one frame cannot see motion), face-dependent (misses occluded
people), fragile under crowding, and cost scales with people (one VLM call per person per
observation). Not action recognition. v-next fixes this.

---

## 3. Target architecture

Two services with a Redis boundary. The dense frame stream lives only in ingestion;
perception only ever sees sparse motion keyframes, so the temporal work must run in
ingestion.

```
INGESTION  services/ingestion/stream.py            (dense frames, per camera)
  decode every frame (already happens for motion)
  L0  track          BoxMOT / Ultralytics .track  -> stable track_id + adaptive re-id
  L1  pose           rtmlib RTMPose (17-kp)        -> skeleton per track
  L2  action         PYSKL ST-GCN++ (NTU-RGB+D)    -> closed action per track per window
  L4  state machine  smooth + debounce             -> emit on transition
        publishes:
          - existing motion keyframe + {track_id, action, conf} sidecar -> redis nurby:motion
          - live action snapshot per camera                              -> redis pub nurby:actions
          - action segments on transition                                -> DB (via API or direct)
PERCEPTION services/perception/pipeline.py          (sparse keyframes)
  YOLO detect, ArcFace, OSNet  (unchanged) -> bind track_id <-> person_id on the keyframe
  services/perception/actions.py  -> becomes the FUSION layer (was per-crop classifier)
  services/perception/vlm_queue.py -> feeds action labels into the VLM extra_context
  L3  VLM  verify (fall) + describe (open detail) + cross-check HAR, keyframe-rate
API + FRONTEND
  services/api/ws.py            -> broadcast person_actions (live)
  services/api/routes/...       -> camera-scoped actions + segments endpoints
  frontend/                     -> live overlay, activity timeline, enriched cards, config
```

Layer detail:

- **L0 tracking, two tiers.** Tier 1 within-scene: BoxMOT (DeepOCSORT/BoT-SORT) with the
  OSNet ReID we already ship, adaptive EMA appearance banks, occlusion recovery; Ultralytics
  `.track()` is the no-new-dep first cut. Gives a stable per-camera `track_id`. Tier 2
  cross-camera: the existing OSNet + ArcFace gallery, now fed tracklet-level embeddings (a
  robust mean over a track) instead of per-frame crops, which sharpens `person_id`. Single-
  camera trackers do not do cross-camera; that stays the gallery's job.
- **L1 pose.** rtmlib RTMPose, Apache-2.0, onnxruntime-only, RTMPose-m ~90 fps on an i7 CPU.
  17-keypoint output matches PYSKL's pretrained format (no remapping).
- **L2 action.** ST-GCN++ / PoseConv3D pretrained on NTU-RGB+D (contains fall, eat, drink,
  sit-down, stand-up, walk). Sliding window of per-track keypoints -> closed action; NTU
  labels map to our vocab, unmapped -> `unknown`. **Runtime is ONNX via onnxruntime, no mmcv.**
  mmcv (the OpenMMLab framework) is only needed to *export* a PYSKL/MMAction2 checkpoint to
  ONNX once on a dev machine; the deployed image ships the small `.onnx` and runs it with the
  onnxruntime we already use for pose. We never take mmcv as a runtime/install dependency.
  Note: ST-GCN is an accuracy/latency *upgrade*, not a requirement for v1 -- the geometric
  backend covers posture + fall and the VLM fusion covers eating/drinking/etc, so v1 can ship
  with no skeleton-action model and add ONNX ST-GCN later.
- **L3 VLM fusion (not replacement).** HAR action labels go into the VLM prompt
  (`vlm.describe(extra_context=...)`, already wired) so captions are grounded. The VLM
  caption/detail confirms or corrects HAR. Agreement -> high-confidence row (`source =
  skeleton+vlm`); disagreement -> a labelled hard case for the opt-in fine-tuning set. The
  VLM stays keyframe-rate; it stops being the per-person per-frame classifier.
- **L4 state machine.** Majority-vote smoothing over a short ring buffer, emit on transition
  with a minimum dwell. Produces clean segments and answers "standing then suddenly eating".

### 3.1 Where HAR compute runs (verified, load-bearing)

Confirmed by reading `services/ingestion/stream.py` + `manager.py`. Ingestion is **one
asyncio process**; the manager spawns **one `StreamWorker` task per camera**
(`asyncio.create_task(worker.run())`), and each worker owns a `ThreadPoolExecutor`. Blocking
OpenCV calls (`cap.read`, `_open_capture`) are already offloaded with
`loop.run_in_executor`. Motion detection runs **synchronously** in the loop only because it
is a cheap frame-diff every 5th frame.

Therefore HAR inference must **not** run synchronously like motion — that would block the
single event loop and starve every other camera. Rules:

- Run track/pose/action through `run_in_executor` (onnxruntime and torch release the GIL
  during native inference, so this genuinely parallelises, same pattern as `cap.read`).
- Cap concurrency with a **process-wide bounded HAR pool / semaphore** shared across camera
  workers, not just the per-worker executor, so N cameras cannot thundering-herd the CPU.
  This shared cap is what the deployment tiers (section 7) actually configure.
- HAR samples a fixed cadence off the dense stream (start 8-12 fps), decoupled from the
  motion-keyframe cadence.

This replaces the earlier hand-wave of "add L0/L1/L2 to stream.py"; the offload + global cap
is the design.

Edge cases found in the same files, now explicit:
- **Snapshot-polling cameras** (`_process_snapshot_stream`) have no real frame rate, so
  temporal windows cannot form -> fall back to v1 VLM snapshot for those, no HAR.
- **Webcam / WHIP cameras** are pulled as RTSP from MediaMTX via the same `StreamWorker`
  path, so HAR works unchanged.
- **Audio-only cameras** get no video `StreamWorker` -> no HAR, correctly.
- **PTZ cameras**: tracking degrades during an active pan/tilt move; suspend HAR while the
  PTZ tracker reports motion, resume when settled.

---

## 4. Impact on the existing stack

| Area | File(s) | Change |
|---|---|---|
| Ingestion loop | `services/ingestion/stream.py` | add L0/L1/L2/L4; sample a HAR cadence off the dense stream; publish track_id sidecar + live action pub |
| Detection reuse | `services/perception/detector.py` | none (Ultralytics already present; BoxMOT reuses it) |
| Identity binding | `services/perception/pipeline.py` | read `track_id` from keyframe; bind to `person_id`; feed tracklet embedding to OSNet gallery (`reid.py`) |
| Fusion | `services/perception/actions.py` | shift from per-crop classifier to fusion/verify; keep `confirms_fall`, parsing, vocab |
| VLM grounding | `services/perception/vlm_queue.py`, `vlm.py` | inject HAR labels into `extra_context`; record agreement |
| Models | `shared/models.py` | extend `observation_actions` (`track_id`, `source`, `window_*` already added; confirm `detail`); add `person_action_segments` |
| Migrations | `alembic/versions/` | one migration for the segment table + any column adds |
| API | camera routes, `services/api/ws.py` | camera-scoped actions/segments endpoints; `person_actions` via the existing global `broadcast()` (clients filter by camera) |
| Privacy gating | `services/guardian/reveal.py`, privacy-zone logic | action display reuses `reveal_box_for()` + suppresses actions inside active privacy zones (see 6) |
| Guardian reuse | `services/guardian/wellbeing.py`, `mcp_tools.py` | re-point rollups to segments where cheaper (already shipped) |
| Settings | `shared/app_settings.py`, `services/api/routes/system.py` | per-camera HAR enable, action set, cadence, thresholds, test mode |
| Frontend live | `frontend/src/lib/ws.tsx`, new `CurrentActivityStrip` | `useWSSubscribe("person_actions", h, cameraId)`; text strip of current actions (NOT bbox-pinned, see 6) |
| Frontend history | extend `frontend/src/components/FollowFeedPage.tsx` | add an action band / action-filtered feed (it already has the 24h heatmap + per-subject timeline) |
| Frontend cards | `frontend/src/components/ObservationGroupCard.tsx` | action chip + open detail (cheap win; no new timeline page needed, `/timeline` is just a redirect) |
| Frontend config | camera settings UI | HAR enable, preset, thresholds, test-mode review screen |

Backward-compatible: every change is additive and gated by `guardian_actions_enabled` /
new per-camera HAR settings. v1 stays the fallback; nothing existing is removed.

### 4.1 Hard requirements from the Phase 0 spike

Non-negotiables surfaced by reading the code (`docs/har-phase0-findings.md`):

- **Retention.** Observations and `observation_actions` have **no** auto-cleanup today
  (`services/ingestion/retention.py` only prunes audio/transcripts/recordings). Continuous HAR
  produces far more rows, so `person_action_segments` MUST ship age-based retention
  (`har_segment_retention_days`) modelled on the audio-capture cleanup, plus CASCADE where it
  references observations. Otherwise it grows unbounded.
- **Facility scoping.** Generic camera/observation endpoints are NOT facility-scoped (only
  Guardian routes are). The new camera-scoped actions endpoint MUST scope via
  `facility_camera_ids()` / the `_allowed_cameras` pattern, or it leaks one facility's activity
  to another.
- **Three identity states.** A track may carry a bound `person_id`, a `body_cluster_id` only,
  or neither (`person_id` exists only for an enrolled + consented face match or a face-confirmed
  body cluster). Guardian-facing action display gates on `person_id`; unknown-body actions are
  dropped or stored without identity, never shown to a family.
- **Consent at query time.** Revoking consent does not erase an already-written `person_id`.
  Action endpoints gate on `consent_given` at read time (redact name/id), same as reveal/blur.
- **Motion zones reused.** `pipeline.py:_apply_motion_zones` already gates detection, so HAR
  inherits include/exclude regions for free. No new zone type.

---

## 5. Data model and API

`observation_actions` (exists): `id, observation_id, camera_id, person_id, person_name,
action, posture, confidence, detail, track_id, source, window_start, window_end,
observed_at`. Raw, per-window. `source in {skeleton, vlm_crop, skeleton+vlm, geometry,
caption_backfill}`.

`person_action_segments` (NEW, and it IS needed): merged contiguous runs, written by L4 on
transition. Columns: `id, camera_id, person_id, person_name, track_id, action,
confidence_avg, started_at, ended_at, source`. Indexed on `(camera_id, started_at)` and
`(person_id, started_at)`.

Why a new table rather than reusing `observation_actions` (a real question the investigation
raised): `observation_actions.observation_id` is **NOT NULL and keyframe-anchored**. It is
the right home for the fused, per-observation row the VLM path produces. But continuous HAR
runs in ingestion and is **track-anchored and observation-independent** — there is no
observation for most of its windows. Forcing it into `observation_actions` means either a
nullable FK and a flood of observation-less rows, or losing continuity. Cleaner to keep
`observation_actions` as the per-observation fused store and add the track-anchored segment
table as the continuous timeline source. The two are joined by `person_id` + time.

Keypoints: not persisted by default (privacy). Opt-in `track_keypoints` table behind a
setting, only when collecting a fine-tuning set (see section 7, data flywheel).

Endpoints:
- `GET /cameras/{id}/actions?from&to` -> segments for all people on a camera (activity
  timeline).
- `GET /cameras/{id}/actions/live` -> current per-person actions (fallback for non-WS).
- `WS person_actions` (via `services/api/ws.py`): `{camera_id, people: [{track_id,
  person_id?, person_name?, action, confidence, bbox}]}` pushed on change.
- Existing guardian `GET /guardian/links/{id}/actions` and `/wellbeing` (shipped) re-point
  to `person_action_segments`.

---

## 6. Dashboard and UX (how HAR reaches the operator)

Three surfaces, smallest to largest commitment:

Reality check from reading the frontend: `LiveCaptionOverlay.tsx` is a translucent caption
*strip*, not a per-person box overlay, and the live tile has no detection-coordinate layer.
The WS is one global `/ws` + `broadcast()`; clients filter with
`useWSSubscribe(type, handler, cameraId)`. The surfaces below are scoped to what actually
exists, with bbox-pinned chips called out as a separate capability, not assumed.

**A. Current-activity strip (camera tile), v1.** A new `CurrentActivityStrip` modelled on
`LiveCaptionOverlay` (same translucent strip, same `useWSSubscribe("person_actions", h,
cameraId)` pattern): a compact line listing current actions per recognised person ("Mr
Rahman, eating, Inara, walking"). `fallen` pinned red. This reuses a proven pattern and ships
the "what's happening now" value without new infrastructure.

*Stretch (separate task, not assumed):* chips pinned to each person's body on the live video
require a live detection-overlay capability that does not exist today — streaming bbox coords
in real time and mapping them onto the displayed video element. Scope it only after the strip
ships and only if there is demand.

**B. Activity timeline.** Extend `FollowFeedPage.tsx`, which already is the per-subject
investigative timeline (24h activity heatmap + unified feed). Add an **action band** (segments
coloured by action across the day) and an **action filter** on the feed, fed by
`person_action_segments` via `GET /cameras/{id}/actions`. This reuses the existing timeline
rather than building a new one (`/timeline` is just a redirect today, so there is nothing to
extend there).

**C. Enriched moment cards.** `ObservationGroupCard.tsx` gains an action chip + the open
`detail` line, so cards read "Mr Rahman, eating, plate of food at the window". Cheapest win;
rides existing data; no new page.

**D. Cross-cutting.** Natural-language search is grounded by action labels; the guardian
wellbeing panel (shipped) reads the segment table; alerts (fall/meal) already fan out through
the existing notification path, no new surface.

**Privacy gating (hard rule, not optional).** An action label leaks what a person is doing
even when their face is blurred, and the most sensitive actions (sleeping, toileting, fallen)
happen exactly where smart privacy zones blur (bed, toilet). So every action surface must:
- gate per-person action display behind the same reveal test as the face,
  `services/guardian/reveal.py::reveal_box_for(person_detections, dependant_id, max_distance)`
  — if the person is not revealed, do not show their action;
- suppress actions whose track sits inside an active privacy zone for guardian-facing views;
- honour the guardian delay cutoff (already handled in `wellbeing.recent_actions`).
This is enforced server-side in the action endpoints, not just hidden in the UI.

UX rules: actions are advisory, never block the live view; `unknown` is never shown; on a
camera where HAR is degraded (section 7) the strip shows a small "HAR limited" marker rather
than silently dropping.

---

## 7. Solution, deployment, and operations (constraints, not opinions)

These are requirements the build must satisfy, derived from a solutions review.

**Deployment profiles / min-spec.** Ship a capability matrix and pick the profile at setup
from measured hardware:

| Tier | Hardware | Cameras | L0/L1/L2 | HAR cadence |
|---|---|---|---|---|
| S | CPU-only (NUC class) | 1-2 | ByteTrack + RTMPose-s + ST-GCN++ | reduced fps |
| M | strong CPU / iGPU | 3-6 | BoxMOT BoT-SORT + RTMPose-m | full cadence |
| L | discrete GPU | 6+ | DeepOCSORT + RTMPose-m + optional RGB fallback | full + RGB on poor cams |

**Graceful degradation (mandatory).** When the per-camera budget is exceeded, degrade in
order: lower HAR fps -> drop pose on lowest-priority tracks -> fall back to v1 VLM snapshot
for that camera. Surface a per-camera `har_status` capability flag (`full | limited | off`)
to the dashboard and API. Never silently stop classifying.

**Operator onboarding and trust.** Per-camera: enable HAR, choose a use-case preset
(eldercare / childcare / security) that selects the relevant action subset and default
thresholds, optionally restrict HAR to a drawn zone (reuse existing motion/privacy zones).
Ship a **test / dry-run mode**: actions are computed and shown on the dashboard but raise no
alerts for a configurable window, with a review screen to confirm accuracy before going
live. Eldercare staff will not trust fall alerts they could not validate on their own
cameras first; this is non-negotiable for adoption.

**Data flywheel vs privacy (resolve the contradiction).** The self-improving loop in L3
needs disagreement cases, but keypoints are not persisted by default. Reconcile explicitly:
the flywheel is **opt-in only**. An operator may enable a local `track_keypoints` capture for
HAR/VLM disagreement cases, reviewable and exportable by them, never auto-uploaded. Any
cross-site improvement is a future, separately-consented program (section 9). Until then, the
honest claim is "improves when you opt in and fine-tune," not "improves automatically."

**Compliance, liability, positioning.** Fall detection is sensitive. Productize the stance,
not just a footnote: surface "best-effort, not a certified medical alarm" on the fall config
and on each fall alert; note that pose/gait may be biometric in some jurisdictions and that
keypoints are not stored by default; reuse existing consent gating. Position cameras as a
**complement to, not a replacement for**, certified fall sensors or wearables. Record the
honest build-vs-buy point: for the highest-liability fall use case, radar/wearables are more
certifiable; Nurby's edge is whole-scene understanding and per-family Guardian, not a
medical guarantee.

**Buyer acceptance criteria (pilot exit).** Define before any rollout: on the customer's own
cameras, in test mode, over a 2-week pilot, fall recall >= agreed threshold and false alerts
<= agreed per-camera-per-night, with operator sign-off. This is what "working" means
contractually, distinct from the dev eval below.

**Day-2 operations.** Per-camera HAR health (pose quality, fps, `har_status`) surfaced in the
UI and API. Model weights versioned and shipped with release images; documented update path.
Accuracy observability: track the skeleton-vs-VLM agreement rate per camera as a live quality
signal and warn when it drops. OSS maintenance risk (OpenMMLab/PYSKL cadence; Ultralytics
commercial-license trigger if a hosted tier is ever sold) tracked as a dependency risk.

---

## 8. Phased delivery and task list

Each phase is independently shippable and reversible by setting. Tasks are sized to land as
their own PRs.

### Phase 0 — Spike (gates 2+; 1-2 days, throwaway code allowed)
- HAR-0.1 Stand up rtmlib RTMPose + a tracker + a PYSKL pretrained ST-GCN++ on a handful of
  demo-camera clips.
- HAR-0.2 Measure pose+track+action throughput at the target cadence for 1/2/3 people on a
  representative CPU box; record the tier ceiling.
- HAR-0.3 Prototype `track_id <-> person_id` binding end to end on one camera.
- HAR-0.4 Read pose quality on genuinely low-end / wide-angle footage; decide skeleton-only
  vs RGB-hybrid; decide ByteTrack vs BoxMOT by ID-switch rate.
- Acceptance: a go/no-go memo with numbers and the two decisions above.

### Phase 1 — Tracking + re-id (safe now, in ingestion)
- HAR-1.0 Examine `services/perception/tracker.py` (`ObjectTracker`, already used for
  loitering/line-cross) and align the HAR tracker with it rather than inventing a second
  notion of a track.
- HAR-1.1 Integrate the chosen Tier-1 tracker in `stream.py`; assign stable `track_id`.
- HAR-1.2 **Version the `nurby:motion` payload** (currently unversioned: `camera_id,
  timestamp, motion_score, frame`) and add the ingestion tracker's `track_ids`; keep
  consumers backward-compatible.
- HAR-1.3 Bind `track_id -> person_id` in `pipeline.py` (the Phase-0 prototype logic: tightest
  containing track box, hold through occlusion); write a shared Redis map
  `(camera_id, track_id) -> person_id` (TTL) that ingestion reads to attribute segments;
  handle the three identity states (person_id / body_cluster_id only / neither).
- HAR-1.4 Feed tracklet-level embeddings into the OSNet gallery (`reid.py`); write `track_id`
  onto `observation_actions`.
- HAR-1.5 Tests: ID continuity through a scripted occlusion clip; binding correctness.
- Acceptance: stable per-person tracks through occlusion on demo cameras; `person_id` ID-
  switch rate down vs today.

### Phase 2 — Pose (in ingestion)
- HAR-2.1 rtmlib RTMPose per tracked person; cap tracked persons; gate to bound dependants.
- HAR-2.2 Optional `track_keypoints` store behind a setting (default off).
- HAR-2.3 Per-camera pose-quality metric -> feeds `har_status`.
- Acceptance: 17-kp skeletons at the tier cadence within the measured CPU budget; quality
  metric visible.

### Phase 3 — Action model + fusion
- HAR-3.1 PYSKL ST-GCN++ on per-track pose windows -> closed action; NTU->vocab map.
- HAR-3.2 Write `observation_actions` with `source=skeleton`.
- HAR-3.3 Fusion in `actions.py`/`vlm_queue.py`: inject HAR labels into VLM context; record
  agreement (`source=skeleton+vlm`); keep fall VLM-confirm.
- HAR-3.4 Run alongside v1 behind a setting; A/B on the labelled clip set; flip default when
  it wins.
- Acceptance: per-frame action accuracy and fall recall beat v1 on the eval set; VLM load
  drops to keyframe rate.

### Phase 4 — State machine, segments, and dashboard
- HAR-4.1 L4 smoothing + transition emission in ingestion.
- HAR-4.2 `person_action_segments` table + migration; write on transition; **age-based
  retention job** (`har_segment_retention_days`) + CASCADE, modelled on audio-capture cleanup.
- HAR-4.3 `GET /cameras/{id}/actions` + `person_actions` via the existing global
  `broadcast()`; enforce **facility scoping** (`facility_camera_ids`), **consent at query
  time**, and the privacy reveal + zone gating, all server-side here.
- HAR-4.4 `CurrentActivityStrip` (text strip, `useWSSubscribe` pattern, modelled on
  `LiveCaptionOverlay`). Bbox-pinned chips are a separate, later task, not in this phase.
- HAR-4.5 Extend `FollowFeedPage.tsx` with an action band + action filter (do not build a new
  timeline page; `/timeline` is a redirect).
- HAR-4.6 Action chip + detail on `ObservationGroupCard.tsx`.
- HAR-4.7 Re-point guardian `wellbeing`/`actions` to segments.
- HAR-4.8 Privacy enforcement tests: an unrevealed/zoned person never exposes an action via
  any endpoint.
- Acceptance: operator sees live current activity and a per-day action timeline; no action is
  shown for a non-revealed or privacy-zoned person; fall/meal still fire correctly.

### Phase 5 — Productization, ops, trust
- HAR-5.1 Deployment-profile detection + min-spec picker at setup.
- HAR-5.2 Graceful degradation + `har_status` per camera (UI + API).
- HAR-5.3 Operator config: per-camera enable, use-case preset, thresholds, HAR zone.
- HAR-5.4 Test/dry-run mode + review screen before alerts go live.
- HAR-5.5 Compliance surfacing: best-effort disclaimers, biometric/consent notes.
- HAR-5.6 Day-2: agreement-rate observability + weight-version/update path + docs.
- Acceptance: a non-technical operator can enable HAR on a camera, validate it in test mode,
  and go live; pilot acceptance criteria measurable.

---

## 9. Risks, open decisions, and later work

Risks: CPU throughput at scale (mitigated by tiers + caps, measured in Phase 0); pose quality
on cheap cameras (decision gate in Phase 0, RGB fallback); cross-service binding (Phase 0
prototype); cadence vs the existing motion budget (reconcile the two ingestion loops); NTU
domain gap (opt-in fine-tuning); OSS maintenance + Ultralytics licensing.

Honest limits: cross-camera identity in a ward of similarly-dressed people is never perfect;
target high-precision / bounded-recall, face as the strongest anchor. Fall detection stays
best-effort, never a medical guarantee.

Open decisions (carried from Phase 0): ByteTrack vs BoxMOT default; RTMPose-m vs -s per tier;
window length and HAR fps; skeleton-only vs RGB-hybrid; persist keypoints default (no).

Later work (out of v-next): per-tenant custom action taxonomies; cross-site federated /
consented training program; additional verticals (retail slip-fall, PPE, queue) reusing the
same engine and segment model.

---

## 10. Reusable OSS and licensing

| Layer | Reuse | Licence | Note |
|---|---|---|---|
| Detection | Ultralytics YOLO (in `detector.py`) | AGPL-3.0 | already a dependency |
| Track Tier 1 | BoxMOT (DeepOCSORT/StrongSORT/BoT-SORT, OSNet/CLIP-ReID) | AGPL-3.0 | recommended; adaptive EMA re-id; reuses our OSNet; AGPL already carried |
| Track fallback | Ultralytics `.track()` ByteTrack/BoT-SORT | AGPL-3.0 | no new dep; first cut |
| Cross-camera | our OSNet + ArcFace gallery, fed tracklet embeddings | ours | single-camera trackers do not do this |
| Pose | rtmlib / RTMPose | Apache-2.0 | onnxruntime-only; ~90 fps i7 CPU; 17-kp matches PYSKL |
| Action | ST-GCN++ / PoseConv3D, **exported to ONNX** (run via onnxruntime) | Apache-2.0 | NTU-RGB+D pretrained; mmcv only for the one-time offline export, never at runtime; optional (geometric + VLM cover v1) |
| Action (alt) | MMAction2 | Apache-2.0 | heavier parent toolbox |
| RGB fallback | VideoMAE / X3D | Apache-2.0 / permissive | only if pose quality forces it |

Skip motcpp (capable but C++, poor fit for a Python async stack). Licence-clean path if a
non-AGPL posture is ever needed: original MIT ByteTrack + our OSNet + Apache pose/action,
leaving the detector as the only AGPL anchor.
