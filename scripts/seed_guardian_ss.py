"""Seed on-brand Guardian demo data for README screenshots.

Creates a dependant "Inara" with an enrolled photo, a fresh present sighting
on a "Classroom B" camera, a guardian link bound to the admin (premium +
primary), arrival/zone/pickup events, and an approved-pickup registry, so the
Guardian Panel, the dependant detail page, and the facility-admin page all
render rich and accurate.

Run inside the api container:
    docker cp scripts/seed_guardian_ss.py nurby-backend-api-1:/app/seed_g.py
    docker exec nurby-backend-api-1 python /app/seed_g.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw
from sqlalchemy import select

from shared.config import settings
from shared.database import async_session
from shared.models import (
    ApprovedPickup,
    Camera,
    Facility,
    GuardianEvent,
    GuardianLink,
    Observation,
    Person,
    User,
)

THUMBS = settings.thumbnails_path
os.makedirs(THUMBS, exist_ok=True)


def _draw_photo(path: str) -> None:
    img = Image.new("RGB", (320, 320), (24, 28, 34))
    d = ImageDraw.Draw(img)
    # simple friendly avatar: shoulders + head
    d.ellipse([70, 40, 250, 220], fill=(212, 180, 160))  # head
    d.ellipse([40, 210, 280, 420], fill=(52, 120, 90))  # shoulders/shirt
    d.ellipse([120, 110, 150, 140], fill=(40, 40, 48))  # eyes
    d.ellipse([170, 110, 200, 140], fill=(40, 40, 48))
    d.arc([135, 140, 185, 185], start=20, end=160, fill=(120, 70, 60), width=5)
    img.save(path, "JPEG", quality=90)


def _draw_scene(path: str, label: str) -> None:
    img = Image.new("RGB", (960, 540), (16, 18, 22))
    d = ImageDraw.Draw(img)
    # a soft "classroom" scene: floor, a desk, two small figures
    d.rectangle([0, 360, 960, 540], fill=(30, 34, 40))
    d.rectangle([120, 300, 360, 420], fill=(70, 55, 40))  # desk
    for cx, col in ((520, (60, 130, 100)), (640, (70, 90, 150))):
        d.ellipse([cx - 30, 200, cx + 30, 260], fill=(212, 180, 160))  # head
        # eyes + mouth so a sharp face reads differently from a blurred one
        d.ellipse([cx - 16, 222, cx - 6, 232], fill=(35, 30, 28))
        d.ellipse([cx + 6, 222, cx + 16, 232], fill=(35, 30, 28))
        d.arc([cx - 14, 234, cx + 14, 252], start=20, end=160, fill=(120, 70, 60), width=3)
        d.rectangle([cx - 45, 255, cx + 45, 400], fill=col)  # body
    d.text((24, 24), label, fill=(180, 200, 190))
    img.save(path, "JPEG", quality=85)


async def main() -> None:
    async with async_session() as db:
        now = datetime.now(timezone.utc)

        # Admin user becomes the guardian.
        admin = (
            await db.execute(select(User).where(User.role == "admin"))
        ).scalars().first()
        if admin is None:
            print("no admin user; run setup first")
            return

        # Default facility.
        fac = (
            await db.execute(select(Facility).where(Facility.is_default.is_(True)))
        ).scalar_one_or_none()
        if fac is None:
            fac = Facility(name="Sunrise Daycare", slug="default", is_default=True)
            db.add(fac)
            await db.flush()

        # Classroom camera.
        cam = (
            await db.execute(select(Camera).where(Camera.name == "Classroom B"))
        ).scalar_one_or_none()
        if cam is None:
            cam = Camera(
                name="Classroom B",
                stream_url="file:///demo/classroom.mp4",
                stream_type="file",
                location_label="Classroom B",
            )
            db.add(cam)
            await db.flush()

        # Dependant "Inara" with an enrolled photo.
        inara = (
            await db.execute(select(Person).where(Person.display_name == "Inara"))
        ).scalar_one_or_none()
        photo_path = os.path.join(THUMBS, "inara_photo.jpg")
        _draw_photo(photo_path)
        if inara is None:
            inara = Person(display_name="Inara", relationship="daughter", photo_path=photo_path)
            db.add(inara)
            await db.flush()
        else:
            inara.photo_path = photo_path

        # Fresh "present" sighting (now) so the status reads green, plus a
        # thumbnail the (blurred) image endpoint can serve.
        thumb = os.path.join(THUMBS, "inara_scene.jpg")
        _draw_scene(thumb, "Classroom B  10:42")
        # Inara is the green figure (head centred ~520); the blue figure is an
        # unmatched stranger. Reveal should leave Inara's face sharp and keep
        # the stranger blurred.
        det = {
            "count": 2,
            "person_name": "Inara",
            "faces": [
                {"person_id": str(inara.id), "person_name": "Inara",
                 "bbox": [488, 198, 552, 262], "match_distance": 0.34},
                {"person_id": None, "person_name": None,
                 "bbox": [608, 198, 672, 262], "match_distance": None},
            ],
        }
        obs = Observation(
            camera_id=cam.id,
            started_at=now - timedelta(seconds=25),
            person_detections=det,
            thumbnail_path=thumb,
            vlm_description="A child in a green shirt at a table in Classroom B.",
        )
        db.add(obs)
        # a couple earlier sightings for the timeline depth
        for mins, lbl in ((180, "Playground"), (320, "Classroom B")):
            db.add(
                Observation(
                    camera_id=cam.id,
                    started_at=now - timedelta(minutes=mins),
                    person_detections=det,
                    thumbnail_path=thumb,
                    vlm_description=f"Inara seen near {lbl}.",
                )
            )

        # Guardian link: admin follows Inara, premium + primary + live image.
        link = (
            await db.execute(
                select(GuardianLink).where(
                    GuardianLink.guardian_user_id == admin.id,
                    GuardianLink.person_id == inara.id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            link = GuardianLink(
                facility_id=fac.id,
                person_id=inara.id,
                guardian_user_id=admin.id,
                relationship_label="mother",
                tier="full",
                premium=True,
                live_presence=True,
                live_video=True,
                is_primary_parent=True,
                granted_by_user_id=admin.id,
            )
            db.add(link)
        else:
            link.premium = True
            link.live_presence = True
            link.live_video = True
            link.is_primary_parent = True
            link.revoked_at = None

        # Approved pickups.
        for nm, plate in (("Mom", None), ("Dad (car)", "DHA-1429")):
            exists = (
                await db.execute(
                    select(ApprovedPickup).where(
                        ApprovedPickup.person_id == inara.id, ApprovedPickup.name == nm
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(
                    ApprovedPickup(
                        person_id=inara.id,
                        name=nm,
                        kind="vehicle" if plate else "person",
                        vehicle_plate=plate,
                        created_by_user_id=admin.id,
                    )
                )

        # Guardian events for the day-timeline + pickup-moment card.
        await db.execute(
            GuardianEvent.__table__.delete().where(GuardianEvent.person_id == inara.id)
        )
        events = [
            ("arrived", "Inara arrived at Classroom B.", "info", "Classroom B", now - timedelta(hours=6, minutes=46)),
            ("entered_zone", "Inara entered Playground.", "info", "Playground", now - timedelta(hours=4, minutes=12)),
            ("left_zone", "Inara left Playground.", "info", "Playground", now - timedelta(hours=3, minutes=30)),
            ("picked_up", "Inara was picked up by Mom.", "info", "Front gate", now - timedelta(minutes=8)),
        ]
        for kind, msg, sev, zone, at in events:
            ev = GuardianEvent(
                person_id=inara.id, kind=kind, message=msg, severity=sev, zone=zone,
                pickup_matched=True if kind == "picked_up" else None,
                pickup_name="Mom" if kind == "picked_up" else None,
            )
            ev.at = at
            db.add(ev)

        await db.commit()
        print("seeded guardian demo: link_id", str(link.id), "person", str(inara.id))


asyncio.run(main())
