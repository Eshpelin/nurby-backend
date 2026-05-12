"""Body cluster suggestions API.

Mirrors the face cluster suggestion endpoints in routes/persons.py but
operates on appearance-based clusters built from OSNet body embeddings.
A "tentative" cluster is body-only. A "confirmed" cluster has been
co-verified by a face hit on the same frame and inherits the linked
face cluster's Person.

Naming a body cluster creates or links to a Person, then writes the
body samples' embeddings as FaceEmbedding-style anchors are NOT stored
(face embeddings live in their own table). For body identity, the
cluster itself is the anchor. Subsequent body detections cluster
against the named row's representative_embedding.
"""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import decode_access_token, get_current_user
from shared.config import settings
from shared.database import get_db
from shared.models import BodyCluster, BodyClusterSample, Person, User

router = APIRouter()


class NameBodyClusterBody(BaseModel):
    display_name: str
    relationship: str | None = None


class LinkBodyClusterBody(BaseModel):
    person_id: uuid.UUID


@router.get("/suggestions", response_model=list)
async def list_body_suggestions(
    min_sightings: int = Query(default=3, ge=1),
    include_confirmed: bool = Query(default=False),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List tentative body clusters pending naming or face co-verification."""
    stmt = (
        select(BodyCluster)
        .where(BodyCluster.status == "pending")
        .where(BodyCluster.sighting_count >= min_sightings)
    )
    if not include_confirmed:
        stmt = stmt.where(BodyCluster.confidence == "tentative")
    stmt = stmt.order_by(BodyCluster.sighting_count.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(c.id),
            "sample_thumbnail_path": c.sample_thumbnail_path,
            "sighting_count": c.sighting_count,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "first_camera_id": str(c.first_camera_id) if c.first_camera_id else None,
            "status": c.status,
            "confidence": c.confidence,
            "person_id": str(c.person_id) if c.person_id else None,
            "linked_face_cluster_id": (
                str(c.linked_face_cluster_id) if c.linked_face_cluster_id else None
            ),
            "auto_label_number": c.auto_label_number,
            "auto_label": (
                f"Unknown body {c.auto_label_number}"
                if c.auto_label_number else "Unknown body"
            ),
            "appearance_description": c.appearance_description,
        }
        for c in rows
    ]


@router.get("/suggestions/{cluster_id}/samples")
async def get_body_samples(
    cluster_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(BodyClusterSample)
            .where(BodyClusterSample.cluster_id == cluster_id)
            .order_by(BodyClusterSample.captured_at.desc())
            .limit(12)
        )
    ).scalars().all()
    return [
        {
            "id": str(s.id),
            "camera_id": str(s.camera_id),
            "thumbnail_path": s.thumbnail_path,
            "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        }
        for s in rows
    ]


@router.get("/suggestions/{cluster_id}/thumbnail")
async def get_body_thumbnail(
    cluster_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    cluster = await db.get(BodyCluster, cluster_id)
    if not cluster or not cluster.sample_thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return _serve_thumbnail(cluster.sample_thumbnail_path)


@router.get("/suggestions/{cluster_id}/samples/{sample_id}/thumbnail")
async def get_body_sample_thumbnail(
    cluster_id: uuid.UUID,
    sample_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    sample = await db.get(BodyClusterSample, sample_id)
    if not sample or sample.cluster_id != cluster_id or not sample.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return _serve_thumbnail(sample.thumbnail_path)


@router.post("/suggestions/{cluster_id}/link")
async def link_body_cluster(
    cluster_id: uuid.UUID,
    body: LinkBodyClusterBody,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Attach a body cluster to an existing Person.

    Used when a face cluster is already named and the user wants to say
    "this body cluster is also that Person." Marks the cluster confirmed
    so the fusion layer treats it as a strong identity going forward.
    """
    cluster = await db.get(BodyCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Body cluster not found")
    person = await db.get(Person, body.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    cluster.person_id = person.id
    cluster.status = "named"
    cluster.confidence = "confirmed"
    await db.commit()
    return {"ok": True}


@router.post("/suggestions/{cluster_id}/name")
async def name_body_cluster(
    cluster_id: uuid.UUID,
    body: NameBodyClusterBody,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Person from a body cluster.

    Use when no face has been captured for this individual but you still
    want to label and track them. The cluster is marked confirmed and
    becomes the identity anchor for future body matches.
    """
    cluster = await db.get(BodyCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Body cluster not found")
    if cluster.status != "pending":
        raise HTTPException(status_code=400, detail="Cluster already processed")

    # Uniqueness check piggybacks on persons.py helper if available.
    try:
        from services.api.routes.persons import _ensure_unique_display_name
        await _ensure_unique_display_name(db, body.display_name)
    except ImportError:
        pass

    person = Person(
        display_name=body.display_name,
        relationship=body.relationship,
        consent_given=True,
        photo_path=cluster.sample_thumbnail_path,
    )
    db.add(person)
    await db.flush()
    cluster.person_id = person.id
    cluster.status = "named"
    cluster.confidence = "confirmed"
    await db.commit()
    return {"person_id": str(person.id), "cluster_id": str(cluster.id)}


@router.post("/suggestions/{cluster_id}/ignore")
async def ignore_body_cluster(
    cluster_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a body cluster as ignored. Skipped by future cluster searches."""
    cluster = await db.get(BodyCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Body cluster not found")
    cluster.status = "ignored"
    await db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------

def _serve_thumbnail(path: str) -> FileResponse:
    abs_path = os.path.abspath(path)
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not abs_path.startswith(allowed_dir + os.sep) and not abs_path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(abs_path, media_type="image/jpeg")
