"""
Search query engine.

Combines structured filters with semantic similarity search
over observation descriptions and VLM-generated content.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, and_, or_, func, cast, String, Float, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Observation, Camera, Person
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

    # Person name filter (search inside JSON)
    if person_name:
        filters.append(
            cast(Observation.person_detections, String).ilike(f"%{person_name}%")
        )

    use_vector = False
    query_embedding = None

    if query:
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            use_vector = True

    if use_vector and query_embedding is not None:
        # Vector similarity search. Order by cosine distance (ascending).
        # Only consider observations that have an embedding.
        filters.append(Observation.description_embedding.isnot(None))

        cosine_distance = Observation.description_embedding.cosine_distance(query_embedding)

        stmt = (
            select(Observation, cosine_distance.label("distance"))
            .where(and_(*filters) if filters else True)
            .order_by(cosine_distance.asc())
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(stmt)
        rows = result.all()
        observations = [row[0] for row in rows]
    else:
        # ILIKE fallback when no embedding is available
        if query:
            text_filter = or_(
                Observation.vlm_description.ilike(f"%{query}%"),
                cast(Observation.object_detections, String).ilike(f"%{query}%"),
                cast(Observation.person_detections, String).ilike(f"%{query}%"),
            )
            filters.append(text_filter)

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

    if not results:
        return {
            "answer": "No matching observations found. Try a different query or check that cameras are recording.",
            "sources": [],
        }

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

    # Build context from observations
    context_parts = []
    for i, obs in enumerate(results[:10]):
        parts = [f"[{i+1}] {obs['started_at']} on {obs['camera_name']}"]
        if obs.get("vlm_description"):
            parts.append(f"  Description: {obs['vlm_description']}")
        if obs.get("object_detections"):
            objects = obs["object_detections"].get("objects", [])
            if objects:
                labels = [o["label"] for o in objects]
                parts.append(f"  Objects: {', '.join(labels)}")
        if obs.get("person_detections"):
            faces = obs["person_detections"].get("faces", [])
            named = [f["person_name"] for f in faces if f.get("person_name")]
            if named:
                parts.append(f"  People: {', '.join(named)}")
        context_parts.append("\n".join(parts))

    context = "\n\n".join(context_parts)

    system_prompt = (
        "You are Nurby, an AI camera monitoring assistant. Answer the user's "
        "question based ONLY on the observation data provided below. Be concise "
        "and specific. Reference timestamps and camera names. If the data does not "
        "contain enough information to answer, say so."
    )

    user_prompt = (
        f"Observation data:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Answer concisely based on the observations above."
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
