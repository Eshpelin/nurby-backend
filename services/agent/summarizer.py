"""Map-reduce long-window summarizer for the Nurby agent (Learning 4).

``summarize_activity`` does a single DB pass over a window and answers
"what happened today?" well. It does not scale to "summarize the last
week at the front door". A 7-day or 30-day window has far more Journeys,
Events, and Observations than fit in one LLM context.

``summarize_window`` solves that with a classic map-reduce.

  CHUNK. partition [now-hours, now] into [t0, t1] slices. The boundary
         heuristic prefers semantic Incident / Journey edges when they
         cleanly partition the window, else falls back to fixed hourly
         (short windows) or daily (multi-day) slices. See ``_plan_chunks``.

  MAP.   for each chunk build a compact factual rollup
         (``_chunk_facts``. per-Person + per-Rule + per-label + per-camera
         counts over that slice, ACL-filtered) and render a deterministic
         1-2 sentence mini-summary from those facts. THIS STEP COSTS ZERO
         LLM TOKENS. The facts are already curated by the perception
         pipeline (Journeys, Events), so a template renders a faithful
         mini-summary with no model call. That keeps cost bounded and
         predictable. all the per-chunk work is O(rows), no model spend.

  REDUCE. fold the mini-summaries into one narrative with a SINGLE LLM
         step (or a hierarchical batch reduce when there are many chunks).
         The reduce is the ONLY LLM spend and it is budget-gated. Before
         every reduce call we ``check_budget``; if exhausted we stop and
         return the deterministic concatenation with ``partial=True`` and
         a note. After every successful call we ``record_usage`` so the
         per-user daily budget is honored.

Why zero-LLM map. the map step runs once per chunk and a 30-day window
can have 30+ chunks. Putting a model call in the map would multiply cost
by the chunk count and risk the budget before the reduce (the step that
actually produces the user-facing narrative) ever runs. The facts the
map summarizes are already high-quality semantic rows, so a template
loses almost nothing. We reserve the model for the reduce, where folding
N mini-summaries into a coherent story is the part a template cannot do.

This module depends only on ``services.agent.access`` (+ models, llm,
budget). It deliberately does NOT import ``services.agent.tools`` so the
tool wrapper in tools.py can import us without a circular import.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import String as SAString
from sqlalchemy import and_, cast, select

from services.agent.access import accessible_camera_ids
from services.agent.budget import check_budget, estimate_cost, record_usage
from services.agent.llm import llm_call
from shared.models import Camera, Event, Journey, Observation, Person, Provider, Rule

logger = logging.getLogger("nurby.agent.summarizer")


# ── Window + chunking bounds ─────────────────────────────────────────

_MAX_WINDOW_HOURS = 720  # 30 days, matches tools._MAX_WINDOW_HOURS
_MIN_WINDOW_HOURS = 1
_DEFAULT_WINDOW_HOURS = 168  # 7 days

# Heuristic thresholds for the "auto" chunker.
_AUTO_HOURLY_CEILING = 48  # windows <= 48h get hourly-ish chunks
_AUTO_HOURLY_BUCKET = 6  # ...grouped into 6h buckets so a 48h window
#                          is 8 chunks, not 48. Short enough to stay
#                          semantic, coarse enough to bound chunk count.

# Reduce control.
_REDUCE_SINGLE_PASS_MAX = 15  # <= this many mini-summaries -> one call
_REDUCE_BATCH_SIZE = 10  # hierarchical batch size when above the cap
_MAX_CHUNK_BRIEFS = 30  # cap the returned chunk list (note truncation)
_REDUCE_MAX_TOKENS = 800

_KNOWN_LABEL_HINT = (
    "person",
    "cat",
    "dog",
    "car",
    "truck",
    "package",
    "bird",
    "bicycle",
)


# ── Public helpers ───────────────────────────────────────────────────


def _clamp_hours(hours: int | None) -> tuple[int, bool]:
    """Clamp the requested window. Returns (hours, was_clamped)."""
    if hours is None:
        return _DEFAULT_WINDOW_HOURS, False
    try:
        h = int(hours)
    except (TypeError, ValueError):
        raise ValueError("hours must be an integer")
    if h < _MIN_WINDOW_HOURS:
        raise ValueError(f"hours must be >= {_MIN_WINDOW_HOURS}")
    if h > _MAX_WINDOW_HOURS:
        return _MAX_WINDOW_HOURS, True
    return h, False


# ── Chunk planning ───────────────────────────────────────────────────


def _plan_chunks(
    now: datetime, hours: int, chunk_by: str
) -> tuple[list[tuple[datetime, datetime]], str]:
    """Partition [now-hours, now] into ordered [t0, t1] slices.

    Returns (slices, effective_chunk_by). The auto heuristic.

      * window <= 48h          -> 6h buckets (hourly-ish, semantic
                                  enough for "the last two days").
      * window  > 48h          -> calendar-ish day buckets (24h slices),
                                  the natural grain for a day-by-day
                                  weekly / monthly narrative.

    Explicit ``hour`` / ``day`` force the bucket size. ``incident`` and
    ``journey`` use the same fixed daily grid. real Incident / Journey
    rows are read inside ``_chunk_facts`` to ground each slice, so the
    slice boundaries land on whole days that cleanly contain those rows
    rather than splitting a single journey across two chunks. The fixed
    daily grid is the cheap, deterministic partition that keeps
    Incident / Journey rows whole for the common multi-day case.
    """
    window_start = now - timedelta(hours=hours)
    cb = (chunk_by or "auto").lower()

    if cb == "auto":
        bucket_hours = _AUTO_HOURLY_BUCKET if hours <= _AUTO_HOURLY_CEILING else 24
        effective = "hour" if hours <= _AUTO_HOURLY_CEILING else "day"
    elif cb == "hour":
        bucket_hours = _AUTO_HOURLY_BUCKET
        effective = "hour"
    elif cb in {"day", "incident", "journey"}:
        bucket_hours = 24
        effective = cb
    else:
        bucket_hours = 24
        effective = "day"

    slices: list[tuple[datetime, datetime]] = []
    t0 = window_start
    while t0 < now:
        t1 = min(t0 + timedelta(hours=bucket_hours), now)
        slices.append((t0, t1))
        t0 = t1
    if not slices:
        slices = [(window_start, now)]
    return slices, effective


# ── MAP. per-slice factual rollup (zero LLM) ─────────────────────────


async def _chunk_facts(
    ctx: dict, t0: datetime, t1: datetime, allowed: set[uuid.UUID]
) -> dict:
    """Per-slice rollup. persons seen + counts, rules fired + counts,
    top labels, top cameras. Mirrors the summarize_activity per-window
    logic over a [t0, t1] slice. ACL-filtered through ``allowed``.

    Returns a structured facts dict plus the citation ids
    (journey/event/observation) gathered in the slice.
    """
    db = ctx["db"]
    facts: dict[str, Any] = {
        "persons": [],
        "rules": [],
        "labels": [],
        "cameras": [],
        "totals": {
            "observations": 0,
            "persons_seen": 0,
            "rules_fired": 0,
            "unique_labels": 0,
        },
    }
    citations: list[dict] = []
    if not allowed:
        return {"facts": facts, "citations": citations}

    # ── per-Person Journey rollup ───────────────────────────────────
    # Journeys overlapping the slice. last_seen in slice OR started in
    # slice catches a journey that straddles a boundary.
    persons_block: list[dict] = []
    j_rows = (
        await db.execute(
            select(Journey)
            .where(Journey.subject_kind == "person")
            .where(Journey.last_seen_at >= t0)
            .where(Journey.started_at <= t1)
            .order_by(Journey.started_at.asc())
        )
    ).scalars().all()
    by_name: dict[str, dict] = {}
    for j in j_rows:
        cams_seen: list[str] = []
        touches_allowed = False
        for seg in j.segments or []:
            if not isinstance(seg, dict):
                continue
            cid = seg.get("camera_id")
            try:
                cu = uuid.UUID(cid) if cid else None
            except (TypeError, ValueError):
                cu = None
            if cu is not None and cu in allowed:
                touches_allowed = True
                cn = seg.get("camera_name")
                if cn and cn not in cams_seen:
                    cams_seen.append(cn)
        if not touches_allowed:
            continue
        # subject_key is a comma-joined set of names.
        for name in [n.strip() for n in (j.subject_key or "").split(",") if n.strip()]:
            bucket = by_name.setdefault(
                name,
                {
                    "display_name": name,
                    "sighting_count": 0,
                    "cameras": [],
                    "first_seen_at": None,
                    "last_seen_at": None,
                },
            )
            bucket["sighting_count"] += 1
            for cn in cams_seen:
                if cn not in bucket["cameras"]:
                    bucket["cameras"].append(cn)
            s_iso = j.started_at.isoformat() if j.started_at else None
            l_iso = j.last_seen_at.isoformat() if j.last_seen_at else None
            if s_iso and (not bucket["first_seen_at"] or s_iso < bucket["first_seen_at"]):
                bucket["first_seen_at"] = s_iso
            if l_iso and (not bucket["last_seen_at"] or l_iso > bucket["last_seen_at"]):
                bucket["last_seen_at"] = l_iso
        citations.append({"kind": "journey", "id": str(j.id)})
    persons_block = sorted(by_name.values(), key=lambda p: -p["sighting_count"])

    # ── per-Rule Event rollup ───────────────────────────────────────
    ev_rows = (
        await db.execute(
            select(Event, Rule)
            .join(Rule, Rule.id == Event.rule_id, isouter=True)
            .where(Event.fired_at >= t0)
            .where(Event.fired_at <= t1)
        )
    ).all()
    by_rule: dict[uuid.UUID, dict] = {}
    for ev, rule in ev_rows:
        payload = ev.payload or {}
        cam_raw = payload.get("camera_id")
        if cam_raw:
            try:
                if uuid.UUID(str(cam_raw)) not in allowed:
                    continue
            except (TypeError, ValueError):
                pass
        rid = ev.rule_id
        if rid is None:
            continue
        bucket = by_rule.setdefault(
            rid,
            {
                "rule_id": str(rid),
                "rule_name": rule.name if rule else None,
                "firing_count": 0,
            },
        )
        bucket["firing_count"] += 1
        citations.append({"kind": "event", "id": str(ev.id)})
    rules_block = sorted(by_rule.values(), key=lambda r: -r["firing_count"])

    # ── per-label + per-camera Observation rollup ───────────────────
    cam_rows = (
        await db.execute(select(Camera).where(Camera.id.in_(allowed)))
    ).scalars().all()
    cam_name_by_id = {c.id: c.name for c in cam_rows}

    obs_rows = (
        await db.execute(
            select(
                Observation.id,
                Observation.camera_id,
                Observation.started_at,
                Observation.object_detections,
            )
            .where(Observation.started_at >= t0)
            .where(Observation.started_at <= t1)
            .where(Observation.camera_id.in_(allowed))
        )
    ).all()

    labels_block: dict[str, dict] = {}
    cameras_block: dict[str, dict] = {}
    obs_total = 0
    for obs_id, cam_id, ts, det in obs_rows:
        obs_total += 1
        cb = cameras_block.setdefault(
            str(cam_id),
            {
                "camera_id": str(cam_id),
                "camera_name": cam_name_by_id.get(cam_id),
                "observation_count": 0,
            },
        )
        cb["observation_count"] += 1
        items = (
            det
            if isinstance(det, list)
            else (det or {}).get("objects")
            if isinstance(det, dict)
            else None
        )
        if isinstance(items, list):
            seen_in_obs: set[str] = set()
            for obj in items:
                if not isinstance(obj, dict):
                    continue
                lab = obj.get("label")
                if not lab or lab in seen_in_obs:
                    continue
                seen_in_obs.add(lab)
                lb = labels_block.setdefault(
                    lab, {"label": lab, "observation_count": 0}
                )
                lb["observation_count"] += 1

    facts["persons"] = persons_block
    facts["rules"] = rules_block
    facts["labels"] = sorted(
        labels_block.values(), key=lambda x: -x["observation_count"]
    )
    facts["cameras"] = sorted(
        cameras_block.values(), key=lambda x: -x["observation_count"]
    )
    facts["totals"] = {
        "observations": obs_total,
        "persons_seen": len(persons_block),
        "rules_fired": sum(r["firing_count"] for r in rules_block),
        "unique_labels": len(labels_block),
    }
    return {"facts": facts, "citations": citations}


def _mini_summary(t0: datetime, t1: datetime, facts: dict) -> str:
    """Deterministic 1-2 sentence template render of a chunk's facts.

    ZERO LLM. The facts are already curated semantic rows so a template
    is a faithful mini-summary. This is the entire MAP step output.
    """
    totals = facts.get("totals", {})
    obs_n = totals.get("observations", 0)
    if obs_n == 0 and not facts.get("rules") and not facts.get("persons"):
        return "Quiet. no notable activity in this window."

    bits: list[str] = []
    persons = facts.get("persons") or []
    if persons:
        top = persons[:3]
        names = ", ".join(
            f"{p['display_name']} ({p['sighting_count']}x)" for p in top
        )
        bits.append(f"People. {names}")
    rules = facts.get("rules") or []
    if rules:
        rnames = ", ".join(
            f"{r.get('rule_name') or 'rule'} fired {r['firing_count']}x"
            for r in rules[:3]
        )
        bits.append(f"Rules. {rnames}")
    labels = facts.get("labels") or []
    notable = [
        l for l in labels if l["label"] in _KNOWN_LABEL_HINT and l["label"] != "person"
    ][:3]
    if notable:
        lnames = ", ".join(
            f"{l['label']} x{l['observation_count']}" for l in notable
        )
        bits.append(f"Detections. {lnames}")
    cameras = facts.get("cameras") or []
    if cameras:
        top_cam = cameras[0]
        bits.append(
            f"Busiest camera. {top_cam.get('camera_name') or 'unknown'} "
            f"({top_cam['observation_count']} obs)"
        )

    head = f"{obs_n} observations"
    body = ". ".join(bits) if bits else "activity recorded"
    return f"{head}. {body}."


# ── Provider resolution (daily_digest precedent) ─────────────────────


async def _resolve_provider(db: Any, provider_id: str | None) -> Provider | None:
    """Resolve a text-capable Provider. Optional explicit ``provider_id``,
    else the system active provider. Mirrors daily_digest._resolve_provider
    + tools' optional-provider-id convention. Returns None when nothing
    resolves (the caller then returns the deterministic concatenation)."""
    if provider_id:
        try:
            p = await db.get(Provider, uuid.UUID(str(provider_id)))
            if p is not None:
                return p
        except Exception:
            logger.debug("provider_id lookup failed", exc_info=True)
    # Fall back to the first active provider.
    try:
        row = (
            await db.execute(
                select(Provider).where(Provider.active == True).limit(1)  # noqa: E712
            )
        ).scalar_one_or_none()
        return row
    except Exception:
        logger.debug("active provider lookup failed", exc_info=True)
        return None


# ── REDUCE. fold mini-summaries into a narrative (the only LLM spend) ─


_REDUCE_SYSTEM_PROMPT = (
    "You are a household security camera analyst. You receive a list of"
    " per-chunk mini-summaries covering a long time window, in"
    " chronological order. Fold them into ONE coherent narrative. Be"
    " concrete with names, counts, and the day/time of notable events."
    " Call out patterns and anything unusual across chunks. If a focus"
    " topic is given, weight the narrative toward it. Never invent"
    " detail beyond the mini-summaries. If everything was quiet, say so"
    " briefly."
)


def _reduce_user_prompt(
    blocks: list[str], hours: int, focus: str | None
) -> str:
    lines = [f"Window. last {hours} hours, {len(blocks)} chunks."]
    if focus:
        lines.append(f"Focus. weight the narrative toward. {focus}.")
    lines.append("")
    for i, b in enumerate(blocks):
        lines.append(f"[chunk {i + 1}] {b}")
    lines.append("")
    lines.append(
        "Write the combined narrative now. Plain prose, no preamble."
    )
    return "\n".join(lines)


async def _reduce_once(
    ctx: dict,
    provider: Provider,
    model: str,
    blocks: list[str],
    hours: int,
    focus: str | None,
) -> tuple[str | None, int, int, int, bool]:
    """One budget-gated LLM reduce call over ``blocks``.

    Returns (text, tokens_in, tokens_out, cost_cents, budget_ok).
    ``budget_ok`` is False when check_budget blocked the call (no spend,
    no text). The caller stops and returns a partial result.
    """
    user = ctx["user"]
    db = ctx["db"]

    status = await check_budget(user.id, db)
    if not status.ok:
        return None, 0, 0, 0, False

    system = _REDUCE_SYSTEM_PROMPT
    user_prompt = _reduce_user_prompt(blocks, hours, focus)
    try:
        resp = await llm_call(
            provider=provider,
            model=model,
            system_prompt=system,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[],
            max_tokens=_REDUCE_MAX_TOKENS,
            stream=False,
        )
    except Exception:
        logger.exception("reduce llm_call failed")
        return None, 0, 0, 0, True

    cost = estimate_cost(provider.kind, model, resp.tokens_in, resp.tokens_out)
    try:
        await record_usage(user.id, resp.tokens_in, resp.tokens_out, cost, db)
    except Exception:
        logger.debug("record_usage failed", exc_info=True)
    text = (resp.text or "").strip() or None
    return text, resp.tokens_in, resp.tokens_out, cost, True


# ── Public entry point ───────────────────────────────────────────────


async def summarize_window(
    ctx: dict,
    *,
    hours: int = _DEFAULT_WINDOW_HOURS,
    focus: str | None = None,
    chunk_by: str = "auto",
    provider_id: str | None = None,
) -> dict:
    """Map-reduce summary of a LONG window.

    See the module docstring for the design. Chunk -> deterministic
    per-chunk mini-summary (zero LLM) -> budget-gated LLM reduce.

    Response shape.
        {
          "hours", "chunk_by", "chunk_count",
          "chunks": [{"from", "to", "mini_summary", "facts_brief"}],
          "summary": "<final narrative>",
          "tokens_used", "cost_cents", "partial": bool,
          "citations": [...],
          "note": "<optional>",
        }
    """
    user = ctx["user"]
    db = ctx["db"]

    hours, clamped = _clamp_hours(hours)
    notes: list[str] = []
    if clamped:
        notes.append(f"window clamped to {_MAX_WINDOW_HOURS}h (30 days)")

    now = datetime.now(timezone.utc)
    allowed = await accessible_camera_ids(user, db)

    slices, effective_chunk_by = _plan_chunks(now, hours, chunk_by)

    # ── MAP. deterministic per-chunk rollup + mini-summary, no LLM ──
    chunk_records: list[dict] = []
    all_citations: list[dict] = []
    seen_cites: set[tuple[str, str]] = set()
    for (t0, t1) in slices:
        rolled = await _chunk_facts(ctx, t0, t1, allowed)
        facts = rolled["facts"]
        for c in rolled["citations"]:
            key = (c["kind"], c["id"])
            if key not in seen_cites:
                seen_cites.add(key)
                all_citations.append(c)
        mini = _mini_summary(t0, t1, facts)
        chunk_records.append(
            {
                "from": t0.isoformat(),
                "to": t1.isoformat(),
                "mini_summary": mini,
                "facts_brief": {
                    "observations": facts["totals"]["observations"],
                    "persons_seen": facts["totals"]["persons_seen"],
                    "rules_fired": facts["totals"]["rules_fired"],
                    "top_persons": [
                        p["display_name"] for p in (facts["persons"] or [])[:3]
                    ],
                    "top_labels": [
                        l["label"] for l in (facts["labels"] or [])[:3]
                    ],
                },
            }
        )

    mini_blocks = [c["mini_summary"] for c in chunk_records]

    # ── REDUCE. the only LLM spend, budget-gated ────────────────────
    provider = await _resolve_provider(db, provider_id)
    tokens_used = 0
    cost_cents = 0
    partial = False
    summary: str

    if provider is None:
        # No LLM available. return the deterministic concatenation. still
        # useful as a day-by-day skeleton.
        notes.append("no LLM provider available; returning chunk summaries unsynthesized")
        summary = _deterministic_concat(chunk_records)
        partial = True
    else:
        model = provider.default_model or "gpt-4o-mini"
        summary, tokens_used, cost_cents, partial, reduce_note = await _run_reduce(
            ctx, provider, model, mini_blocks, hours, focus, chunk_records
        )
        if reduce_note:
            notes.append(reduce_note)

    # Cap the returned chunk briefs.
    returned_chunks = chunk_records
    if len(chunk_records) > _MAX_CHUNK_BRIEFS:
        returned_chunks = chunk_records[:_MAX_CHUNK_BRIEFS]
        notes.append(
            f"chunk list truncated to {_MAX_CHUNK_BRIEFS} of {len(chunk_records)}"
        )

    out: dict = {
        "hours": hours,
        "chunk_by": effective_chunk_by,
        "chunk_count": len(chunk_records),
        "chunks": returned_chunks,
        "summary": summary,
        "tokens_used": tokens_used,
        "cost_cents": cost_cents,
        "partial": partial,
        "citations": all_citations,
    }
    if notes:
        out["note"] = "; ".join(notes)
    return out


def _deterministic_concat(chunk_records: list[dict]) -> str:
    """Fallback narrative. join the mini-summaries chronologically."""
    if not chunk_records:
        return "No activity in the requested window."
    lines = []
    for c in chunk_records:
        lines.append(f"{c['from']} to {c['to']}. {c['mini_summary']}")
    return "\n".join(lines)


async def _run_reduce(
    ctx: dict,
    provider: Provider,
    model: str,
    mini_blocks: list[str],
    hours: int,
    focus: str | None,
    chunk_records: list[dict],
) -> tuple[str, int, int, bool, str | None]:
    """Drive the reduce. single pass for few chunks, hierarchical batch
    reduce for many. Each LLM call is budget-gated; the first time the
    budget blocks we stop and return the deterministic concat as a
    partial. Returns (summary, tokens, cost, partial, note)."""
    tokens_used = 0
    cost_cents = 0

    if len(mini_blocks) <= _REDUCE_SINGLE_PASS_MAX:
        text, ti, to, cost, ok = await _reduce_once(
            ctx, provider, model, mini_blocks, hours, focus
        )
        if not ok:
            return (
                _deterministic_concat(chunk_records),
                tokens_used,
                cost_cents,
                True,
                "ran out of budget before reduce; returning unsynthesized chunk summaries",
            )
        tokens_used += ti + to
        cost_cents += cost
        if text is None:
            return (
                _deterministic_concat(chunk_records),
                tokens_used,
                cost_cents,
                True,
                "reduce produced no text; returning unsynthesized chunk summaries",
            )
        return text, tokens_used, cost_cents, False, None

    # Hierarchical. summarize batches, then summarize the batch summaries.
    batch_summaries: list[str] = []
    for i in range(0, len(mini_blocks), _REDUCE_BATCH_SIZE):
        batch = mini_blocks[i : i + _REDUCE_BATCH_SIZE]
        text, ti, to, cost, ok = await _reduce_once(
            ctx, provider, model, batch, hours, focus
        )
        if not ok:
            # Budget hit mid-way. fold what we have so far.
            remaining = batch_summaries + mini_blocks[i:]
            return (
                _deterministic_concat(chunk_records),
                tokens_used,
                cost_cents,
                True,
                "ran out of budget mid-reduce; returning unsynthesized chunk summaries",
            )
        tokens_used += ti + to
        cost_cents += cost
        batch_summaries.append(text or "(empty batch)")

    # Final fold over the batch summaries.
    text, ti, to, cost, ok = await _reduce_once(
        ctx, provider, model, batch_summaries, hours, focus
    )
    if not ok:
        return (
            "\n".join(batch_summaries),
            tokens_used,
            cost_cents,
            True,
            "ran out of budget before final fold; returning batch summaries",
        )
    tokens_used += ti + to
    cost_cents += cost
    if text is None:
        return (
            "\n".join(batch_summaries),
            tokens_used,
            cost_cents,
            True,
            "final fold produced no text; returning batch summaries",
        )
    return text, tokens_used, cost_cents, False, None


__all__ = ["summarize_window"]
