# Guardian by Nurby. Product Brief

Status: exploration / draft v0.2 (build-locked)
Owner: Product
Last updated: 2026-06-07
Scope: product, UX, privacy, business model, go-to-market. Build decisions in section 24.

> Product name. "Guardian by Nurby." Public brand avoids the words
> "monitoring" and "surveillance" (see Positioning). Guardian is a view layer
> on top of the existing Nurby engine, not a separate product.

---

## 1. Executive summary

A dedicated product, separate from the Nurby operator dashboard, that lets a
specific guardian follow a specific person inside a facility, and nobody
else's. The guardian sees presence, location, safe arrival and verified
pickup, and optional blurred live video and AI summaries. Everyone other than
the bound person stays anonymous.

The opportunity is real and the downside is real. Done carefully this is a
high-trust safety product with strong willingness to pay. Done carelessly it
normalizes surveillance of vulnerable people and can be weaponized against
them. The whole brief is organized around making safety, consent, and data
minimization the spine, not a feature.

**The one-line bet:** parents and families will pay for peace of mind, and
facilities will pay for a safety differentiator and liability cover, if and
only if the privacy guarantee actually holds in practice.

---

## 2. Strategic stance (the guiding principles)

These are non-negotiable framing decisions, not preferences.

1. **Launch narrow.** Do not ship the broad multi-segment, many-feature
   vision. Pick one wedge feature and one buyer. Earn the rest.
2. **Peace of mind, not surveillance.** Positioning is a product decision. It
   determines which features get built and how it is sold. Optimize to
   reassure a worried family, not to let someone watch.
3. **Consent, custody, and abuse-prevention are the architecture, not edge
   cases.** A guardian-to-person binding plus facility video is, in the wrong
   hands, a stalking and custody-dispute tool. This is designed for first.
4. **Default-deny, reveal-on-proof.** Blur everyone by default. Reveal the
   bound person only above a high identity-confidence threshold. The system
   must fail to blur, never fail to expose.
5. **Awareness aid, never a safety guarantee.** Never sell or imply that the
   product keeps a person safe or catches every incident. That is liability
   the company cannot carry.
6. **Minimize and localize data.** Short guardian-facing retention.
   Edge / self-hosted posture so footage can stay at the facility.
7. **Walk away from the creepy high-margin features.** No behavioral,
   aggression, or distress inference on children. Ever, until the company is
   mature, trusted, and legally covered, and probably not even then for kids.

---

## 3. Problem and opportunity

**The parent problem.** Families place a vulnerable person (a child, an
elderly parent, a patient) in someone else's care for hours a day and have
almost no visibility. Their real questions are narrow and emotional:
- Did they arrive safely?
- Are they here and okay right now?
- Who did they leave with?

Today this is answered by a phone call, a pickup line, or nothing.

**The facility problem.** Facilities compete on trust and safety, carry
liability for incidents and disputes, and have cameras they cannot turn into
parent-facing value without exposing every other family.

**Why now.** Cheap cameras, on-device AI (Nurby already does detection,
faces, plates, zones, audio events, natural-language search, narrative
summaries), high smartphone penetration, and mobile-money rails that make
small recurring consumer payments collectable in markets like Bangladesh.

---

## 4. Positioning and vision

**Positioning statement (to test):**
> Peace of mind for families, without surveillance for everyone else. Know
> they arrived, where they are, and who they left with. Nothing more, and
> nothing about anyone else.

**Vision.** Answer the three questions a guardian actually has, with certainty
and calm, while every other person in the frame stays private. Timelines,
summaries, and analytics are supporting cast, not the headline.

**Anti-vision (what this is NOT):**
- Not "watch your child all day." Long live-watching is a failure mode that
  gets the product banned and is not where the value is.
- Not staff surveillance. Teachers and carers are people working, protected by
  the same anonymity, and positioned as beneficiaries (evidence in disputes).
- Not behavioral judgment of children.

---

## 5. Target market and beachhead

**Segments, ranked for sequencing:**

| Priority | Segment | Killer feature | Notes |
|---|---|---|---|
| 1 (beachhead) | Daycares, preschools, coaching centers | Verified pickup + safe arrival | Owner-operated, fast deciders, compete on safety |
| 2 | Assisted living / eldercare | Falls + activity + meals | Same "follow one person" engine, buyer is adult children |
| 3 | Special-needs facilities | Presence + zone alerts | High need, high sensitivity, slower sale |
| Later | Hospitals, dialysis | Presence + safety | Heavy regulation, do not anchor v1 here |

**Beachhead decision.** Daycares, preschools, and coaching centers first.
Highest emotional value, lowest creepiness, easiest institutional yes, and
they market safety as an enrollment differentiator. K-12 schools are
deliberately deferred (most staff resistance, most surveillance backlash, most
guardians per decision, lowest willingness to pay per seat).

**Wedge feature.** Safe Arrival + Verified Pickup + plain-text presence. The
trojan horse that earns the right to live video and AI summaries later.

---

## 6. Personas

- **Worried Parent (primary).** Dual-income, anxious, time-poor, smartphone-
  first, price-sensitive. Wants a 10-second "they're fine" check. Will pay a
  small recurring fee for depth.
- **Remote Guardian (grandparent, traveling parent).** Wants daily summary and
  safe-arrival/pickup, not live video. Summary tier.
- **Facility Owner / Principal (economic buyer).** Buys safety differentiation,
  liability cover, and parent satisfaction. Wants control and simplicity.
- **Teacher / Carer (gatekeeper, can kill deployment).** Fears being watched.
  Won over by anonymity + dispute-evidence + transparency about viewing.
- **School Admin (operator).** Maps cameras to zones, manages roster,
  guardianship, pickup registry, audit log, alert policy.
- **Adult Child of a resident (eldercare persona).** Wants wellbeing signals,
  falls, meals attended, time out of room.

---

## 7. Jobs to be done

1. "Tell me they arrived safely." (notification)
2. "Tell me they're okay right now." (presence)
3. "Tell me who they left with." (verified pickup)
4. "Help me find where they are when I'm anxious." (location + short clip)
5. "Tell me what their day was like." (AI summary, premium)
6. "Let me answer a specific question about today." (smart search, premium)
7. (Facility) "Give parents value without exposing other families."
8. (Facility) "Protect my staff and me in disputes."

---

## 8. User journeys

**J1. Guardian onboarding (trust won or lost here).** Facility enrolls person,
invites and binds the guardian with a permission tier. Guardian installs,
verifies via phone OTP + facility code, sees exactly one person, and is shown a
plain-language "what you can and cannot see, and that every view is logged"
screen. That screen is a feature.

**J2. The 10-second check-in (core daily loop).** Open app. Above the fold:
"Ahmed is at school. Classroom B. Seen 30 seconds ago." Calm green state. Most
sessions end here. No video.

**J3. Safe arrival / departure.** Push at arrival and pickup. "Ahmed was picked
up by you" or "by Vehicle ABC-1234 at 3:12 PM." Highest-value, most-shared
moment. Optimize relentlessly.

**J4. The worried moment.** Alert or anxiety. "Where now" shows current
location + recent movement, optional short blurred live clip. Job: resolve
anxiety fast and accurately, then close.

**J5. Facility admin.** Map cameras to named zones once, enroll, bind
guardians, set tiers, manage approved-pickup registry, review audit log,
configure alerts, use kill switches.

---

## 9. Product scope and roadmap

MoSCoW for v1 (the daycare wedge):

**Must:**
- Plain-text presence ("named zone, last seen Xs ago").
- Safe arrival and departure notifications.
- Verified pickup against an approved-pickup registry (+ vehicle plate when
  available).
- Guardian binding + permission tiers + instant revoke + expiry.
- Default-deny face blur of everyone but the bound person.
- Full access audit log, visible to the facility.
- Bangla-first, mobile-first, low-bandwidth, mobile-money billing.
- Edge / self-hosted footage option.

**Should:**
- On-demand short blurred live clip (session-capped).
- Daily AI recap (warm, factual tone).
- Zone / restricted-area alerts.

**Could (v2):**
- Location history search (natural language).
- Weekly trend reports.
- Multi-child / multi-guardian management.
- Eldercare variant (falls, meals, time-out-of-room).

**Won't (explicit non-goals for now):**
- Behavioral / aggression / distress inference on children.
- Unrestricted live watching.
- Guardian bulk export of footage.
- Hospitals / dialysis vertical.
- Cross-facility tracking of a person.

---

## 10. Identity and relationship model

Graph: **Facility -> Person (student/resident/patient) -> Guardian-link**.

- A Person belongs to one Facility at a time.
- A Guardian-link binds a Guardian account to a Person, with a permission
  tier, a scope, an expiry, and a granted-by (always the facility).
- A Person may have multiple Guardian-links (mother, father, grandparent,
  carer), each independent and independently logged.
- The Person identity is the binding the privacy guarantee rests on. it must
  degrade safely (see Risk register: misidentification).

---

## 11. Permission model

Few named tiers, not a checkbox matrix (matrices cause dangerous
misconfiguration by non-technical admins).

| Tier | Can see | Typical holder |
|---|---|---|
| Full Guardian | Presence, location, history, blurred live, alerts, pickup | Parent |
| Summary Guardian | Presence, daily summary, arrival/pickup. No live video | Grandparent, remote parent |
| Alerts-Only | Arrival + safety alerts only | Secondary contact |

Cross-cutting rules:
- The **facility grants and revokes. the guardian never self-grants.**
- Every link has **expiry** and **instant revoke** (custody change, transfer,
  restraining order).
- **Audio** and **live video** are separate permissions, default off.
- All access logged; the log is visible to the facility.

---

## 12. Privacy and consent framework (deepest section)

The differentiator and the deepest risk.

**Reveal model.**
- Blur every face by default. Reveal the bound Person only above a high
  identity-confidence threshold.
- The catastrophic failure is revealing the wrong person to a guardian, not
  over-blurring the target. So reveal is conservative and **fails to blur**.
- Others appear as anonymous bodies for context. faces are identity, bodies
  are situational awareness.

**Consent (multi-party, minimum two-sided).**
- The facility consents to operate it.
- The guardians of **all** persons consent to AI processing of their person
  even when blurred. A facility cannot unilaterally opt every family in.
- Where the person has rights of their own (older children, competent
  adults), their consent or assent is part of the model.

**Data minimization.**
- Short guardian-facing footage retention by default (hours to days).
- Summaries / reports may persist longer than raw video.
- Encrypt at rest and in transit. regionalize storage.
- Strongly prefer **edge / on-prem** so footage stays at the facility. This is
  also a sales and trust asset and a hedge against future regulation.

**Transparency as a feature.**
- Show the guardian the blur is active.
- Show staff when guardian viewing is active and that they are protected.
- Make the audit log visible to the facility.

**Honest hard truth.** The entire guarantee rests on identity binding being
correct under identical uniforms, occlusion, and lookalikes. Design assuming
misidentification will happen and making its consequences non-catastrophic:
blur-by-default, conservative reveal, visible "not certain" states, trivial
human correction.

---

## 13. Alert taxonomy

Tiered by trust-risk. ship green, defer yellow, avoid red for children.

**Green (v1, high value, low risk):** arrived, departed, picked up by X /
Vehicle ABC-1234, entered/left a designated zone, not-seen-for-N-minutes.

**Yellow (later, with care):** crowd/density, loud-noise in approved zones,
fall detection (primary value in eldercare).

**Red (avoid for children, likely never):** aggression, abnormal behavior,
distress inference. Biased, unreliable, ethically loaded, lawsuit magnet. If a
facility insists, gate behind explicit institutional opt-in and never market
for children.

Every alert carries a confidence level and an honest "not certain" state.
Tune the scary alerts for precision over recall. a false "they left the
building" destroys trust faster than a missed event.

---

## 14. AI-generated reporting

Where premium value and differentiation live (reuses Nurby's narrative digest,
zones, plates, and natural-language search).

- **Daily recap**, warm and factual: "Arrived 8:14, morning in Classroom B, 40
  minutes outdoors, lunch on schedule, picked up by you at 3:12."
- **Weekly trends**: attendance consistency, indoor/outdoor balance, routine
  changes. Framed as gentle wellbeing signals, not judgments.
- **Smart search (premium):** "When did Ahmed leave the classroom?", "Who
  picked him up Tuesday?"
- **Eldercare variant:** activity level, meals attended, falls, time out of
  room.

Guardrail: reports state observed activity factually. No behavioral or
developmental judgments about children (defamation and bias risk).

---

## 15. Monetization

**Model: B2B2C.** Facility pays for the platform and base access. parents pay
for premium follow features.

Rationale:
- Pure parent-paid fails. the facility controls cameras and consent and will
  not deploy for a vendor's consumer subscription without payment or a clear
  enrollment benefit.
- Pure facility-paid underfunds the depth parents want. facilities buy safety
  + differentiation, not analytics depth.
- Hybrid aligns each party's willingness to pay with what they value.

Concretely:
- **Facility** pays per-student or per-camera SaaS, including presence, safe
  arrival, and verified pickup for all guardians (the free trust-building
  base). In Bangladesh this can fold into existing fees.
- **Parents** pay premium for blurred live clips, daily AI summaries, history
  search, weekly reports, multi-child, extra guardians. billed via mobile
  money.
- Keep the free parent tier genuinely useful. free word-of-mouth among parents
  is the cheapest acquisition channel.

---

## 16. Subscription tiers

**Facility plans:**
- **Safety Base** (wedge): presence, safe arrival, verified pickup, approved-
  pickup registry, audit log. Priced as a no-brainer enrollment differentiator.
- **Facility Plus:** zone/restricted alerts, operator presence/attendance
  reporting, staff-dispute evidence tools.

**Parent plans (on top of facility-provided free base):**
- **Free (always):** live presence, arrival + pickup notifications.
- **Parent Premium:** short blurred live clips, daily AI summary, history
  search, multi-guardian, more retention.
- **Family Premium:** multiple children, weekly trend reports, priority alerts.

---

## 17. Edge cases and risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Misidentification (lookalikes, uniforms, occlusion) | Critical | Blur-by-default, conservative reveal above high confidence, "not certain" states, easy human correction, never assert an unsure location |
| Custody disputes / restraining-order violation | Critical | Facility grants/revokes only, instant revoke, link expiry, full audit log, facility is the arbiter, documented policy |
| Abuse / stalking vector | Critical | Same as above + identity verification, anomaly detection on access, no self-grant |
| Other-children consent not obtainable | Critical (viability) | Validate before build. if unsolved, the model cannot ship in good conscience |
| Data breach / child-video honeypot | Critical | Minimize retention, encrypt, regionalize, edge/on-prem posture |
| Liability for missed incident | High | Position as awareness aid, never a safety guarantee. disclaimers in product language |
| Staff resistance kills deployment | High | Anonymity for staff, dispute-evidence benefit, transparency about viewing |
| Credential sharing / screen recording | Medium | Device binding, OTP, watermark guardian video with identity, session limits, no bulk export, legal terms |
| Child not visible / blind spots | Medium | "Last seen 9:50, Playground." never invent a location. calm absence UI |
| Multiple cameras simultaneously | Medium | Highest-confidence single location, movement as a sequence, smooth handoff |
| Visitors and staff | Medium | Anonymous bodies, never identified to guardians |
| Child transfer / withdrawal | Medium | Facility revokes link, access ends immediately and historically per retention/legal-hold |
| Shared guardianship conflict | Medium | Facility as arbiter, each guardian independently tiered and logged |
| False alerts erode trust | Medium | Confidence levels, precision over recall on scary alerts |

---

## 18. Trust, safety, and liability posture

- The product is an **awareness aid**, not a safety guarantee. This is stated
  in product language, not buried in an EULA.
- Footage minimized, encrypted, regionalized, and preferably on-prem.
- Every guardian view watermarked and logged.
- Clear, fast incident-response and revoke paths for custody/abuse situations.
- A published, plain-language privacy promise that the company can actually
  keep, and is held to.

---

## 19. Go-to-market: Bangladesh

**Why it can work:** large price-sensitive but safety-obsessed parent base, a
huge private school and coaching-center sector competing on trust, rising
dual-income families driving daycare demand, very high smartphone and mobile-
data penetration, and bKash/Nagad making small recurring consumer payments
collectable where cards are rare. The data-protection regime is still
maturing. an opportunity, and a reason to self-impose strong privacy now.

**Beachhead:** premium daycares, English-medium preschools, and coaching
centers in Dhaka and Chattogram.

**Wedge:** Safe Arrival + Verified Pickup + plain-text presence.

**Motion:**
- Sell to the operator, activate the parents. land flagship facilities, make
  the parent experience so reassuring that parents demand it elsewhere. parent
  pull is the cheapest growth.
- Bangla-first UI, bKash/Nagad billing, works on 3G and budget phones.
- Lead with the "your footage never leaves the campus" edge / self-hosted
  story. a trust message competitors using cloud-only cannot match, and a
  regulation hedge.
- Pricing: facility SaaS folded into fees. parent premium at a low, recurring,
  mobile-money-friendly price. free tier seeds word of mouth.
- Reference selling: a few respected principals as references. trust here flows
  through institutions.

---

## 20. Success metrics

**Trust / safety (leading, most important):**
- Misidentification rate (wrong-person reveal). target near zero, hard ceiling.
- Number of privacy incidents. target zero.
- Time-to-revoke a guardian link.

**Engagement (health, not vanity):**
- Daily active guardians doing a presence check.
- Safe-arrival / pickup notification open rate.
- Median session length (watch for it climbing. long watching is a red flag,
  not a win).

**Commercial:**
- Facilities live, parent free-to-premium conversion, parent churn, revenue per
  facility.

**Operational:**
- Alert precision (false-alarm rate on green alerts).
- Uptime and notification latency (arrival/pickup must be near-instant).

---

## 21. Validation plan (before any engineering)

Pressure-test the riskiest assumptions on a whiteboard and with real people,
not after launch:

1. Will a handful of facility owners pay for safe-arrival/pickup and place
   cameras under a strict consent model?
2. Will the **other** families consent to AI processing of their blurred
   person? This gates the entire model's legality and viability.
3. What will regulators and any education authorities tolerate?
4. Can misidentification be driven low enough that the privacy guarantee holds
   in practice?
5. Custody/abuse: review the grant/revoke/audit model with a lawyer and a
   parent who would object.

If 2, 4, or 5 cannot be solved, do not build. learning that on a whiteboard is
the cheapest outcome available.

---

## 22. Open questions

- Audio: none / approved-zones-only / facility-enabled. default to none for v1.
- Retention windows per data type and per market.
- Whether the person (older child, competent adult) gets a voice or visibility
  into who follows them.
- Insurance and legal structure for liability exposure.
- Brand and naming (must avoid "monitoring" / "surveillance").

---

## 23. Glossary

- **Guardian.** A person granted a link to follow one Person.
- **Person.** The followed individual (student, resident, patient).
- **Facility.** The operator (daycare, school, care home) that owns cameras and
  controls grants.
- **Guardian-link.** The binding of a Guardian to a Person with a tier, scope,
  expiry, and granted-by.
- **Reveal.** Showing the bound Person unblurred to their Guardian above a
  confidence threshold. everyone else stays blurred.
- **Wedge.** Safe Arrival + Verified Pickup + presence. the narrow first
  product.

---

## 24. v0.2 product decisions (locked for build)

These 11 decisions supersede anything above that conflicts. They are the
contract the V1 build implements.

### 24.1 Name
Product is **Guardian by Nurby**. In code and UI the guardian-facing surface is
the **Guardian Panel**. Internal noun for the followed individual stays
**Person** (reusing the existing People system). The followed person shown to a
guardian is their **dependant**.

### 24.2 Reuse everything
Guardian is **not** a parallel stack. It is a thin permission-and-view layer on
the existing Nurby backend and dashboard. Concretely it reuses, unchanged:
- People system (Person, FaceEmbedding, FaceCluster, BodyCluster) for identity.
- Observation / Journey for presence and last-seen location.
- Rules / Events / Notification channels (Telegram, email, webhook, in-app) for
  alerts.
- DailyDigest / Summary / pgvector search for AI recaps and smart search.
- User / auth / camera-access for accounts and scoping.

The Guardian Panel adds only: the guardian-to-person binding, entitlements,
audit logging, the delayed/throttled free-tier view, and the guardian-facing
screens. No detection, identity, or AI logic is forked. **Any future
improvement to the People/detection engine is inherited automatically** because
Guardian references live Person rows, never a frozen snapshot.

### 24.3 MCP for the Guardian Panel
Yes, planned. Expose **guardian-scoped, read-only** MCP tools so a guardian can
ask "is my child at school right now?" from an MCP client. Tools are strictly
scoped to that guardian's active links, honor the same delay/throttle/blur
entitlements as the app, and never expose another person. Built on the existing
read-only MCP server (services/mcp/server.py) with a guardian token scope.

### 24.4 Camera limit consideration
Add a configurable ceiling so a guardian deployment cannot fan a single person
across unbounded cameras (cost + abuse surface). New system setting
`guardian_max_cameras_per_person` (default sane cap, facility-overridable). Not
a hard product limit, a safety governor that is logged when hit.

### 24.5 Configurable face-reveal confidence
The reveal threshold is configurable, defense-in-depth. Default high. Settable
at **facility** level and **per camera** (camera wins). A parent can *request* a
stricter threshold for their own dependant but can never *loosen* it below the
facility floor. New setting `guardian_reveal_min_confidence` (system default),
optional `Camera`-level override, optional per-link stricter override. Reveal
still fails to blur, never fails to expose.

### 24.6 Onboarding via the existing People system
No photo upload by the parent at onboarding. The facility **selects an existing
Person** from the People system and binds the guardian to it. The face/body
engine has usually already clustered that child from real footage, so identity
is grounded in actual sightings, not a parent selfie. If the Person does not yet
exist, the facility creates it through the normal People flow (which can adopt
an auto-discovered cluster). This guarantees the binding tracks the live
identity and inherits every future engine update by default.

### 24.7 Guardian-controllable alert toggles
Each guardian controls which supported alerts they receive **per dependant**.
Toggles cover the green alert set (arrived, departed, picked up, entered/left
zone, not-seen-for-N-min). The facility sets which alerts are *available*; the
guardian opts in/out within that set. Stored as alert preferences on the
guardian link. Defaults: arrival + pickup on, the rest off.

### 24.8 Free vs paid: images and live video
- **Free parent tier** gives **text/status updates plus at most 1 image per
  hour** per dependant. Enforced server-side (throttle).
- **Live video** is never free. It is a **separately purchasable package** a
  parent signs up for, independent of other premium.
- **Multi-child and multi-parent are free.** A family with several children, or
  several parents following one child, costs nothing extra.
- **Extra guardians are free** (grandparents, carers) as long as **at least one
  parent on that dependant holds a paid package**.

### 24.9 Live presence is paid; free data is delayed
- **Live (real-time) presence** is a paid entitlement.
- **Free data is always delayed by 30 minutes.** A free guardian sees "where
  they were 30 minutes ago," never the live state. The delay is enforced
  server-side on every presence/status/image response for non-paid links.

### 24.10 Audio is paid
Any audio-derived signal (heard conversation gist, audio events surfaced to the
guardian) is a paid entitlement, off by default, separate permission. Matches
the brief's audio-as-separate-permission stance.

### 24.11 No payment rail yet
**No billing integration in V1.** Entitlements are modeled as flags an admin
toggles (`premium`, `live_video`, `live_presence`, `audio`). They behave exactly
as paid features will (gating, delay, throttle) but are granted manually for
now. When billing lands later, it flips the same flags. Nothing else changes.

### 24.12 Entitlement summary (the gate table)

| Capability | Free | Paid flag that unlocks it |
|---|---|---|
| Text / status updates | yes (30-min delayed) | `live_presence` removes delay |
| Images | 1 / hour (30-min delayed) | `live_presence` removes delay; cap stays unless `live_video` |
| Real-time presence | no | `live_presence` |
| Live video / blurred clips | no | `live_video` |
| Audio signals | no | `audio` |
| Daily AI recap | no | `premium` |
| Smart search | no | `premium` |
| Multi-child / multi-parent | yes (free) | n/a |
| Extra guardians | free if one parent paid | n/a |
| Arrival + pickup alerts | yes | n/a (always available) |

Free tier stays genuinely useful (delayed presence + hourly image + arrival and
pickup alerts). The delay and the live-video paywall are the upgrade levers.
