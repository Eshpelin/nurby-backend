# Guardian by Nurby. V1 Build Plan

Status: V1 built + smoke-passed (branch guardian-by-nurby). See "V1 result" at end.
Derived from: docs/guardian-portal-product-brief.md section 24 (locked decisions).
Principle: thin layer over existing engine. Fork no detection/identity/AI logic.

## Architecture stance

Guardian is a permission-and-view layer. New domain rows bind an existing
`User` (role `guardian`) to an existing `Person`, attach entitlements and alert
prefs, and log every view. Presence, alerts, recaps, and search all delegate to
existing Nurby subsystems (Journey/Observation, Rules/Events, DailyDigest,
pgvector search).

## Data model (new tables, single migration chained to current head)

1. `Facility`
   - id, name, slug, timezone, created_at
   - settings overrides: `reveal_min_confidence` (nullable), `max_cameras_per_person` (nullable)
   - For V1 self-host, one default facility auto-created (the household).

2. `GuardianLink` (the binding, the spine)
   - id, facility_id, person_id (FK Person), guardian_user_id (FK User)
   - relationship_label (mother/father/grandparent/carer)
   - tier: full | summary | alerts_only
   - alert_prefs (JSON): {arrived, departed, picked_up, entered_zone, left_zone, not_seen} booleans
   - entitlements (flags): premium (bool), live_presence (bool), live_video (bool), audio (bool)
   - is_primary_parent (bool): used for "extra guardians free if one parent paid"
   - reveal_min_confidence (nullable, stricter-only override)
   - granted_by_user_id (facility admin), granted_at, expires_at (nullable), revoked_at (nullable)
   - status derived: active if not revoked and not expired

3. `ApprovedPickup` (verified-pickup registry)
   - id, person_id, name, kind (person|vehicle), linked_person_id (nullable FK Person), vehicle_plate (nullable)
   - photo_path (nullable), active, created_at, created_by_user_id

4. `GuardianAccessLog` (audit, facility-visible)
   - id, guardian_link_id, guardian_user_id, person_id, action (status|image|timeline|live|recap|search), at, ip (nullable), detail (JSON)

## Entitlement / delay / throttle engine (services/guardian/entitlements.py)

Pure, unit-tested helpers:
- `effective_delay_seconds(link)` -> 0 if live_presence else 1800.
- `can_view(link, capability)` -> bool by tier + flags + active.
- `image_allowed(link, last_image_at, now)` -> bool (free = 1/hour; live_video lifts cap).
- `cutoff_time(link, now)` -> now - delay, used to filter presence/images/timeline.
- `reveal_threshold(link, camera, facility, system_default)` -> max(floors), stricter-only.
- `extra_guardian_unlocked(person)` -> any active link on person with a paid flag.

## Presence (services/guardian/presence.py)

- `dependant_status(db, link, now)` -> {state: at_facility|away|unknown, zone/camera label, last_seen_at, seconds_ago} computed from Journey/Observation filtered to `cutoff_time` and to cameras the facility exposes. Only the bound Person; never reveal others. Honors blur and reveal threshold. Calm "last seen Xs ago", never invents a location.
- `latest_image(db, link, now)` -> most recent observation thumbnail at/under cutoff, blurred per privacy, throttled.

## API (services/api/routes/guardian.py, prefix /api/guardian)

Guardian-facing (scoped to caller's own active links):
- GET /me -> guardian profile + dependants (links) summary
- GET /links/{id}/status
- GET /links/{id}/image (throttled, logged)
- GET /links/{id}/timeline (arrival/pickup/zone events)
- GET /links/{id}/recap (premium)
- GET /links/{id}/live (live_presence/live_video gated)
- PATCH /links/{id}/alerts (toggle within facility-allowed set)
- POST /links/{id}/search (premium smart search, scoped to dependant)

Facility-admin (require_admin):
- POST /facilities, GET /facilities, PATCH /facilities/{id}
- POST /links (grant), GET /links, PATCH /links/{id} (tier/entitlements/expiry), DELETE /links/{id} (revoke now)
- GET /persons/{id}/pickups, POST .../pickups, DELETE .../pickups/{pid}
- GET /access-log (filter by person/guardian)

Auth deps: `get_current_guardian`, `require_link_access(link_id)` (caller owns the active link or is admin).

## Alerts wiring (reuse Rules/Events)

- On guardian link create, ensure presence/arrival/pickup signals exist. Implement guardian alert dispatch as a fan-out that respects `alert_prefs` and the guardian's notification channel. Reuse existing notification dispatch (telegram/email/in-app). Pickup verified against ApprovedPickup.
- Arrival/departure derived from Journey enter/exit on facility cameras.

## Reveal/blur

- Reuse Person.privacy_blur + existing blur pipeline. Reveal only the bound person above `reveal_threshold`. Everyone else stays anonymous bodies. Image endpoint serves the existing blurred thumbnail; never an unblurred crop of non-dependants.

## MCP (services/mcp/server.py)

- Add guardian-scoped read tools: `guardian_dependant_status`, `guardian_recent_events`. Resolve guardian from token, enforce same entitlements (delay/throttle/blur), only the caller's links.

## Frontend (frontend/src/app/guardian/...)

Guardian Panel (guardian role):
- /guardian -> dependants overview, each a calm status card (10-second check), green/away/unknown.
- /guardian/[linkId] -> status detail, latest image (throttled state shown), arrival/pickup timeline, alert toggles, premium upsell states (locked live video / live presence / audio / recap), delayed-data banner for free tier.
Facility admin (admin role), under /settings or /guardian/admin:
- Grant/revoke links, set tier + entitlement flags, expiry.
- Approved-pickup registry editor.
- Access log viewer.
Reuse auth context, theme, navbar. Role-aware nav (guardian sees only Guardian Panel).

## Settings (shared/app_settings.py + system route whitelist)

- `guardian_enabled` (default True)
- `guardian_free_delay_seconds` (default 1800)
- `guardian_free_image_interval_seconds` (default 3600)
- `guardian_reveal_min_confidence` (default 0.90)
- `guardian_max_cameras_per_person` (default 12)

## Tests

- tests/test_guardian_entitlements.py: delay, throttle, tier gating, stricter-reveal, extra-guardian-unlock.
- tests/test_guardian_presence.py: cutoff filtering, only-bound-person, never-invent-location.
- tests/test_guardian_api.py: scoping (guardian cannot read another's link), revoke kills access, admin grant flow, free vs paid response shape.

## Rollout / sequencing

1. Models + migration. 2. Settings. 3. Entitlements engine + tests. 4. Presence + tests. 5. API routes + auth deps + tests. 6. Alert fan-out. 7. MCP tools. 8. Frontend guardian panel. 9. Frontend admin. 10. End-to-end smoke on the running stack. Iterate.

## V1 result (smoke-passed against the running stack)

All built, lint-clean, 111 unit assertions green, and verified end to end on
real data:
- Settings expose guardian_* and PATCH-validate.
- Default facility auto-creates; admin grants a link to an existing Person.
- Free tier: status + timeline + /me all delayed 30 min; recap 402-gated.
- Upgrade flags (premium/live_presence/live_video) flip gating live: recap 200,
  delay removed, image throttle lifted.
- Presence over real Observation rows: state/zone ("Entrance"), 1-item timeline,
  and a real 89 KB blurred image served.
- Alerts: "arrived" green (info), unrecognized pickup yellow (warning), matched
  pickup "picked up by Mom" (info).
- Security: a second guardian gets 404 on another's link and an empty /me;
  revoke flips status to 410 immediately.
- Guardian MCP tool registered and self-scoped.

### Known V1 boundaries (next increments, not blockers)
- Alert transport is in-app Notification + recipient list. Per-guardian push
  (Telegram/email per link) reuses the same decision logic when wired.
- Perception does not yet auto-call POST /internal/alerts on arrival/departure;
  that hook is the integration seam and is admin/API-key callable today.
- Facility camera scoping is all-cameras for the single-household deploy; the
  max-cameras-per-person governor is modeled and settable.

## Round 2 (push transport + perception wiring + live/search)

- Per-guardian delivery: alerts now push to each recipient's paired Telegram +
  email (best-effort, isolated) and broadcast the in-app notification over WS.
  services/guardian/delivery.py.
- Perception wiring: journey open -> "arrived", journey finalize -> "departed",
  resolved to the bound Person's active links, fire-and-forget and isolated.
  services/guardian/lifecycle.py + journey_tracker hooks.
- Live endpoint (live_presence/live_video gated) and premium dependant-scoped
  smart search; delayed flag derived from entitlement.
- Frontend premium smart-search panel on the dependant detail page.
- 83 guardian unit assertions green; rebuilt api/perception/frontend; smoke
  re-verified (live 200, search delayed=false on a live link).
