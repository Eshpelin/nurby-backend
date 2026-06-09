# HAR Phase 0 spike: findings and go/no-go

Throwaway spike to de-risk the HAR engineering plan (`docs/har-design.md`) before any
implementation. Ran real models on real footage, prototyped the binding, and audited the
code for edge cases. This memo records what was measured, what changed in the plan, and the
verdict.

## Method and its limits (read first)

- Hardware: Apple **M4 Pro, CPU execution** (verified: ultralytics selected `cpu` even with
  MPS available). M4-Pro CPU is **faster than a typical low-end x86 self-host NUC**, so
  absolute throughput here is optimistic. Derate ~1.5-2.5x for a NUC. Relative costs and
  pose quality transfer; absolute fps must be re-confirmed on target hardware.
- Footage: `dev/sample-cctv.mp4` (768x432 @12fps) + 90 real recordings. **These scenes are
  sparse (0-2 people)**, so multi-person throughput scaling and crowded-occlusion ID
  stability were NOT stress-tested. This is the main gap the spike could not close here.
- Pose model: `yolo11n-pose` (one-stage, whole-frame). The plan's primary is RTMPose
  (top-down, per-person). One-stage cost is per-frame; top-down scales per person. rtmlib was
  not pre-installed; the one-stage number is a valid proxy for the per-frame budget.
- Action model: PYSKL ST-GCN++ was **not** run (mmcv install is fragile and was skipped).
  ST-GCN inference is sub-10ms/window per published benchmarks, negligible vs detect/pose, so
  it is treated as a cited assumption, not a measurement.

## Measured numbers (M4-Pro CPU, 768x432)

| Stage | Model | Per-frame ms | Notes |
|---|---|---|---|
| Detect + track | yolov8n + ByteTrack | ~37 | ~27 fps/cam; person count near 0-1 in clip |
| Pose (one-stage) | yolo11n-pose | ~40 | also yields boxes; ~24-27 fps/cam |
| Action | ST-GCN++ | ~5 (cited) | not measured here |

Budget read: a **pose-as-detector** path (one model gives boxes + keypoints) is ~40ms/frame
on this CPU; separate detect+pose is ~77ms. Adding ST-GCN is marginal.

Derated to a low-end x86 CPU-only NUC (~1.5-2.5x slower):
- pose-as-detector ~60-100 ms/frame -> ~10-16 fps single camera.
- **Tier S (CPU-only NUC): 1-2 cameras at a reduced HAR cadence (6-8 fps) is feasible.**
  3+ cameras CPU-only is tight -> lower cadence or a GPU. Validates the S/M/L tiers in the
  plan. Confirm on the real box before quoting numbers to customers.

## Pose quality (the decision gate)

On real 768x432 CCTV, keypoint confidence was **mean 0.76, 82% of keypoints > 0.5**. That is
good for this camera class. **Decision: skeleton-only is viable; RGB-hybrid is not mandatory
for v-next.** Caveat: measured on <=2-person, low-occlusion scenes; crowded / heavily occluded
quality is unverified and should be checked on real eldercare footage.

## Binding prototype (HAR-0.3) — de-risked

A pure-Python `track_id <-> person_id` binding (face-centre in tightest containing track box,
held for the track's life, TTL-evicted) passed all 5 edge cases: bind on face hit, **hold
through face occlusion**, do not bind an unknown (no person_id), tightest-box-wins under
overlap, evict after TTL. The integration logic is simple and correct; the risk here is not
the logic, it is the cross-service plumbing (below).

## Decisions taken

- **Tracker: start with ByteTrack** (via the Ultralytics dep we already ship, zero new
  dependency). Evaluate BoxMOT DeepOCSORT only if ID-switches prove bad in crowded rooms,
  which this footage could not test. Residual, deferred to real multi-person footage.
- **Skeleton-only** action recognition for v-next (pose quality supports it). RGB model stays
  an optional later backend.
- **Pose-as-detector** is worth prototyping (one model for boxes + keypoints) to halve the
  per-frame budget, but reconcile with the existing object detector that also finds non-person
  classes.

## Plan-changing edge cases found (folded into har-design.md)

1. **person_id is frequently absent.** It exists only for an enrolled + consented face match,
   or a face-confirmed body cluster (`faces.py`, `reid.py`). HAR must handle three states per
   track: bound `person_id`; `body_cluster_id` only; neither. Guardian-facing action display
   gates on `person_id`; unknown-body actions are either dropped or stored without identity.
2. **No retention on observations/observation_actions** (`services/ingestion/retention.py`
   cleans audio/transcripts/recordings only). Continuous HAR produces far more rows than v1,
   so the new `person_action_segments` table **must** ship its own age-based retention
   (`har_segment_retention_days`) modelled on the audio-capture cleanup, plus CASCADE where it
   references observations. Without this it grows unbounded.
3. **Facility scoping is not enforced on generic camera/observation endpoints** (only Guardian
   routes use `facility_camera_ids()`). The new camera-scoped actions endpoint **must** add
   facility scoping or it leaks one facility's activity to another. Copy the
   `_allowed_cameras` / `facility_camera_ids` pattern.
4. **Consent revocation does not retroact.** `person_id` is written into rows at perception
   time; revoking consent later does not erase it. Action endpoints must gate on
   `consent_given` at query time (redact name / id), same as the existing reveal/blur model.
5. **The keyframe -> person_id boundary needs a deliberate design.** Tracking runs in
   ingestion (dense frames); faces/person_id resolve in perception (keyframes). The redis
   `nurby:motion` payload is currently unversioned (`camera_id, timestamp, motion_score,
   frame`). Plan: **version the payload** and add the ingestion tracker's current `track_ids`;
   perception binds and writes a shared Redis map `(camera_id, track_id) -> person_id` (TTL,
   the prototype above); ingestion reads that map to attribute its continuous action segments.
   Examine the existing `services/perception/tracker.py` (`ObjectTracker`, used for
   loitering/line-cross) to avoid two divergent tracker notions.
6. **Demo camera loops**, so track_ids reset at the loop boundary. Good for functional tests,
   unusable for identity-continuity tests. Continuity tests need real, non-looping footage.
7. **Motion zones** (include/exclude, `pipeline.py:_apply_motion_zones`) already gate
   detection, so HAR inherits "only run here / skip there" for free. Reuse, no new zone type.

## Residual risks (cannot close from this desk)

- Absolute throughput on the **real target NUC** (M4-Pro CPU is a soft proxy).
- ID-switch rate and pose quality in **crowded, occluded** eldercare rooms (sparse demo
  footage could not test) -> this is the ByteTrack-vs-BoxMOT decision and the skeleton-vs-RGB
  fallback trigger.
- ST-GCN++ real latency + the NTU->our-vocab mapping accuracy on domain footage.
- RTMPose vs yolo-pose: top-down per-person scaling under many people.

## Verdict: conditional GO

The architecture is sound, the binding is proven, pose quality supports skeleton-only, and
the CPU budget is feasible for the S/M tiers. **Proceed to Phase 1 (tracking)** after folding
the seven edge cases above into the plan (done). Hold Phase 3 (the temporal model) until the
residual throughput/occlusion items are confirmed on real target hardware and real
multi-person footage. Do not quote customer-facing fps numbers until measured on a NUC.
