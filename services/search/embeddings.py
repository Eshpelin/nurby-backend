"""
Text embedding generation for semantic search.

Uses the configured VLM provider to generate text embeddings for
observation descriptions. Falls back to a simple TF-IDF-like
approach when no embedding API is available.
"""

import hashlib
import logging
import re

import httpx
import numpy as np
from sqlalchemy import select

from shared.database import async_session
from shared.models import Provider

logger = logging.getLogger("nurby.search.embeddings")

EMBEDDING_DIM = 384  # dimension for lightweight embeddings


async def get_embedding_provider() -> Provider | None:
    """Fetch active provider that supports embeddings."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Provider).where(Provider.active == True).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to fetch embedding provider")
        return None


async def generate_embedding(text: str, provider: Provider | None = None) -> list[float]:
    """Generate embedding vector for text.

    Tries provider API first (OpenAI embeddings endpoint).
    Falls back to deterministic hash-based embedding if no provider.
    """
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIM

    if provider and provider.kind == "openai":
        try:
            return await _openai_embedding(text, provider)
        except Exception:
            logger.warning("OpenAI embedding failed, using fallback")

    # Fallback. deterministic hash-based embedding
    return _hash_embedding(text)


async def _openai_embedding(text: str, provider: Provider) -> list[float]:
    """Call OpenAI embeddings API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{provider.base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {provider.api_key}"},
            json={
                "model": "text-embedding-3-small",
                "input": text,
                "dimensions": EMBEDDING_DIM,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]


def _hash_embedding(text: str) -> list[float]:
    """Generate deterministic embedding from text using word hashing.

    Not semantically meaningful but consistent. Allows basic
    keyword overlap matching via cosine similarity.
    """
    text = text.lower().strip()
    words = re.findall(r"\w+", text)
    vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)

    for word in words:
        h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        indices = []
        for i in range(8):
            indices.append((h >> (i * 10)) % EMBEDDING_DIM)
        for idx in indices:
            vec[idx] += 1.0

    # Normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    return vec.tolist()
