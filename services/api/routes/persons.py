"""People management API. CRUD for persons + face photo upload + auto-discovery suggestions."""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.database import get_db
from shared.models import FaceCluster, FaceClusterSample, FaceEmbedding, Person
from shared.schemas import PersonCreate, PersonResponse, PersonUpdate

router = APIRouter()

PHOTOS_DIR = os.path.join(settings.thumbnails_path, "persons")


@router.get("", response_model=list[PersonResponse])
async def list_persons(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Person).order_by(Person.created_at))
    return result.scalars().all()


# ── Face cluster suggestion endpoints ──


class NameClusterBody(PydanticBaseModel):
    display_name: str
    relationship: str | None = None


@router.get("/suggestions", response_model=list)
async def list_suggestions(
    min_sightings: int = Query(default=2, ge=1, description="Minimum sightings to show as suggestion"),
    db: AsyncSession = Depends(get_db),
):
    """List auto-discovered face clusters pending user naming."""
    result = await db.execute(
        select(FaceCluster)
        .where(FaceCluster.status == "pending")
        .where(FaceCluster.sighting_count >= min_sightings)
        .order_by(FaceCluster.sighting_count.desc())
    )
    clusters = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "sample_thumbnail_path": c.sample_thumbnail_path,
            "sighting_count": c.sighting_count,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "first_camera_id": str(c.first_camera_id) if c.first_camera_id else None,
            "status": c.status,
        }
        for c in clusters
    ]


@router.get("/suggestions/{cluster_id}/samples")
async def get_cluster_samples(
    cluster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get sample face thumbnails for a cluster."""
    result = await db.execute(
        select(FaceClusterSample)
        .where(FaceClusterSample.cluster_id == cluster_id)
        .order_by(FaceClusterSample.captured_at.desc())
        .limit(12)
    )
    samples = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "camera_id": str(s.camera_id),
            "thumbnail_path": s.thumbnail_path,
            "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        }
        for s in samples
    ]


@router.get("/suggestions/{cluster_id}/thumbnail")
async def get_cluster_thumbnail(
    cluster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the representative face thumbnail for a cluster."""
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster or not cluster.sample_thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    if not os.path.exists(cluster.sample_thumbnail_path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(cluster.sample_thumbnail_path, media_type="image/jpeg")


@router.get("/suggestions/{cluster_id}/samples/{sample_id}/thumbnail")
async def get_sample_thumbnail(
    cluster_id: uuid.UUID,
    sample_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get thumbnail for a specific sample."""
    sample = await db.get(FaceClusterSample, sample_id)
    if not sample or not sample.thumbnail_path or sample.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    if not os.path.exists(sample.thumbnail_path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(sample.thumbnail_path, media_type="image/jpeg")


@router.post("/suggestions/{cluster_id}/name")
async def name_cluster(
    cluster_id: uuid.UUID,
    body: NameClusterBody,
    db: AsyncSession = Depends(get_db),
):
    """Name a face cluster. Creates Person and links all cluster embeddings."""
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if cluster.status != "pending":
        raise HTTPException(status_code=400, detail="Cluster already processed")

    # Create Person
    person = Person(
        display_name=body.display_name,
        relationship=body.relationship,
        consent_given=True,
        photo_path=cluster.sample_thumbnail_path,
    )
    db.add(person)
    await db.flush()

    # Link all cluster sample embeddings to the new person
    samples_result = await db.execute(
        select(FaceClusterSample).where(FaceClusterSample.cluster_id == cluster_id)
    )
    samples = samples_result.scalars().all()

    for sample in samples:
        face_emb = FaceEmbedding(
            person_id=person.id,
            embedding=sample.embedding,
            source="detection",
        )
        db.add(face_emb)

    # Update cluster
    cluster.person_id = person.id
    cluster.status = "named"

    await db.commit()
    await db.refresh(person)

    return {
        "status": "ok",
        "person_id": str(person.id),
        "display_name": person.display_name,
        "embeddings_linked": len(samples),
    }


@router.post("/suggestions/{cluster_id}/ignore")
async def ignore_cluster(
    cluster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Dismiss a face cluster suggestion."""
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cluster.status = "ignored"
    await db.commit()
    return {"status": "ok"}


# ── Person CRUD endpoints ──


@router.post("", response_model=PersonResponse, status_code=201)
async def create_person(body: PersonCreate, db: AsyncSession = Depends(get_db)):
    person = Person(**body.model_dump())
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return person


@router.get("/{person_id}", response_model=PersonResponse)
async def get_person(person_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.patch("/{person_id}", response_model=PersonResponse)
async def update_person(
    person_id: uuid.UUID, body: PersonUpdate, db: AsyncSession = Depends(get_db)
):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(person, field, value)

    await db.commit()
    await db.refresh(person)
    return person


@router.delete("/{person_id}", status_code=204)
async def delete_person(person_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.delete(person)
    await db.commit()


@router.post("/{person_id}/face", response_model=dict)
async def upload_face(
    person_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a face photo. Generates embedding and stores for matching."""
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Save photo
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "jpg"
    photo_filename = f"{person_id}.{ext}"
    photo_path = os.path.join(PHOTOS_DIR, photo_filename)

    with open(photo_path, "wb") as f:
        f.write(image_bytes)

    person.photo_path = photo_path
    await db.commit()

    # Generate face embedding
    try:
        from services.perception.faces import FaceRecognizer
        embedding = FaceRecognizer.embed_from_image(image_bytes)
    except Exception:
        embedding = None

    if embedding is None:
        return {
            "status": "photo_saved",
            "message": "Photo saved but no face detected. Try a clearer photo with one visible face.",
            "photo_path": photo_path,
        }

    # Store embedding
    face_emb = FaceEmbedding(
        person_id=person_id,
        embedding=embedding,
        source="upload",
    )
    db.add(face_emb)
    await db.commit()

    return {
        "status": "ok",
        "message": "Face photo saved and embedding generated",
        "photo_path": photo_path,
        "embedding_id": str(face_emb.id),
    }


@router.get("/{person_id}/photo")
async def get_person_photo(person_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person.photo_path or not os.path.exists(person.photo_path):
        raise HTTPException(status_code=404, detail="No photo uploaded")
    return FileResponse(person.photo_path, media_type="image/jpeg")
