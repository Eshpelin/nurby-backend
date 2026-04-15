"""People management API. CRUD for persons + face photo upload."""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.database import get_db
from shared.models import FaceEmbedding, Person
from shared.schemas import PersonCreate, PersonResponse, PersonUpdate

router = APIRouter()

PHOTOS_DIR = os.path.join(settings.thumbnails_path, "persons")


@router.get("", response_model=list[PersonResponse])
async def list_persons(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Person).order_by(Person.created_at))
    return result.scalars().all()


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
