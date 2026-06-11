"""
Backfill embeddings for observations that have VLM descriptions
but no description_embedding yet.
"""

import logging

from sqlalchemy import and_, select

from services.search.embeddings import generate_embedding, get_embedding_provider
from shared.database import async_session
from shared.models import Observation

logger = logging.getLogger("nurby.search.backfill")


async def backfill_embeddings(batch_size: int = 50) -> int:
    """Generate embeddings for observations missing them.

    Queries observations where description_embedding IS NULL and
    vlm_description IS NOT NULL, generates embeddings in batches,
    and updates the records.

    Returns the total count of updated records.
    """
    provider = await get_embedding_provider()
    updated_total = 0

    while True:
        async with async_session() as db:
            stmt = (
                select(Observation)
                .where(
                    and_(
                        Observation.description_embedding.is_(None),
                        Observation.vlm_description.isnot(None),
                    )
                )
                .limit(batch_size)
            )
            result = await db.execute(stmt)
            batch = result.scalars().all()

            if not batch:
                break

            for obs in batch:
                try:
                    # Build combined text from all available context
                    parts = []
                    if obs.vlm_description:
                        parts.append(obs.vlm_description)

                    if obs.object_detections and obs.object_detections.get("objects"):
                        labels = [o["label"] for o in obs.object_detections["objects"]]
                        parts.append("Objects detected. " + ", ".join(labels))

                    if obs.person_detections and obs.person_detections.get("faces"):
                        named = [
                            f["person_name"]
                            for f in obs.person_detections["faces"]
                            if f.get("person_name")
                        ]
                        if named:
                            parts.append("People present. " + ", ".join(named))

                    embed_text = ". ".join(parts)
                    embedding = await generate_embedding(embed_text, provider)
                    obs.description_embedding = embedding
                    updated_total += 1
                except Exception:
                    logger.warning(
                        "Failed to generate embedding for observation %s, skipping",
                        obs.id,
                    )

            await db.commit()
            logger.info(
                "Backfill batch complete. %d observations updated so far",
                updated_total,
            )

    logger.info("Backfill finished. %d observations updated in total", updated_total)
    return updated_total
