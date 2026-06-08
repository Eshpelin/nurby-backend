# Human Action Recognition (HAR): engineering plan

Status: plan, ready to schedule. Supersedes the earlier discussion-style drafts. This is
the build document: what we implement, how it touches the existing stack, how it shows up
on the dashboard, and a phased task list with acceptance criteria.

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
- **L2 action.** PYSKL ST-GCN++ / PoseConv3D, Apache-2.0, pretrained on NTU-RGB+D (contains
  fall, eat, drink, sit-down, stand-up, walk). Sliding window of per-track keypoints ->
  closed action. NTU labels map to our vocab; unmapped -> `unknown`.
- **L3 VLM fusion (not replacement).** HAR action labels go into the VLM prompt
  (`vlm.describe(extra_context=...)`, already wired) so captions are grounded. The VLM
  caption/detail confirms or corrects HAR. Agreement -> high-confidence row (`source =
  skeleton+vlm`); disagreement -> a labelled hard case for the opt-in fine-tuning set. The
  VLM stays keyframe-rate; it stops being the per-person per-frame classifier.
- **L4 state machine.** Majority-vote smoothing over a short ring buffer, emit on transition
  with a minimum dwell. Produces clean segments and answers "standing then suddenly eating".

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
| API | `services/api/routes/` (camera routes), `services/api/ws.py` | camera-scoped actions + segments endpoints; `person_actions` WS broadcast |
| Guardian reuse | `services/guardian/wellbeing.py`, `mcp_tools.py` | re-point rollups to segments where cheaper (already shipped) |
| Settings | `shared/app_settings.py`, `services/api/routes/system.py` | per-camera HAR enable, action set, cadence, thresholds, test mode |
| Frontend live | `frontend/src/lib/ws.tsx`, `LiveCaptionOverlay.tsx` (+ new `ActionOverlay`) | handle `person_actions`; draw per-track chips on the tile |
| Frontend history | `frontend/src/app/cameras/[id]/page.tsx` (+ new `ActivityTimeline`) | per-camera action timeline |
| Frontend cards | `frontend/src/components/ObservationGroupCard.tsx` | action chip + open detail on each observation |
| Frontend global | `frontend/src/app/timeline/page.tsx` | filter by action |
| Frontend config | camera settings UI | HAR enable, preset, thresholds, test-mode review screen |

Backward-compatible: every change is additive and gated by `guardian_actions_enabled` /
new per-camera HAR settings. v1 stays the fallback; nothing existing is removed.

---

## 5. Data model and API

`observation_actions` (exists): `id, observation_id, camera_id, person_id, person_name,
action, posture, confidence, detail, track_id, source, window_start, window_end,
observed_at`. Raw, per-window. `source in {skeleton, vlm_crop, skeleton+vlm, geometry,
caption_backfill}`.

`person_action_segments` (NEW): merged contiguous runs, written by L4 on transition. Columns:
`id, camera_id, person_id, person_name, track_id, action, confidence_avg, started_at,
ended_at, source`. Indexed on `(camera_id, started_at)` and `(person_id, started_at)`. This
is the clean source for the timeline and the wellbeing rollups (cheap range queries instead
of merging raw rows).

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

**A. Live action overlay (camera tile).** Extend `LiveCaptionOverlay.tsx` (or add
`ActionOverlay.tsx`). On the live feed, draw a small chip per tracked person near their box:
recognised name (or "Person") + current action + a confidence dot. `fallen` renders red and
pinned; calm actions render muted. Driven by the new `person_actions` WS event handled in
`frontend/src/lib/ws.tsx`. This is the "what is happening right now" view and the highest-
impact surface for trust.

**B. Activity timeline (camera detail page).** New `ActivityTimeline` on
`frontend/src/app/cameras/[id]/page.tsx`: a horizontal band per recognised person showing
action segments across the selected day (sitting 9:00-9:45, walking 9:45-9:50, eating
12:05-12:35). Reads `GET /cameras/{id}/actions`. Reuses the visual language of the existing
timeline. Click a segment to jump to that moment/clip. This is the "what happened" view and
the thing eldercare families and staff actually scan.

**C. Enriched moment cards.** `ObservationGroupCard.tsx` gains an action chip + the open
`detail` line, so existing observation cards read "Mr Rahman, eating, plate of food at the
window" instead of a bare caption. Cheapest win; rides existing data.

**D. Cross-cutting.** The global `/timeline` page gets an action filter; natural-language
search is now grounded by action labels; the guardian wellbeing panel (shipped) reads the
segment table. Alerts (fall/meal) already fan out through the existing notification path and
event feed, no new surface needed.

UX rules: actions are advisory, never block the live view; `unknown` is not shown as a chip;
on cameras where HAR is degraded (section 7) the overlay shows a small "HAR limited" marker
rather than silently dropping.

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
- HAR-1.1 Integrate the chosen Tier-1 tracker in `stream.py`; assign stable `track_id`.
- HAR-1.2 Stamp `track_id` (+ smoothed action placeholder) onto the published keyframe.
- HAR-1.3 Bind `track_id -> person_id` in `pipeline.py`; TTL Redis map for the gap between
  face hits; buffer actions for unbound tracks.
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
- HAR-4.2 `person_action_segments` table + migration; write on transition.
- HAR-4.3 `GET /cameras/{id}/actions` + `person_actions` WS broadcast in `services/api/ws.py`.
- HAR-4.4 Live `ActionOverlay` on the camera tile (`ws.tsx`, `LiveCaptionOverlay.tsx`).
- HAR-4.5 `ActivityTimeline` on `cameras/[id]/page.tsx`; segment click -> moment/clip.
- HAR-4.6 Action chip + detail on `ObservationGroupCard.tsx`; action filter on `/timeline`.
- HAR-4.7 Re-point guardian `wellbeing`/`actions` to segments.
- Acceptance: operator sees live per-person actions and a per-day activity timeline; fall/
  meal still fire correctly.

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
| Action | PYSKL ST-GCN++ / PoseConv3D | Apache-2.0 | NTU-RGB+D pretrained |
| Action (alt) | MMAction2 | Apache-2.0 | heavier parent toolbox |
| RGB fallback | VideoMAE / X3D | Apache-2.0 / permissive | only if pose quality forces it |

Skip motcpp (capable but C++, poor fit for a Python async stack). Licence-clean path if a
non-AGPL posture is ever needed: original MIT ByteTrack + our OSNet + Apache pose/action,
leaving the detector as the only AGPL anchor.
