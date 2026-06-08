# Human Action Recognition (HAR)

Status: design. v1 (single-frame VLM action classifier) shipped in
`services/perception/actions.py` and gated on by default. This doc specifies the
path from that snapshot classifier to a real, temporal, per-track HAR engine.

## TL;DR

What shipped is single-frame classification: crop one recognised person, ask the
VLM what they are doing, store a closed-vocabulary action plus an open-world
detail. It is useful for coarse, low-frequency signals (a meal happened today, a
person is on the floor) but it is **temporally blind, face-dependent, and scales
its cost with the number of people in frame**. Real action recognition is motion
over time, per tracked person. This doc proposes a layered HAR pipeline that
adds tracking and pose, moves continuous classification to a cheap temporal
model, and **demotes the VLM from classifier to verifier and describer**. The
existing `observation_actions` schema, closed vocabulary, and fall geometry
survive unchanged; the temporal model simply becomes the new producer of those
rows.

## The current implementation, assessed honestly

Pipeline today (`actions.extract_for_observation`, wired in `vlm_queue`):

1. YOLO produces person boxes per keyframe. ArcFace gives recognised faces with
   `person_id`; OSNet body re-id gives `body_cluster_id`. These are appearance
   models ("who"), associated by embedding similarity, **not** a frame-to-frame
   motion tracker.
2. For each recognised face, find the body box whose region contains the face
   centre (`body_box_for_face`), crop the frame to that one person.
3. Send the single-person crop to the VLM with a constrained prompt; parse
   `{action, posture, confidence, detail}` against a closed vocabulary.
4. Persist one `observation_actions` row per dependant. Fall uses bbox geometry
   plus a hold timer plus a VLM crop confirm; meal reads the `eating` action.

What is genuinely good and worth keeping:

- The **closed vocabulary + open `detail`** split. Closed action is the
  queryable anchor; detail is the open description. Right shape.
- The `observation_actions` table and the dependant-in-frame gate.
- Fall = geometry + duration hold + VLM confirm. A defensible pragmatic fall
  detector even in the target architecture.

What is wrong for HAR:

- **Temporally blind.** A single frame cannot encode motion. "Eating" is
  repeated hand-to-mouth over seconds; one still of a hand near a mouth is
  indistinguishable from scratching. "Walking" vs "standing" is ambiguous in a
  still. Only fall has any temporal state (the hold timer). General-action
  transitions ("standing then suddenly eating") are invisible by construction.
- **Face-dependent attribution.** Only people whose face is recognised get
  classified. Back-turned, distant, or blurry faces are skipped. Worse, the
  actions we most care about (eating, lying down) frequently occlude the face,
  hiding the very person we want to classify.
- **Fragile attribution under crowding.** Face-centre-in-box breaks when bodies
  overlap, because one face centre can fall inside two boxes.
- **Cost scales with people.** Per observation we fire the full-frame caption,
  one VLM call per dependant, and fall-confirm calls. A three-resident room is
  three extra VLM calls per observation. On a local model (1-15 s each) this is
  the throughput bottleneck, and it grows in exactly the wrong direction for a
  ward.

Conclusion: v1 is acceptable for daily-granularity wellbeing (meal attended,
fell) but must not be marketed or relied on as action recognition. The rest of
this doc is the real thing.

## Goals and non-goals

Goals.

- Continuous, per-person action labels over time, robust to short face
  occlusion and to multiple people in frame.
- Detect transitions, not just per-frame states, so the timeline reads
  "sitting 12:00, eating 12:05-12:30, standing 12:31".
- Cost that scales with *cameras*, not with *people x observations*.
- Same closed vocabulary and `observation_actions` rows as today, so the API,
  MCP tools, meal, and fall layers inherit the upgrade for free.
- Run on a self-hosted box. CPU-only must work; GPU should accelerate.

Non-goals.

- Centimetre pose accuracy or clinical gait analysis.
- Recognising fine-grained activities beyond the closed set in v-next (the open
  `detail` field already carries nuance via the VLM).
- A certified medical fall alarm. Best-effort remains the framing.

## Proposed architecture: layered HAR, VLM demoted

```
            ┌──────────────────────────────────────────────────────────┐
  frames →  │ L0 Detect (YOLO)  →  L0 Track (ByteTrack)  → track_id     │
            │ L1 Pose per track (YOLOv8-pose / RTMPose) → keypoints[]    │
            │ L2 Temporal action model on pose window (PoseConv3D/ST-GCN)│
            │    → closed action per track per window                    │
            │ L3 VLM verify+describe (rare): fall confirm, open detail   │
            │ L4 State machine per track → emit on transition, debounce  │
            └──────────────────────────────────────────────────────────┘
                         ↓ writes
            observation_actions (+ track_id, + source) and guardian events
```

### L0. Motion tracking (the missing backbone)

Add a tracker (ByteTrack or OC-SORT) over YOLO person boxes so each person gets a
stable `track_id` across frames within a camera. We have appearance re-id, but
not frame-continuous tracking; tracking is what lets an action attach to a person
*over time* and survive a few frames of face occlusion.

- ByteTrack is association-only (IoU + Kalman), no extra network, near-free on
  CPU. OC-SORT is similar with better handling of nonlinear motion and
  occlusion. Default: **ByteTrack** for simplicity; OC-SORT as a config swap.
- `track_id` is per-camera and per-session. Cross-camera identity stays the job
  of OSNet re-id and ArcFace, unchanged. Tracking and re-id compose: the tracker
  gives short-term continuity, re-id stitches tracks to a `person_id`.
- Identity binding: a track inherits `person_id` when any face hit lands inside
  its box, and keeps it for the track's life even when the face later occludes.
  This is the core fix for face-dependent attribution.

### L1. Pose estimation (cheap substrate)

Per active track, run a pose model to get 2D keypoints (17 COCO joints). Options:

- **YOLOv8-pose** (Ultralytics). Already in the YOLO family we ship, one more
  weight file, top-down per box, CPU-tolerant. Default.
- **RTMPose** (MMPose). Faster/more accurate at similar size, but a second
  framework dependency.

Keypoints are tiny (17 x (x,y,score)) and become the HAR model input. Pose is the
single biggest accuracy lever and the thing v1 entirely lacks.

### L2. Temporal action model (the actual HAR engine)

Feed a sliding window (for example 32-48 frames at the keyframe rate, a few
seconds) of per-track keypoints into a skeleton action model:

- **PoseConv3D (MMAction2)** or **ST-GCN / 2s-AGCN**. Pretrained weights exist on
  **NTU-RGB+D 60/120**, whose label set already includes *falling down, eating,
  drinking, sitting down, standing up, walking, playing*. We map NTU labels to
  our closed vocabulary; unmapped labels collapse to `unknown`.
- Inference is milliseconds per window on CPU, far cheaper than one VLM call, and
  it is *temporal by construction*, so transitions and motion-defined actions
  (eating, walking) become tractable.
- Output: per-track action + confidence per window. Written to
  `observation_actions` with `source = "skeleton"`.

Alternative / heavier: clip-based video models (X3D, VideoMAE, SlowFast) on the
RGB track crop. More accurate on appearance-defined actions, GPU-hungry, larger.
Keep as an optional high-accuracy backend behind the same interface; default is
skeleton for cost.

### L3. VLM as verifier and describer (rare, not per-frame)

Invert the current design. The temporal model classifies continuously; the VLM is
called only when it adds value:

- **Fall confirm.** On a high-confidence skeleton `fallen` (or geometry hold),
  one VLM crop confirms vs sleeping/lying. Same `confirms_fall` policy as today.
- **Open-world detail.** Periodically, or on a notable transition, one VLM crop
  fills the open `detail` (objects held, clothing, who they are with). Not every
  frame, not every person.

VLM load drops from O(people x observations) to a handful of events per camera.

### L4. State machine and event emission

Maintain per-track action state. Smooth the per-window labels (majority vote over
a short ring buffer), then emit on **transition**, debounced, with a minimum
dwell time per action to kill flicker. This is what produces a clean timeline and
what finally answers "standing then suddenly eating": it is a transition in the
per-track action stream, detected by comparing consecutive smoothed windows.

Fall and meal become consumers of this stream:

- Fall: transition into `fallen` held for N seconds, VLM-confirmed.
- Meal: a sustained `eating` run during a meal window (already deduped per
  person/day/meal).

## Data model changes

`observation_actions` gains, by migration:

- `track_id` (string, indexed): the per-camera track the action came from.
- `source` (string): `skeleton` | `vlm_crop` | `geometry` | `caption_backfill`,
  so we can tell continuous HAR rows from VLM/legacy rows and tune trust.
- optional `window_start` / `window_end` (timestamptz): the time span the
  temporal label covers, since a HAR label is over a window, not an instant.

Keypoints: store sparingly. Option A, a `track_keypoints` table keyed by
(camera, track_id, frame_ts) for debugging and future retraining. Option B, do
not persist raw keypoints at all (privacy-lean, recompute on demand). Default B
for privacy; flip to A behind a setting when collecting a training set.

Nothing in the existing schema is dropped. Closed vocab, `action`, `posture`,
`confidence`, `detail`, the indexes, and the wellbeing API/MCP all stay.

## Resource budget (rough, self-hosted)

Per camera at the keyframe rate (not every video frame):

- YOLO detect: already paid.
- ByteTrack: negligible, no network.
- Pose: one top-down pose per active person box. ~5-20 ms/person on CPU, less on
  GPU. This is the new recurring cost; bounded by capping tracked persons per
  frame.
- Skeleton HAR: one window inference per active track per stride. Single-digit ms.
- VLM: only on fall confirm and occasional detail. Down from per-person-per-obs.

Net: continuous classification moves off the VLM onto cheap CPU models, so total
inference cost drops sharply on multi-person scenes while gaining temporal
accuracy. Pose is the cost to watch; gate it (only tracks bound to a dependant,
or only when motion exceeds a threshold) exactly as the VLM is gated today.

## Privacy

- Pose and skeletons are computed on-device, same as everything else. No frames
  leave the box.
- Default to **not persisting keypoints** (recompute on demand), so we do not
  accumulate a biometric gait store unless an operator opts in for training.
- HAR runs only for recognised dependants by default (same gate as v1), so we do
  not build action profiles on strangers or non-consented people.
- Per-person blur and consent gating are upstream of this layer and unchanged.

## Phased migration (additive, non-destructive)

- **Phase 1. Tracking.** ByteTrack over YOLO; write `track_id` onto detections
  and `observation_actions`; bind `person_id` to tracks. No model swap. Fixes
  attribution; foundation for everything. Ships value alone (stable identity
  through occlusion).
- **Phase 2. Pose.** YOLOv8-pose per track; optional keypoint store behind a
  setting. No behaviour change yet; sets up the substrate, lets us eval pose
  quality on real cameras.
- **Phase 3. Temporal model.** PoseConv3D/ST-GCN on pose windows producing the
  closed vocabulary with `source="skeleton"`. Run it **alongside** the v1 VLM
  classifier behind a setting; compare before switching the default. Demote the
  VLM to confirm+detail once skeleton quality is proven.
- **Phase 4. State machine.** Per-track smoothing and transition emission; move
  fall and meal onto the action stream; build the per-person action timeline UI.

Each phase is shippable and reversible via a setting. v1 stays the fallback until
Phase 3 proves out on real footage.

## Evaluation

- A labelled clip set from the demo/test cameras and any consented real footage:
  short clips tagged with ground-truth action and transition times.
- Metrics: per-frame action accuracy, transition timing error, fall
  precision/recall (the high-stakes one), false-alert rate per camera-hour.
- A/B v1 (VLM snapshot) vs Phase 3 (skeleton temporal) on the same clips before
  flipping the default. Reuse the nightly-CI eval harness pattern from the agent
  work.
- Explicit honesty gate: do not relax the "best-effort, not a medical alarm"
  framing until fall recall and false-alert rate clear an agreed bar on real
  footage.

## Risks and open questions

- **Pose quality on cheap/old cameras.** Low-res, wide-angle, ceiling-mounted, or
  poorly lit feeds degrade pose, which caps skeleton HAR. Mitigation: keep the
  clip-based RGB backend as an option; keep geometry+VLM fall as a floor.
- **Keyframe rate vs temporal need.** HAR wants a steady frame cadence; our
  pipeline is keyframe/motion-gated. We may need a denser sampling burst for
  tracked dependants, or to run tracking/pose on a faster sub-stream than the VLM
  path.
- **CPU-only throughput** with several cameras and several people. Cap tracked
  persons, gate pose to dependant-bound tracks, stagger per camera.
- **NTU label coverage.** NTU is rich but its "eating/drinking" classes are
  scripted; real eldercare eating may differ. May need light fine-tuning on
  domain clips, which motivates the optional keypoint store.

Open decisions for the build: ByteTrack vs OC-SORT default; YOLOv8-pose vs
RTMPose; persist keypoints or not by default; dense sub-stream sampling vs reuse
keyframes; skeleton-only vs skeleton+RGB hybrid for the first temporal model.
