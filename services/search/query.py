"""
Search query engine.

Combines structured filters with semantic similarity search
over observation descriptions and VLM-generated content.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, and_, or_, func, cast, String, Float, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Conversation, Observation, Camera, FaceCluster, Person, Summary, Transcript
from services.search.embeddings import generate_embedding, get_embedding_provider, EMBEDDING_DIM

logger = logging.getLogger("nurby.search.query")


async def _embed_query(text: str) -> list[float] | None:
    """Try to generate an embedding for a search query.

    Returns None if embedding generation fails or no provider is available.
    """
    try:
        provider = await get_embedding_provider()
        embedding = await generate_embedding(text, provider)
        # Check that we got a real embedding, not all zeros
        if any(v != 0.0 for v in embedding):
            return embedding
    except Exception:
        logger.debug("Embedding generation failed for query, falling back to ILIKE")
    return None


def _build_observation_dict(obs, camera_map: dict) -> dict:
    """Convert an Observation ORM object to a response dict."""
    result = {
        "id": str(obs.id),
        "camera_id": str(obs.camera_id),
        "camera_name": camera_map.get(obs.camera_id, "Unknown"),
        "started_at": obs.started_at.isoformat(),
        "object_detections": obs.object_detections,
        "person_detections": obs.person_detections,
        "vlm_description": obs.vlm_description,
        "confidence": obs.confidence,
        "thumbnail_path": obs.thumbnail_path,
    }
    return result


async def _resolve_camera_names(db: AsyncSession, camera_ids: set) -> dict:
    """Fetch camera display names for a set of camera IDs."""
    if not camera_ids:
        return {}
    cam_result = await db.execute(
        select(Camera).where(Camera.id.in_(camera_ids))
    )
    return {c.id: c.name for c in cam_result.scalars().all()}


async def search_observations(
    db: AsyncSession,
    query: str | None = None,
    camera_id: uuid.UUID | None = None,
    person_name: str | None = None,
    object_label: str | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    """Search observations with structured filters and vector similarity.

    When a text query is provided, generates an embedding and uses pgvector
    cosine distance to find semantically similar observations. Falls back
    to ILIKE keyword matching if embedding generation fails.
    """
    filters = []

    # Camera filter
    if camera_id:
        filters.append(Observation.camera_id == camera_id)

    # Time range
    if time_from:
        filters.append(Observation.started_at >= time_from)
    if time_to:
        filters.append(Observation.started_at <= time_to)

    # Object label filter (search inside JSON)
    if object_label:
        filters.append(
            cast(Observation.object_detections, String).ilike(f"%{object_label}%")
        )

    # Person name filter (search inside JSON). Detections store the
    # canonical display_name, so reverse-map a typed household nickname
    # ("mommy") to canonical names before matching. Falls back to the
    # literal text when nothing resolves.
    if person_name:
        from shared.person_alias import resolve_name_to_canonical

        canon = await resolve_name_to_canonical(db, person_name)
        needles = canon or [person_name]
        filters.append(
            or_(
                *[
                    cast(Observation.person_detections, String).ilike(f"%{n}%")
                    for n in needles
                ]
            )
        )

    # ── Extract keywords for targeted label/name matching ──
    query_lower = (query or "").lower().strip()
    query_words = set(re.findall(r"\w+", query_lower)) if query_lower else set()

    # Common synonyms and related terms for better keyword coverage
    SYNONYMS: dict[str, list[str]] = {
        "car": ["car", "vehicle", "automobile"],
        "vehicle": ["car", "truck", "bus", "motorcycle", "van", "vehicle"],
        "truck": ["truck", "vehicle"],
        "person": ["person", "people", "someone", "human", "man", "woman"],
        "people": ["person", "people"],
        "dog": ["dog", "puppy", "canine"],
        "cat": ["cat", "kitten", "feline"],
        "bike": ["bicycle", "bike", "cycling"],
        "bicycle": ["bicycle", "bike"],
        "package": ["package", "parcel", "delivery", "box"],
        "delivery": ["delivery", "package", "courier"],
        "mail": ["mail", "letter", "mailbox", "postal"],
    }

    # Expand query words with synonyms
    expanded_words = set(query_words)
    for word in query_words:
        if word in SYNONYMS:
            expanded_words.update(SYNONYMS[word])

    use_vector = False
    query_embedding = None

    if query:
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            use_vector = True

    observations: list = []

    # ── Strategy 1. Vector similarity (with relevance threshold) ──
    if use_vector and query_embedding is not None:
        vector_filters = list(filters) + [Observation.description_embedding.isnot(None)]

        cosine_distance = Observation.description_embedding.cosine_distance(query_embedding)

        # Fetch more than needed so we can filter by threshold
        stmt = (
            select(Observation, cosine_distance.label("distance"))
            .where(and_(*vector_filters) if vector_filters else True)
            .order_by(cosine_distance.asc())
            .limit(limit * 2)
            .offset(offset)
        )

        result = await db.execute(stmt)
        rows = result.all()

        # Filter out poor matches (cosine distance > 0.85 means very low similarity)
        MAX_DISTANCE = 0.85
        observations = [row[0] for row in rows if row[1] <= MAX_DISTANCE][:limit]

    # ── Strategy 2. Direct label + name matching (always runs alongside) ──
    label_results: list = []
    if query_lower and expanded_words:
        label_conditions = []
        for word in expanded_words:
            # Object labels are stored as "label": "cat" in JSON, match exact label
            label_conditions.append(
                cast(Observation.object_detections, String).ilike(f"%\"{word}\"%")
            )
            # Person names can be partial
            label_conditions.append(
                cast(Observation.person_detections, String).ilike(f"%{word}%")
            )
            # VLM descriptions use word boundary matching (space/punctuation bounded)
            # Match "cat" but not "scattered" by requiring word boundary context
            label_conditions.append(
                Observation.vlm_description.op("~*")(f"\\m{re.escape(word)}\\M")
            )

        label_filters = list(filters) + [or_(*label_conditions)]

        stmt = (
            select(Observation)
            .where(and_(*label_filters))
            .order_by(Observation.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        label_results = result.scalars().all()

    # ── Merge and deduplicate results ──
    # Label/keyword matches are higher confidence than hash-based vectors,
    # so they go first. Vector results fill in after.
    if observations or label_results:
        seen_ids = set()
        merged = []

        # Label matches first (direct keyword hits, most relevant)
        for obs in label_results:
            if obs.id not in seen_ids:
                seen_ids.add(obs.id)
                merged.append(obs)

        # Then vector results (ranked by embedding similarity)
        for obs in observations:
            if obs.id not in seen_ids:
                seen_ids.add(obs.id)
                merged.append(obs)

        observations = merged[:limit]

    # ── Strategy 3. Broad regex fallback (only if nothing found yet) ──
    if not observations and query:
        # Use PostgreSQL regex with word boundaries for description
        # Use ILIKE for JSON fields since labels are quoted strings
        text_filter = or_(
            Observation.vlm_description.op("~*")(f"\\m{re.escape(query_lower)}\\M"),
            cast(Observation.object_detections, String).ilike(f"%\"{query_lower}\"%"),
            cast(Observation.person_detections, String).ilike(f"%{query}%"),
        )
        fallback_filters = list(filters) + [text_filter]

        stmt = (
            select(Observation)
            .where(and_(*fallback_filters) if fallback_filters else True)
            .order_by(Observation.started_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(stmt)
        observations = result.scalars().all()

    # No query at all, just return recent with applied filters
    if not observations and not query:
        stmt = (
            select(Observation)
            .where(and_(*filters) if filters else True)
            .order_by(Observation.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        observations = result.scalars().all()

    # Build response with camera names
    camera_ids_set = {obs.camera_id for obs in observations}
    camera_map = await _resolve_camera_names(db, camera_ids_set)

    return [_build_observation_dict(obs, camera_map) for obs in observations]


PEOPLE_INTENT_WORDS = {
    "who", "whom", "anyone", "someone", "somebody", "people",
    "person", "visitor", "visitors", "guest", "guests",
    "came", "come", "arrived", "visited", "seen", "spotted",
    "intruder", "intruders", "stranger", "strangers",
}


def _is_people_intent(question: str) -> bool:
    words = set(re.findall(r"\w+", (question or "").lower()))
    return bool(words & PEOPLE_INTENT_WORDS)


async def _recent_people_observations(db: AsyncSession, hours: int = 24, limit: int = 30) -> list:
    """Fetch recent observations that involved a person or a face.

    Used as a fallback when the question asks 'who came' etc. but
    keyword/vector search misses because VLM captions rarely say
    'person arrived'.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(Observation)
        .where(Observation.started_at >= cutoff)
        .where(
            or_(
                cast(Observation.object_detections, String).ilike("%\"person\"%"),
                cast(Observation.person_detections, String).ilike("%cluster_id%"),
            )
        )
        .order_by(Observation.started_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def answer_question(
    db: AsyncSession,
    question: str,
    provider=None,
) -> dict:
    """Answer a natural language question using observation context.

    Fetches relevant observations, builds context, sends to VLM.
    """
    import httpx

    # Search for relevant observations
    results = await search_observations(db, query=question, limit=20)

    # People-intent fallback. Questions like "who came" rarely match VLM
    # captions or object labels through vector/keyword search because the
    # caption says things like "A man is raising his arm". Pull any recent
    # observation that involved a person or a clustered face.
    if _is_people_intent(question):
        recent = await _recent_people_observations(db, hours=24, limit=30)
        if recent:
            camera_ids_set = {obs.camera_id for obs in recent}
            camera_map = await _resolve_camera_names(db, camera_ids_set)
            recent_dicts = [_build_observation_dict(o, camera_map) for o in recent]
            seen = {r["id"] for r in results}
            for r in recent_dicts:
                if r["id"] not in seen:
                    results.append(r)

    if not results:
        return {
            "answer": "No matching observations found. Try a different query or check that cameras are recording.",
            "sources": [],
        }

    # Resolve cluster_id -> named-person name so answers use real names
    # for historic observations captured before a cluster was named.
    cluster_ids_seen: set[str] = set()
    for obs in results:
        for f in (obs.get("person_detections") or {}).get("faces", []) or []:
            cid = f.get("cluster_id")
            if cid:
                cluster_ids_seen.add(str(cid))
    # Household nicknames replace canonical names in the answer. Live
    # detections carry the canonical name, so build a name->nickname map.
    alias_rows = (
        await db.execute(select(Person.display_name, Person.nickname))
    ).all()
    name_alias = {
        dn: nk.strip()
        for dn, nk in alias_rows
        if dn and isinstance(nk, str) and nk.strip()
    }

    cluster_name_map: dict[str, str] = {}
    if cluster_ids_seen:
        try:
            rows = await db.execute(
                select(FaceCluster.id, Person.display_name, Person.nickname)
                .join(Person, FaceCluster.person_id == Person.id)
                .where(FaceCluster.id.in_([uuid.UUID(c) for c in cluster_ids_seen]))
            )
            for cid, name, nick in rows.all():
                cluster_name_map[str(cid)] = (
                    nick.strip() if isinstance(nick, str) and nick.strip() else name
                )
        except Exception:
            logger.exception("Failed to resolve cluster -> person names for answer")

    if not provider:
        # Prefer the active VLM/text provider. Embedding providers are
        # not chat models and cannot synthesize answers.
        try:
            from services.perception.vlm import get_active_provider as _get_vlm
            provider = await _get_vlm()
        except Exception:
            provider = None
    if not provider:
        from services.search.embeddings import get_embedding_provider
        provider = await get_embedding_provider()

    if not provider:
        # No VLM. return search results without synthesized answer
        return {
            "answer": None,
            "sources": results,
            "note": "No VLM provider configured. Showing matching observations.",
        }

    def _pretty_ts(iso: str | None) -> str:
        """Human-readable timestamp. Avoids the model echoing raw ISO
        strings like 2026-04-22T19:31:30.609929+00:00 in answers."""
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            # Local time reads naturally for a single-home user.
            local = dt.astimezone()
            return local.strftime("%b %d, %-I:%M %p").lower().replace(" 0", " ")
        except Exception:
            return iso

    # Build context from observations
    context_parts = []
    for i, obs in enumerate(results[:10]):
        parts = [f"[{i+1}] {_pretty_ts(obs['started_at'])} on {obs['camera_name']}"]
        if obs.get("vlm_description"):
            parts.append(f"  Description: {obs['vlm_description']}")
        if obs.get("object_detections"):
            objects = obs["object_detections"].get("objects", [])
            if objects:
                labels = [o["label"] for o in objects]
                parts.append(f"  Objects: {', '.join(labels)}")
        if obs.get("person_detections"):
            faces = obs["person_detections"].get("faces", [])
            resolved: list[str] = []
            unknown_ct = 0
            for f in faces:
                name = f.get("person_name")
                if name:
                    name = name_alias.get(name, name)
                else:
                    cid = f.get("cluster_id")
                    if cid:
                        name = cluster_name_map.get(str(cid))
                if name:
                    resolved.append(name)
                else:
                    unknown_ct += 1
            who_parts = []
            if resolved:
                who_parts.append(", ".join(sorted(set(resolved))))
            if unknown_ct:
                who_parts.append(
                    "1 unknown person" if unknown_ct == 1
                    else f"{unknown_ct} unknown people"
                )
            if who_parts:
                parts.append(f"  People. {' and '.join(who_parts)}")
        context_parts.append("\n".join(parts))

    context = "\n\n".join(context_parts)

    # Intent flags. only surface timestamps / cameras when the question
    # actually asks about them. Otherwise answer the question directly.
    q_lower = (question or "").lower()
    wants_time = any(w in q_lower for w in (
        "when", "what time", "how long", "how often", "how many times",
        "last time", "first time", "recent", "today", "yesterday",
        "tonight", "morning", "evening", "night",
    ))
    wants_where = any(w in q_lower for w in (
        "where", "which camera", "what camera", "room", "location",
    ))
    wants_count = any(w in q_lower for w in (
        "how many", "count", "number of",
    ))
    wants_list = _is_people_intent(question) and any(w in q_lower for w in (
        "who", "list", "everyone", "all ",
    ))

    directives = [
        "Answer the question directly in 1 to 3 short sentences.",
        "Synthesize across observations. Summarize behaviour, not each frame.",
        "Do not list observations one by one. Do not number them. Do not use bullet points unless the user asked for a list.",
    ]
    if wants_time:
        directives.append("Include the relevant timestamp in natural language, like 'around 7:30 pm'.")
    else:
        directives.append("Do not mention timestamps unless the user asked when something happened.")
    if wants_where:
        directives.append("Mention the camera or location.")
    else:
        directives.append("Do not mention camera names unless the user asked where.")
    if wants_count:
        directives.append("Give a specific count.")
    if wants_list:
        directives.append("List each distinct person by name, once.")

    directives.extend([
        "Use the person's real name when one is given. Unnamed faces are 'an unknown person'.",
        "Do not output ISO strings, seconds, or microseconds.",
        "If the observations do not actually answer the question, say so briefly.",
    ])

    system_prompt = (
        "You are Nurby, a home camera assistant. You receive a log of recent "
        "observations and answer the user's question about what is happening. "
        "Write like a person giving a quick update, not like a database dump.\n\n"
        "Rules.\n- " + "\n- ".join(directives)
    )

    user_prompt = (
        f"Observations (most recent first):\n{context}\n\n"
        f"Question. {question}\n\n"
        f"Write the answer now. Keep it short and direct."
    )

    try:
        answer_text = await _call_text_llm(provider, system_prompt, user_prompt)
        return {
            "answer": answer_text,
            "sources": results[:10],
        }
    except Exception:
        logger.exception("VLM question answering failed")
        return {
            "answer": None,
            "sources": results,
            "note": "VLM call failed. Showing matching observations.",
        }


async def _call_text_llm(provider, system_prompt: str, user_prompt: str) -> str:
    """Call LLM for text-only question answering."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        if provider.kind == "openai":
            model = provider.default_model or "gpt-4o-mini"
            resp = await client.post(
                f"{provider.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json={
                    "model": model,
                    "max_tokens": 500,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        elif provider.kind == "anthropic":
            model = provider.default_model or "claude-sonnet-4-20250514"
            resp = await client.post(
                f"{provider.base_url}/v1/messages",
                headers={
                    "x-api-key": provider.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]

        elif provider.kind == "google":
            model = provider.default_model or "gemini-2.0-flash"
            resp = await client.post(
                f"{provider.base_url}/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": provider.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "generationConfig": {"maxOutputTokens": 500},
                },
            )
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

        elif provider.kind == "ollama":
            model = provider.default_model or "llama3"
            resp = await client.post(
                f"{provider.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{user_prompt}",
                    "stream": False,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")

        else:
            raise ValueError(f"Unknown provider kind: {provider.kind}")


# ---- transcript search (Phase 2) -----------------------------------------


async def search_transcripts(
    db: AsyncSession,
    query: str | None = None,
    camera_id: uuid.UUID | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    limit: int = 30,
) -> list[dict]:
    """Search transcripts by ILIKE + cosine similarity on embeddings.

    Mirrors :func:`search_observations` shape so the API layer can
    return a unified result envelope tagged with ``kind``. Filtered
    transcripts are excluded.
    """
    base_filters = [Transcript.filtered.is_(False)]
    if camera_id:
        base_filters.append(Transcript.camera_id == camera_id)
    if time_from:
        base_filters.append(Transcript.started_at >= time_from)
    if time_to:
        base_filters.append(Transcript.started_at <= time_to)

    rows: list[Transcript] = []
    distances: dict[uuid.UUID, float | None] = {}

    if query:
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            vector_filters = list(base_filters) + [Transcript.embedding.isnot(None)]
            cosine_distance = Transcript.embedding.cosine_distance(query_embedding)
            stmt = (
                select(Transcript, cosine_distance.label("distance"))
                .where(and_(*vector_filters))
                .order_by(cosine_distance.asc())
                .limit(limit)
            )
            result = await db.execute(stmt)
            for tx, dist in result.all():
                rows.append(tx)
                distances[tx.id] = float(dist) if dist is not None else None
        if not rows:
            text_filters = list(base_filters) + [Transcript.text.ilike(f"%{query}%")]
            stmt = (
                select(Transcript)
                .where(and_(*text_filters))
                .order_by(Transcript.started_at.desc())
                .limit(limit)
            )
            rows = (await db.execute(stmt)).scalars().all()
    else:
        stmt = (
            select(Transcript)
            .where(and_(*base_filters))
            .order_by(Transcript.started_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()

    camera_names = await _resolve_camera_names(db, {r.camera_id for r in rows})
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "kind": "transcript",
                "id": str(r.id),
                "camera_id": str(r.camera_id),
                "camera_name": camera_names.get(r.camera_id, "Unknown"),
                "started_at": r.started_at.isoformat(),
                "ended_at": r.ended_at.isoformat(),
                "text": r.text,
                "language": r.language,
                "provider": r.provider,
                "speaker_person_id": str(r.speaker_person_id)
                if r.speaker_person_id
                else None,
                "speaker_source": r.speaker_source,
                "audio_capture_id": str(r.audio_capture_id)
                if r.audio_capture_id
                else None,
                "distance": distances.get(r.id),
            }
        )
    return out


# ---- summary search ------------------------------------------------------


async def search_summaries(
    db: AsyncSession,
    query: str | None = None,
    camera_id: uuid.UUID | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    limit: int = 30,
) -> list[dict]:
    """Search Summary rows. Mirrors the transcript path."""
    base_filters = []
    if camera_id:
        base_filters.append(Summary.camera_id == camera_id)
    if time_from:
        base_filters.append(Summary.started_at >= time_from)
    if time_to:
        base_filters.append(Summary.started_at <= time_to)

    rows: list[Summary] = []
    distances: dict[uuid.UUID, float | None] = {}

    if query:
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            stmt = (
                select(Summary, Summary.embedding.cosine_distance(query_embedding).label("distance"))
                .where(and_(*(base_filters + [Summary.embedding.isnot(None)])))
                .order_by("distance")
                .limit(limit)
            )
            result = await db.execute(stmt)
            for s, dist in result.all():
                rows.append(s)
                distances[s.id] = float(dist) if dist is not None else None
        if not rows:
            stmt = (
                select(Summary)
                .where(and_(*(base_filters + [Summary.summary_text.ilike(f"%{query}%")])))
                .order_by(Summary.started_at.desc())
                .limit(limit)
            )
            rows = (await db.execute(stmt)).scalars().all()
    else:
        stmt = (
            select(Summary)
            .where(and_(*base_filters)) if base_filters else select(Summary)
        )
        stmt = stmt.order_by(Summary.started_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()

    camera_names = await _resolve_camera_names(db, {r.camera_id for r in rows})
    return [
        {
            "kind": "summary",
            "id": str(r.id),
            "camera_id": str(r.camera_id),
            "camera_name": camera_names.get(r.camera_id, "Unknown"),
            "started_at": r.started_at.isoformat(),
            "ended_at": r.ended_at.isoformat(),
            "summary_kind": r.kind,
            "summary_text": r.summary_text,
            "provider_name": r.provider_name,
            "people_seen": r.people_seen,
            "plates_seen": r.plates_seen,
            "distance": distances.get(r.id),
        }
        for r in rows
    ]


# ---- conversation search -------------------------------------------------


async def search_conversations(
    db: AsyncSession,
    query: str | None = None,
    camera_id: uuid.UUID | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    limit: int = 30,
) -> list[dict]:
    """Search finalized Conversation rows by their summary embedding +
    text. Open conversations are excluded since they have no summary
    yet."""
    base_filters = [Conversation.finalized.is_(True)]
    if camera_id:
        base_filters.append(Conversation.camera_id == camera_id)
    if time_from:
        base_filters.append(Conversation.started_at >= time_from)
    if time_to:
        base_filters.append(Conversation.started_at <= time_to)

    rows: list[Conversation] = []
    distances: dict[uuid.UUID, float | None] = {}

    if query:
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            stmt = (
                select(Conversation, Conversation.embedding.cosine_distance(query_embedding).label("distance"))
                .where(and_(*(base_filters + [Conversation.embedding.isnot(None)])))
                .order_by("distance")
                .limit(limit)
            )
            result = await db.execute(stmt)
            for c, dist in result.all():
                rows.append(c)
                distances[c.id] = float(dist) if dist is not None else None
        if not rows:
            stmt = (
                select(Conversation)
                .where(and_(*(base_filters + [Conversation.summary_text.ilike(f"%{query}%")])))
                .order_by(Conversation.started_at.desc())
                .limit(limit)
            )
            rows = (await db.execute(stmt)).scalars().all()
    else:
        stmt = (
            select(Conversation)
            .where(and_(*base_filters))
            .order_by(Conversation.started_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()

    camera_names = await _resolve_camera_names(db, {r.camera_id for r in rows})
    return [
        {
            "kind": "conversation",
            "id": str(r.id),
            "camera_id": str(r.camera_id),
            "camera_name": camera_names.get(r.camera_id, "Unknown"),
            "started_at": r.started_at.isoformat(),
            "ended_at": (r.ended_at or r.ended_at_provisional).isoformat(),
            "summary_text": r.summary_text,
            "transcript_count": r.transcript_count,
            "summary_provider_name": r.summary_provider_name,
            "distance": distances.get(r.id),
        }
        for r in rows
    ]
