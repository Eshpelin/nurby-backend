# HAR build trace (Phases 1-4): what was built, what's verified, what's next

Honest record of the v-next HAR build, for the "trace what could go better" step. Plan:
`docs/har-design.md`. Spike: `docs/har-phase0-findings.md`.

## What shipped (6 PRs, all merged)

| PR | Phase | What | Verified by |
|---|---|---|---|
| #23 | 1 | `identity_binding.py` + pipeline wiring | 12 unit tests (a real stale-reuse bug caught + fixed) |
| #24 | 2-3 | `har_actions.py` (geometric backend + ST-GCN seam) + `har_state.py` | 11 unit tests |
| #25 | 4 | `person_action_segments` model + migration + retention + settings | host import + alembic head + 200-test suite |
| #26 | 4 | `GET /cameras/{id}/actions` + `broadcast_person_actions` | route registration + serializer test |
| #27 | 2 | `pose.py` + `har_runner.py` + gated `har_hook` in `stream.py` | 3 orchestration tests (scripted poses) |
| #28 | 4 | `CurrentActivityStrip` live overlay | `tsc --noEmit` clean |

## What is genuinely verified (unit-tested, host-validated)

The HAR **logic** is tested end-to-end at the function level:
- Identity binding: tightest-box, hold-through-occlusion, TTL expiry, three states, per-camera
  isolation. The careful part, and it caught its own bug.
- Action classifier: posture + motion, honest `unknown`, never fabricates `fallen`/`eating`.
- State machine: smoothing, debounce, transition segments, flush.
- Runner orchestration: scripted poses -> identity-attributed segments + live snapshot.
- Data layer: model, migration (single head), retention, settings allowlist.
- API: endpoint registered, serializer correct. WS helper present.
- Frontend: live strip typechecks.

## What is integration-pending (written against real interfaces, NOT run here)

These can only be verified on a real deployment (live ingestion stack + a camera; some need a
GPU / model weights). They are written against interfaces that were actually read, and every
one is gated by `guardian_har_enabled` (**default OFF**) so they cannot affect the live path:
- `stream.py` -> `har_hook` -> pose -> runner -> persist/broadcast, under the executor.
- `pose.py` actual inference + weight download.
- `persist_segments` DB writes; `broadcast_person_actions` reaching the browser.
- The `CurrentActivityStrip` rendering from a real `person_actions` push.

## The one piece deliberately left honest, not fabricated

**Cross-service identity reconciliation.** Ingestion's `tracker_id` space is not perception's,
and `person_id` resolves only in perception (faces). Wiring a real `identity_fn` requires:
ingestion publishes its track boxes on the (versioned) keyframe; perception binds faces to
*those* boxes with the tested `identity_binding` logic and writes a shared Redis map
`(camera, ingestion_track_id) -> person_id`; the runner reads it. Until that lands,
`har_hook._identity_fn` returns `None` and segments persist **track-anchored without a
person** rather than with a guessed one. The binding *logic* is built and tested; only this
reconciliation remains. This is exactly the "make sure we track the right person" concern, and
it is left unfinished-but-honest instead of subtly wrong.

## Remaining UI (small, on the existing endpoint)

- Historical activity timeline: extend `FollowFeedPage` with an action band reading
  `GET /cameras/{id}/actions`.
- Observation-card action chip on `ObservationGroupCard`.
Both are thin; deferred because they show data that only flows once HAR is enabled.

## What could go better (risks + next actions, in priority order)

1. **Finish identity reconciliation** (above). Nothing guardian-facing should show actions
   until this is correct, because the whole value depends on the right person. Highest
   priority.
2. **Real-hardware throughput** is still unmeasured on a target NUC (Phase 0 used an M4-Pro
   CPU, optimistic). Confirm the global concurrency cap + cadence before enabling on multi-
   camera CPU boxes. The hook is single-camera-gated today; a process-wide HAR semaphore (per
   `docs/har-design.md` 3.1) is not yet implemented and should be before multi-camera use.
3. **Action coverage is geometric-only.** standing/sitting/lying_down/walking are real;
   eating/drinking/sleeping/playing return `unknown`. Those need ST-GCN (seam built, weights +
   mmcv on a real box) or the VLM-fusion path (designed, not yet wired into the runner).
4. **VLM fusion not wired into the runner.** The design (HAR label -> VLM context; agreement ->
   high-confidence, disagreement -> training set) lives in `vlm_queue` for v1; the runner does
   not yet feed segments to the VLM. Build after identity is correct.
5. **Privacy gating on the operator endpoint.** Guardian endpoints already gate
   (delay/consent/reveal); the new `/cameras/{id}/actions` is operator-scoped (full data). If
   it is ever surfaced to guardians, it must reuse `reveal_box_for` + privacy zones + consent,
   per `docs/har-design.md` 4.1/6.
6. **No real-footage accuracy eval yet.** The classifier is unit-tested on synthetic skeletons,
   not validated on real eldercare footage. Stand up the labelled-clip eval (plan 8) before
   trusting fall/meal off the skeleton path.
7. **`fallen` ownership.** The classifier intentionally never emits `fallen`; the existing fall
   module owns it. When the runner matures, decide whether fall stays geometry+VLM (current) or
   consumes the skeleton `lying_down` signal as an additional prior.

## Update: follow-up work landed (PRs #30, #31)

Closed since the trace was written:
- **Identity reconciliation (was the #1 gap): DONE.** `har_idmap.py` writes
  `(camera, ingestion_track_id) -> person` to Redis from perception (faces bound to the
  ingestion track boxes carried on the v2 keyframe payload), TTL-refreshed on presence; the
  ingestion hook reads it and attributes segments/live to the right person or to none.
  Unit-tested vs a fake Redis. The careful part is no longer a stub.
- **Concurrency cap (was #2): DONE.** Process-wide `min(4, cpu-2)` semaphore around pose.
- **Historical activity timeline (was remaining UI): DONE.** `ActivityTimeline` on the camera
  page reading `/cameras/{id}/actions`; tsc clean.

Still open: ST-GCN weights/runtime (seam only -> eating/etc are `unknown` until then); VLM
fusion into the runner; observation-card action chip; Phase 5 productization (operator config,
test mode, deployment profiles, degradation, compliance surfacing); and the verification gates
(real-NUC throughput, real-footage accuracy eval). Identity is now correct-by-construction in
code, but still unverified on a live camera + stack.

## How to turn it on (when ready)

Set `guardian_har_enabled=true` after: identity reconciliation done, throughput confirmed on
target hardware, and a real-footage eval passes the agreed fall/false-alert bar. Until then it
is dormant and safe.
