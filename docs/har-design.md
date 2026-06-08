# Human Action Recognition (HAR)

Status: design v2 (revised after a senior-engineering review and an AI/ML-researcher
review). v1 (single-frame VLM action classifier) shipped in
`services/perception/actions.py` and is gated on by default. This doc specifies the
path from that snapshot classifier to a real, temporal, per-track HAR engine, names
the open-source components to reuse, and gates the risky phases behind a measurement
spike.

## TL;DR

What shipped is single-frame classification: crop one recognised person, ask the VLM
what they are doing, store a closed-vocabulary action plus an open-world detail. It is
useful for coarse, low-frequency signals (a meal happened today, a person is on the
floor) but it is temporally blind, face-dependent, and scales its cost with the number
of people in frame.

Real action recognition is motion over time, per tracked person. The target is a
layered pipeline: track every person, estimate pose, classify a short window of pose
with a skeleton action model, and **fuse** that with the VLM rather than replacing it.
HAR becomes the cheap continuous classifier; the VLM keeps running at keyframe cadence,
now fed the HAR labels so its captions improve, and HAR/VLM agreement becomes a
self-improving training signal (see L3). We do not build the models. We reuse a mature, mostly Apache-2.0 stack
(Ultralytics tracking, rtmlib/RTMPose, PYSKL ST-GCN++/PoseConv3D on NTU-RGB+D) whose
keypoint formats line up, so most of the work is glue and integration, not research.

Two things from review reshaped the v1 sketch and must be respected:

1. **Placement.** HAR cannot run in the perception service. Perception only ever sees
   sparse motion keyframes off Redis. The dense frame stream exists only in the
   ingestion worker. L0 (track) and L1 (pose) must live in ingestion; perception keeps
   detection, face ID, and the VLM. A `track_id` carried across the Redis boundary
   stitches them.
2. **Measure before committing.** The CPU throughput, the cross-service identity
   binding, and pose quality on cheap cameras are unproven. Phase 1 (tracking) is safe
   to build now; Phases 2-4 are gated behind a short Phase 0 spike that turns three
   assumptions into measured numbers.

The `observation_actions` schema, closed vocabulary, and fall geometry survive
unchanged; the temporal model becomes the new producer of those rows.

## The current implementation, assessed honestly

Pipeline today (`actions.extract_for_observation`, wired in `vlm_queue`):

1. YOLO produces person boxes per keyframe (`services/perception/detector.py`, via
   Ultralytics). ArcFace gives recognised faces with `person_id`; OSNet body re-id gives
   `body_cluster_id`. These are appearance models ("who"), associated by embedding
   similarity, not a frame-to-frame motion tracker.
2. For each recognised face, find the body box whose region contains the face centre
   (`body_box_for_face`), crop the frame to that one person.
3. Send the single-person crop to the VLM with a constrained prompt; parse
   `{action, posture, confidence, detail}` against a closed vocabulary.
4. Persist one `observation_actions` row per dependant. Fall uses bbox geometry plus a
   hold timer plus a VLM crop confirm; meal reads the `eating` action.

Genuinely good, keep:

- The closed vocabulary + open `detail` split. Closed action is the queryable anchor;
  detail is the open description.
- The `observation_actions` table and the dependant-in-frame gate.
- Fall = geometry + duration hold + VLM confirm. A defensible pragmatic fall detector
  even in the target architecture.

Wrong for HAR:

- **Temporally blind.** A single frame cannot encode motion. "Eating" is repeated
  hand-to-mouth over seconds; one still is indistinguishable from scratching. Only fall
  has temporal state (the hold timer). Transitions are invisible by construction.
- **Face-dependent attribution.** Only people whose face is recognised get classified.
  The actions we most care about (eating, lying down) frequently occlude the face.
- **Fragile under crowding.** Face-centre-in-box breaks when bodies overlap.
- **Cost scales with people.** Per observation: the full-frame caption, one VLM call per
  dependant, plus fall-confirm. A three-resident room is three extra VLM calls per
  observation, the throughput bottleneck on a local model.

v1 is acceptable for daily-granularity wellbeing (meal attended, fell) but must not be
relied on as action recognition.

## Goals and non-goals

Goals.

- Continuous, per-person action labels over time, robust to short face occlusion and to
  multiple people in frame.
- Detect transitions, not just per-frame states.
- Cost dominated by cheap local models, with the expensive VLM called rarely.
- Run on a self-hosted box. CPU-only must work; GPU should accelerate.
- Reuse existing OSS for every model. No training from scratch in the first cut.
- Same closed vocabulary and `observation_actions` rows as today, so the API, MCP tools,
  meal, and fall layers inherit the upgrade for free.

Non-goals.

- Clinical gait analysis or centimetre pose accuracy.
- Fine-grained activities beyond the closed set in the first cut (the open `detail` field
  carries nuance via the VLM).
- A certified medical fall alarm. Best-effort remains the framing.

## Architecture and placement (the load-bearing revision)

The system is two services with a Redis boundary, not one pipeline.

```
INGESTION  (services/ingestion/stream.py)            dense frames live here
  RTSP via MediaMTX → decode every frame
  L0  Ultralytics .track()  → person boxes + stable track_id   (ByteTrack/BoT-SORT)
  L1  rtmlib RTMPose        → 17-kp skeleton per track
  L2  PYSKL ST-GCN++        → closed action per track per pose window
  L4  per-track state machine → emit on transition, debounce
        │  publishes: existing motion keyframe + {track_id, action, window} sidecar
        ▼ Redis  nurby:motion (+ action sidecar)
PERCEPTION (services/perception/pipeline.py)         sparse keyframes here
  YOLO detect, ArcFace face → person_id, OSNet re-id  (unchanged)
  binds track_id ↔ person_id via face-in-track-box on the keyframe
  L3  VLM verify (fall) + describe (open detail), rare
        │ writes
        ▼  observation_actions(+track_id,+source)  guardian events
```

Why ingestion: `services/ingestion/stream.py` already decodes every RTSP frame to run
motion detection (every 5th frame) and to write recording segments. It is the only place
with a steady frame cadence. Perception consumes `nurby:motion`, a sparse,
cooldown-throttled keyframe stream, and structurally cannot feed a temporal model.

Why keep detect/face/VLM in perception: they are keyframe-rate by design and already
wired to identity, alerts, search, and entitlements. We do not move them.

This split is the single biggest change from the v1 sketch, which implicitly assumed one
pipeline. It also reframes the old "keyframe rate vs temporal need" risk: that was not a
tuning knob, it was a placement decision, now resolved.

### L0. Tracking (in ingestion)

Use Ultralytics' built-in tracker: `model.track(..., persist=True)` with `bytetrack` or
`botsort`. We already depend on Ultralytics in `detector.py`, so this adds a stable
per-camera `track_id` with no new dependency and no new licence exposure. BoT-SORT adds
appearance to the association and handles occlusion better; ByteTrack is lighter. Default
ByteTrack, BoT-SORT behind a setting.

`track_id` is per-camera, per-session, short-term. Cross-camera identity stays the job of
OSNet re-id and ArcFace. Tracking gives short-term continuity; re-id and faces stitch it
to a `person_id`. Binding a track to a person and holding it through occlusion is the fix
for face-dependent attribution.

### L1. Pose (in ingestion)

Primary: **rtmlib** (RTMPose). Apache-2.0, dependencies are only numpy, opencv,
onnxruntime, with optional OpenVINO/TensorRT backends. RTMPose-m reports 90+ FPS on an
Intel i7-11700 CPU via ONNXRuntime; RTMPose-s is lighter again. This directly answers the
"pose is too expensive on CPU" worry from review: at those rates, top-down pose for a
handful of tracked people per camera is affordable on CPU, subject to the spike confirming
it on the real box with real person counts.

Output is a 17-keypoint COCO/HRNet skeleton, which is exactly the format the action model
below was pretrained on, so no keypoint remapping is needed.

Alternative: Ultralytics YOLO-pose (same dependency we already ship). Simpler to wire,
but heavier per person and keeps us on the AGPL dependency for this stage. rtmlib is the
better default because it is Apache and CPU-fast.

### L2. Temporal action model (in ingestion)

Use **PYSKL** (`kennymckormick/pyskl`, Apache-2.0): ST-GCN++ and PoseConv3D with model
zoos pretrained on **NTU-RGB+D 60 and 120**, whose label set already contains *falling
down, eating, drinking, sitting down, standing up, staggering, walking*. PYSKL's
pretrained models use the HRNet 17-keypoint 2D skeleton format, which matches RTMPose's
output. It ships a real-time skeleton-action demo, including a CPU example. MMAction2
(Apache-2.0) is the heavier parent toolbox and an equivalent fallback.

Feed a sliding window of per-track keypoints (start at ~32-48 frames, tune in the spike)
into ST-GCN++; map the NTU label to our closed vocabulary, unmapped labels collapse to
`unknown`. ST-GCN++ inference is millisecond-scale. Output: per-track action + confidence
per window, written with `source = "skeleton"`.

Heavier alternative for appearance-defined actions or poor pose: an RGB clip model
(VideoMAE / X3D, both with permissive weights) on the track crop. GPU-hungry. Keep behind
the same interface; default skeleton for cost. See the pose-quality gate in Risks.

### L3. VLM as a fused enricher, not a replacement (in perception)

Do not retire the VLM. Re-order it. The mistake in v1 is using the VLM as the
per-person, per-frame classifier, which is the O(people x frames) cost. The fix is not to
silence it but to stop fanning it out per person: HAR becomes the continuous, cheap,
per-track classifier, and the VLM stays at its current keyframe cadence, now **fed by
HAR** and cross-checking it. The two enrich each other.

- **HAR to VLM.** Pass each tracked person's action label + confidence into the VLM's
  prompt as ground-truth context (we already have `vlm.describe(extra_context=...)` and the
  cascade refiner for exactly this). The VLM stops guessing "someone near a table" and
  writes a grounded, consistent caption: "the resident the motion model reads as eating is
  having lunch at the window". Better captions, better search embeddings, for free.
- **VLM to HAR.** The VLM caption / open `detail` can confirm or correct the skeleton label
  ("drinking tea", not "eating"). Disagreement is a signal, not noise.
- **Agreement over time, a self-improving loop.** When HAR and VLM agree, write a
  high-confidence row and spend nothing more. When they disagree, that is a labelled hard
  case: route it (with the opt-in keypoint store) into a fine-tuning set. This is what
  closes the NTU domain gap (NTU's scripted "eating/drinking" differs from real
  eldercare). The system improves from its own disagreements without hand-labelling.
- **High-stakes still verifies.** A high-confidence skeleton `fallen` still triggers the
  VLM crop confirm, same `confirms_fall` policy as today.

So the VLM load drops from O(people x observations) to roughly its current keyframe rate,
while its output quality goes up because HAR grounds it. The `source` column records who
produced each row (`skeleton`, `vlm_crop`, or a fused `skeleton+vlm` agreement) so trust
and the training set are auditable.

### L4. State machine (in ingestion)

Per-track smoothing (majority vote over a short ring buffer) then emit on transition,
debounced with a minimum dwell per action. This produces a clean timeline and is what
answers "standing then suddenly eating": a transition in the smoothed per-track stream.
Fall and meal become consumers of this stream.

## Cross-service identity binding (new)

`track_id` is born in ingestion; `person_id` is resolved later in perception. They must be
joined, and review flagged this as unspecified. Plan:

- Ingestion stamps `track_id` (and the current smoothed action + window bounds) onto the
  motion keyframe it publishes, as sidecar fields on the Redis message.
- Perception runs its existing ArcFace pass on the keyframe. The recognised face box falls
  inside one tracked person box, which carries a `track_id`; that binds
  `track_id → person_id` for the life of the track.
- A short-lived map `(camera_id, track_id) → person_id` (Redis, TTL on track loss) lets
  ingestion-side action emissions in the gap between face hits still attribute to the
  right person.
- Until a track is bound to a `person_id`, its actions are buffered, not emitted, so we
  never write actions for unidentified people (also the privacy default).

This is real glue work and is the main integration risk after placement. The spike
prototypes it end to end on one camera.

## Open-source reuse and licensing (researcher view)

The HAR field is mature. Every layer has a strong, reusable implementation, so this is an
integration project, not a modelling one. Verified options and licences:

| Layer | Reuse | Licence | Note |
|---|---|---|---|
| Detection | Ultralytics YOLO (already in `detector.py`) | AGPL-3.0 | already a dependency |
| Tracking | Ultralytics `.track()` ByteTrack/BoT-SORT | AGPL-3.0 | no new dep; or original ByteTrack (MIT) for a clean path |
| Pose | **rtmlib / RTMPose** | Apache-2.0 | deps: numpy/opencv/onnxruntime; 90+ FPS on i7 CPU |
| Action | **PYSKL** ST-GCN++ / PoseConv3D | Apache-2.0 | NTU-RGB+D pretrained, 17-kp HRNet format matches RTMPose |
| Action (alt) | MMAction2 | Apache-2.0 | heavier parent toolbox, same models |
| RGB fallback | VideoMAE / X3D (HF, OpenMMLab) | Apache-2.0 / permissive | only if pose quality forces it |
| Fall references | badalyaz/fall-detection, aay-b/human-fall-detection, niraljshah/Fall_Detection | mixed | reference heuristics only, not core |

Two practical wins from the survey:

1. **Keypoint-format match.** RTMPose emits 17-keypoint COCO/HRNet skeletons; PYSKL's
   pretrained NTU models consume the same. The two compose with no remapping and the
   pretrained weights are usable directly. This removes the largest "build it ourselves"
   risk.
2. **Avoid the heavy frameworks where possible.** rtmlib deliberately strips the mmcv /
   mmpose / mmdet dependency stack down to onnxruntime, which is what makes it deployable
   inside our container without dragging in the full OpenMMLab build chain. Prefer rtmlib
   for inference; only reach for MMAction2/MMPose if we need to fine-tune.

Licensing strategy. We are already AGPL-3.0 through the Ultralytics detector, so the
**easy path** (Ultralytics track + pose + PYSKL + MMAction2) adds no new exposure and is
fine for an open-source self-host product. If a commercial, non-AGPL posture is ever
wanted, the HAR-specific layers already have Apache options (rtmlib pose, PYSKL/MMAction2
action) and the tracker has an MIT option (original ByteTrack); the lone AGPL anchor is
then the YOLO detector, which has separate-licence alternatives. So HAR does not deepen
the licensing problem and can be made licence-clean independently of the detector.

Avoid: BoxMOT / yolo_tracking and motcpp for tracking. Both are AGPL-3.0 and bring no
advantage over Ultralytics' built-in trackers or MIT ByteTrack.

## Data model changes

`observation_actions` gains, by migration:

- `track_id` (string, indexed): the per-camera track the action came from.
- `source` (string): `skeleton` | `vlm_crop` | `skeleton+vlm` (the two agreed) |
  `geometry` | `caption_backfill`, so continuous HAR rows, fused high-confidence rows, and
  VLM/legacy rows are distinguishable for trust tuning and for building the fine-tuning set
  from disagreements.
- optional `window_start` / `window_end` (timestamptz): the span a temporal label covers.

Keypoints: default to **not persisting** raw keypoints (privacy-lean, recompute on
demand). Add an opt-in `track_keypoints` table behind a setting only when collecting a
fine-tuning set. Nothing in the existing schema is dropped; the closed vocab, `action`,
`posture`, `confidence`, `detail`, indexes, and the wellbeing API/MCP all stay.

## Resource budget (revised, OSS-grounded, still spike-gated)

Per camera, on the dense ingestion stream rather than every raw frame (sample to a HAR
cadence, start ~8-12 fps):

- Decode + motion: already paid in ingestion.
- Track (Ultralytics ByteTrack): association only, negligible.
- Pose (RTMPose-m via onnxruntime): the recurring cost. Published numbers are 90+ FPS on
  an i7 CPU for single inference; real cost is per tracked person per sampled frame, so it
  scales with people. Cap tracked persons and gate pose to dependant-bound tracks, exactly
  as the VLM is gated today.
- Action (ST-GCN++): millisecond-scale per track per window.
- VLM: only fall confirm and occasional detail.

Net: continuous classification moves off the VLM onto cheap CPU models, and the OSS
benchmarks say this is feasible for modest person counts on CPU, with GPU as headroom. But
the honest goal must be stated correctly: cost scales with **people** (pose is per person),
not "cameras not people" as v1 claimed. The spike measures the real ceiling.

## Privacy

- Pose and skeletons are computed on-device, same as everything else. No frames leave the
  box.
- Default to not persisting keypoints, so we do not accumulate a biometric gait store
  unless an operator opts in for fine-tuning.
- HAR runs only for recognised, bound dependants by default (the binding gate above), so
  we do not build action profiles on strangers or non-consented people.
- Per-person blur and consent gating are upstream and unchanged.

## Phased migration (additive, reversible by setting)

- **Phase 0. Spike (1-2 days, gates everything below).** On the demo/test cameras and one
  representative CPU box, stand up rtmlib RTMPose + Ultralytics track + a PYSKL pretrained
  ST-GCN++ on a handful of clips. Measure: pose+track+action throughput at the target
  cadence for 1, 2, 3 people; the track_id ↔ person_id binding prototype end to end; and
  pose quality on genuinely low-end / wide-angle footage. Output: go/no-go numbers and the
  skeleton-only vs RGB-hybrid decision. Do not start Phase 2 until this lands.
- **Phase 1. Tracking (safe now, build in ingestion).** Ultralytics `.track()` in
  `stream.py`; stamp `track_id` onto published keyframes; implement the binding map and
  write `track_id` onto `observation_actions`. Ships value alone: stable identity through
  occlusion, fixing the worst v1 attribution failure, with no model swap.
- **Phase 2. Pose.** rtmlib RTMPose per track in ingestion; optional keypoint store behind
  a setting. No behaviour change yet; validates pose quality on real cameras.
- **Phase 3. Temporal model.** PYSKL ST-GCN++ producing the closed vocabulary with
  `source="skeleton"`. Run alongside the v1 VLM classifier behind a setting; A/B before
  switching the default. Demote the VLM to confirm + detail once skeleton quality is
  proven.
- **Phase 4. State machine + timeline.** Per-track smoothing and transition emission; move
  fall and meal onto the action stream; build the per-person action-timeline UI.

Each phase is shippable and reversible via a setting. v1 stays the fallback until Phase 3
proves out on real footage.

## Evaluation

- A labelled clip set from the demo/test cameras and any consented real footage, tagged
  with ground-truth action and transition times.
- Metrics: per-frame action accuracy, transition-timing error, fall precision/recall (the
  high-stakes one), false-alert rate per camera-hour.
- A/B v1 (VLM snapshot) vs Phase 3 (skeleton) on the same clips before flipping the
  default. Reuse the nightly-CI eval harness pattern from the agent work.
- Honesty gate: do not relax the "best-effort, not a medical alarm" framing until fall
  recall and false-alert rate clear an agreed bar on real footage.

## Risks and open decisions

- **CPU throughput at scale.** OSS benchmarks say RTMPose is CPU-real-time, but multi-person
  multi-camera on the actual self-host box is unproven. Mitigation: cap tracked persons,
  gate pose to bound dependants, stagger cameras; measured in Phase 0.
- **Pose quality on cheap cameras is a decision gate, not a footnote.** Low-res,
  wide-angle, ceiling-mounted, poorly lit feeds degrade 2D pose, which caps skeleton HAR
  and may force the GPU-heavy RGB clip model as the primary on those cameras. Phase 0 must
  read pose quality on genuinely bad footage and decide skeleton-only vs RGB-hybrid before
  Phase 3.
- **Cross-service binding** (above) is the main integration risk after placement; prototype
  it in Phase 0.
- **Cadence vs the existing motion budget.** Ingestion runs motion every 5th frame on
  purpose to save CPU; HAR wants a steadier sampled cadence on tracked persons. Reconcile
  the two loops so HAR sampling does not starve motion/recording.
- **NTU domain gap.** NTU "eating/drinking" classes are scripted; real eldercare differs.
  May need light fine-tuning on domain clips, which is why the opt-in keypoint store
  exists.
- **Licensing.** Easy path is AGPL (already true via the detector). A licence-clean HAR
  path exists (rtmlib + MIT ByteTrack + PYSKL); the detector is then the only AGPL anchor.

Open decisions for the build: ByteTrack vs BoT-SORT default; rtmlib RTMPose-m vs -s;
window length and HAR sampling fps; skeleton-only vs RGB-hybrid (Phase 0 output); persist
keypoints or not by default.

## Review log

- v1: initial sketch (single-pipeline assumption, VLM-as-classifier critique).
- v2 (this revision): senior-engineering review relocated HAR to ingestion, added the
  cross-service binding section, a Phase 0 spike, and corrected the "cost scales with
  cameras" claim to "scales with people". AI/ML-researcher review added the concrete OSS
  reuse stack (Ultralytics track, rtmlib/RTMPose, PYSKL ST-GCN++/PoseConv3D on NTU-RGB+D),
  established the RTMPose↔PYSKL keypoint-format match that removes most build-it-ourselves
  risk, grounded the CPU budget in published RTMPose benchmarks, and worked out the
  licensing matrix (Apache HAR layers; AGPL only via the existing detector).
