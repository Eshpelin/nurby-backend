"""Seed rich demo data WITH generated imagery for README screenshots.

Inserts people, vehicles, observations, rules, a narrative digest, and a
conversation, and draws placeholder imagery with PIL at the exact paths
the photo/thumbnail routes serve, so the People / Vehicles / Dashboard /
Timeline pages render fully. Run INSIDE the api container (has the app on
PYTHONPATH, the DB session, the shared thumbnails volume, and PIL):

    docker cp scripts/seed_screenshots.py nurby-backend-api-1:/app/seed_ss.py
    docker exec nurby-backend-api-1 python /app/seed_ss.py
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete, select

from shared.config import settings
from shared.database import async_session
from shared.models import (
    Camera,
    Conversation,
    DailyDigest,
    Observation,
    Person,
    Rule,
    Transcript,
    Vehicle,
)

THUMBS = settings.thumbnails_path
random.seed(7)


def _font(size: int):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _center(draw, box, text, font, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    x = box[0] + (box[2] - box[0] - w) / 2 - l
    y = box[1] + (box[3] - box[1] - h) / 2 - t
    draw.text((x, y), text, font=font, fill=fill)


def _grad(w, h, top, bot):
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        f = y / max(1, h - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * f) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img, d


EMERALD = (16, 185, 129)
AMBER = (245, 158, 11)
RED = (239, 68, 68)
SLATE = (148, 163, 184)


def surveillance_frame(path, cam_name, ts_text, label, label_color, sub=""):
    w, h = 854, 480
    img, d = _grad(w, h, (10, 14, 18), (4, 6, 9))
    for y in range(0, h, 3):
        d.line([(0, y), (w, y)], fill=(18, 22, 28))
    d.rectangle([0, 0, w, 34], fill=(0, 0, 0))
    d.rectangle([0, h - 34, w, h], fill=(0, 0, 0))
    bx = random.randint(120, 360)
    by = random.randint(90, 200)
    bw = random.randint(150, 240)
    bh = random.randint(150, 220)
    d.rectangle([bx, by, bx + bw, by + bh], outline=label_color, width=3)
    tagw = d.textlength(label, font=_font(20)) + 16
    d.rectangle([bx, by - 26, bx + tagw, by], fill=label_color)
    d.text((bx + 8, by - 24), label, font=_font(20), fill=(8, 10, 12))
    d.ellipse([16, 9, 30, 23], fill=RED)
    d.text((38, 8), "REC", font=_font(16), fill=(230, 230, 230))
    d.text((w - 230, 8), ts_text, font=_font(16), fill=(210, 210, 210))
    d.text((16, h - 26), cam_name, font=_font(18), fill=EMERALD)
    if sub:
        sw = d.textlength(sub, font=_font(15))
        d.text((w - sw - 16, h - 25), sub, font=_font(15), fill=SLATE)
    img.save(path, "JPEG", quality=88)


def avatar(path, name, accent, starred=False):
    w = h = 480
    palettes = [((30, 41, 59), (15, 23, 42)), ((49, 46, 70), (24, 24, 37)),
                ((20, 50, 45), (8, 24, 22)), ((55, 35, 30), (24, 16, 14))]
    top, bot = random.choice(palettes)
    img, d = _grad(w, h, top, bot)
    d.ellipse([90, 70, 390, 370], fill=accent)
    initials = "".join([p[0] for p in name.split()[:2]]).upper() if name[0].isalpha() else "??"
    _center(d, (90, 70, 390, 370), initials, _font(150), (245, 245, 245))
    _center(d, (0, 390, w, 460), name, _font(40), (226, 232, 240))
    if starred:
        d.text((40, 36), "*", font=_font(60), fill=AMBER)
    img.save(path, "JPEG", quality=90)


def vehicle_img(path, label, color_rgb, plate):
    w, h = 854, 480
    img, d = _grad(w, h, (17, 21, 28), (6, 8, 12))
    for y in range(0, h, 3):
        d.line([(0, y), (w, y)], fill=(15, 18, 24))
    bx, by, bw, bh = 180, 200, 500, 150
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=26, fill=color_rgb)
    d.rounded_rectangle([bx + 90, by - 70, bx + bw - 90, by + 20], radius=24, fill=color_rgb)
    d.ellipse([bx + 60, by + bh - 40, bx + 140, by + bh + 40], fill=(20, 20, 22))
    d.ellipse([bx + bw - 140, by + bh - 40, bx + bw - 60, by + bh + 40], fill=(20, 20, 22))
    pw = d.textlength(plate, font=_font(34)) + 28
    d.rounded_rectangle([w / 2 - pw / 2, by + bh + 54, w / 2 + pw / 2, by + bh + 104],
                        radius=8, fill=(245, 245, 245))
    _center(d, (w / 2 - pw / 2, by + bh + 54, w / 2 + pw / 2, by + bh + 104),
            plate, _font(34), (20, 20, 20))
    d.text((16, 8), label, font=_font(22), fill=EMERALD)
    img.save(path, "JPEG", quality=90)


NOW = datetime.now(timezone.utc)


def t(hours_ago, mins=0):
    return NOW - timedelta(hours=hours_ago, minutes=mins)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def main():
    for sub in ("persons", "vehicles", "observations"):
        os.makedirs(os.path.join(THUMBS, sub), exist_ok=True)

    async with async_session() as db:
        await db.execute(delete(Transcript))
        await db.execute(delete(Conversation))
        await db.execute(delete(DailyDigest))
        await db.execute(delete(Rule))
        await db.execute(delete(Observation))
        await db.execute(delete(Person))
        await db.execute(delete(Vehicle))
        await db.commit()

        # Demo feeds. one clip rich in distinct frontal faces (people walking
        # toward camera) and one rich in vehicles, so the People and Vehicles
        # pages both have real material. scene_mode=indoor keeps face
        # clustering on (outdoor skips it to avoid passerby flooding).
        FACE_URL = "https://videos.pexels.com/video-files/6784527/6784527-uhd_3840_2160_24fps.mp4"
        VEH_URL = "https://videos.pexels.com/video-files/854100/854100-hd_1920_1080_25fps.mp4"
        # name -> (location, url)
        CAM_FEEDS = {
            "Front Door": ("Entrance", FACE_URL),
            "Living Room": ("Indoor", FACE_URL),
            "Driveway": ("Front", VEH_URL),
            "Backyard": ("Garden", VEH_URL),
        }

        demo = (await db.execute(select(Camera))).scalars().first()
        if demo is None:
            demo = Camera(name="Front Door", stream_url=FACE_URL, stream_type="file")
            db.add(demo)
            await db.flush()
        else:
            demo.name = "Front Door"
        demo.location_label = CAM_FEEDS["Front Door"][0]
        demo.stream_url = CAM_FEEDS["Front Door"][1]
        demo.stream_type = "file"
        demo.scene_mode = "indoor"
        demo.detect_faces = True
        demo.detect_objects = True
        demo.status = "live"
        cams = [demo]
        for nm, (loc, url) in CAM_FEEDS.items():
            if nm == "Front Door":
                continue
            c = (await db.execute(select(Camera).where(Camera.name == nm))).scalars().first()
            if c is None:
                c = Camera(name=nm)
                db.add(c)
            c.location_label = loc
            c.stream_url = url
            c.stream_type = "file"
            c.scene_mode = "indoor"
            c.status = "live"
            c.detect_objects = True
            c.detect_faces = True
            c.recording_enabled = True
            c.recording_mode = "motion"
            cams.append(c)
        await db.flush()
        cam_id = {c.name: c.id for c in cams}

        people_spec = [
            ("Sarah Chen", "Sarah", "Family", True, EMERALD,
             "Adult woman, dark shoulder-length hair, often in a green raincoat."),
            ("Mike Rivera", "Mike", "Family", True, (59, 130, 246),
             "Adult man, beard, baseball cap, usually carrying a backpack."),
            ("Emma Doyle", "Em", "Family", False, (236, 72, 153),
             "Young woman, blonde, frequently with the dog on a leash."),
            ("David Park", None, "Neighbor", False, (234, 179, 8),
             "Older man, glasses, grey jacket. Waves from the sidewalk."),
            ("Unknown person", None, None, False, SLATE,
             "Unrecognized face, hooded jacket. Seen once near the driveway at 02:14."),
        ]
        people = []
        for name, nick, rel, star, accent, desc in people_spec:
            pid = uuid.uuid4()
            pp = os.path.join(THUMBS, "persons", f"{pid}.jpg")
            avatar(pp, name if name != "Unknown person" else "??", accent, star)
            p = Person(id=pid, display_name=name, nickname=nick, relationship=rel,
                       is_starred=star, consent_given=True, photo_path=pp,
                       recap_cached_status=desc, created_at=t(72))
            db.add(p)
            people.append(p)
        await db.flush()

        veh_spec = [
            ("Sarah's Altima", "ABC1234", "car", "Nissan", "Altima", "red",
             (200, 40, 40), "Red Nissan Altima sedan with slightly tinted windows.", True, 23),
            ("Mike's F-150", "TRK8890", "truck", "Ford", "F-150", "white",
             (235, 235, 235), "White Ford F-150 pickup, roof rack, mild front dent.", True, 14),
            ("Blue Tesla", "EV77777", "car", "Tesla", "Model 3", "blue",
             (40, 80, 200), "Blue Tesla Model 3 with aftermarket black rims.", False, 6),
            ("Delivery van", "VAN4521", "van", "Mercedes", "Sprinter", "silver",
             (170, 175, 185), "Silver Mercedes Sprinter courier van, side branding.", False, 4),
        ]
        vehicles = []
        for name, plate, vtype, make, model, color, rgb, desc, star, count in veh_spec:
            vid = uuid.uuid4()
            vp = os.path.join(THUMBS, "vehicles", f"{vid}.jpg")
            vehicle_img(vp, f"{color.title()} {make} {model}", rgb, plate)
            v = Vehicle(id=vid, identity_key=plate, display_name=name, license_plate=plate,
                        vehicle_type=vtype, make=make, model=model, color=color,
                        description=desc, description_status="done", photo_path=vp,
                        is_provisional=False, is_starred=star, sighting_count=count,
                        first_seen_at=t(70), last_seen_at=t(random.randint(0, 6)),
                        first_camera_id=cam_id["Driveway"])
            db.add(v)
            vehicles.append(v)
        await db.flush()

        scenes = [
            ("Front Door", "person", EMERALD, 0.94,
             "Sarah arrived home, unlocked the front door and carried in two grocery bags.",
             people[0], None),
            ("Driveway", "car", RED, 0.91,
             "Red Nissan Altima pulled into the driveway and parked. One occupant stepped out.",
             None, vehicles[0]),
            ("Backyard", "dog", AMBER, 0.88,
             "The dog ran across the lawn chasing a ball near the back fence.", None, None),
            ("Front Door", "person", (59, 130, 246), 0.9,
             "Mike walked up to the porch carrying a backpack and checked the mailbox.",
             people[1], None),
            ("Driveway", "truck", EMERALD, 0.86,
             "White Ford F-150 reversed into the driveway. Tailgate opened briefly.",
             None, vehicles[1]),
            ("Living Room", "person", (236, 72, 153), 0.82,
             "Emma sat on the couch with the dog. Quiet indoor activity.", people[2], None),
            ("Driveway", "van", SLATE, 0.79,
             "Silver delivery van stopped at the curb. Courier left a package on the porch.",
             None, vehicles[3]),
            ("Front Door", "package", AMBER, 0.84,
             "A cardboard package was left on the doormat next to the planter.", None, None),
            ("Driveway", "person", RED, 0.73,
             "Unrecognized person in a hooded jacket lingered near the driveway at night.",
             people[4], None),
            ("Backyard", "car", (40, 80, 200), 0.8,
             "Blue Tesla Model 3 idled in the alley behind the backyard for a minute.",
             None, vehicles[2]),
        ]
        for i, (cam, label, color, conf, desc, pers, veh) in enumerate(scenes):
            oid = uuid.uuid4()
            hours = 17 - i * 1.6
            st = t(int(hours), int((hours % 1) * 60))
            thumb = os.path.join(THUMBS, "observations", f"{oid}.jpg")
            surveillance_frame(thumb, cam, fmt(st), label.upper(), color,
                               sub=(pers.display_name if pers else
                                    (veh.display_name if veh else "")))
            pd = None
            if pers:
                pd = {"count": 1, "person_name": pers.display_name,
                      "faces": [{"person_id": str(pers.id), "confidence": conf}]}
            vd = None
            if veh:
                vd = {"count": 1, "vehicles": [{
                    "vehicle_id": str(veh.id), "identity_key": veh.identity_key,
                    "plate_text": veh.license_plate, "label": veh.vehicle_type,
                    "confidence": conf}]}
            db.add(Observation(
                id=oid, camera_id=cam_id[cam], started_at=st,
                ended_at=st + timedelta(seconds=14),
                object_detections={"count": 1, "objects": [
                    {"label": label, "confidence": conf, "bbox": [120, 90, 320, 300]}]},
                person_detections=pd, vehicle_detections=vd,
                vlm_description=desc, vlm_provider="gemma3:4b", confidence=conf,
                thumbnail_path=thumb))
        await db.flush()

        rules = [
            ("Unknown face at night", {"type": "face_unknown"},
             {"time_range": {"start": "22:00", "end": "06:00"}},
             [{"type": "notify", "message": "Unknown person at {camera_name}", "severity": "warning"},
              {"type": "record", "duration_seconds": 30}], 600),
            ("Sarah arrives home", {"type": "face_recognized", "person_id": str(people[0].id)},
             None, [{"type": "notify", "message": "Sarah is home", "severity": "info"}], 900),
            ("Package on porch", {"type": "object_detected", "label": "package", "min_confidence": 0.6},
             None, [{"type": "notify", "message": "Package delivered to {camera_name}", "severity": "info"},
                    {"type": "webhook", "url": "https://example.com/hooks/package",
                     "payload_template": {"camera": "{camera_name}"}}], 300),
            ("Unknown vehicle in driveway", {"type": "vehicle_detected", "mode": "any"},
             {"cameras": ["Driveway"]},
             [{"type": "record", "duration_seconds": 45},
              {"type": "notify", "message": "Vehicle in the driveway", "severity": "warning"}], 300),
            ("Baby cry detected", {"type": "audio_event", "label": "baby_cry", "min_score": 0.5},
             None, [{"type": "notify", "message": "Possible baby cry in {camera_name}", "severity": "warning"}], 120),
        ]
        for name, trig, cond, acts, cd in rules:
            db.add(Rule(name=name, enabled=True, trigger_pattern=trig, conditions=cond,
                        actions=acts, cooldown_seconds=cd, created_at=t(50)))

        db.add(DailyDigest(
            window_start=NOW.replace(hour=0, minute=0, second=0, microsecond=0),
            window_end=NOW, generated_at=t(0, 30), provider_name="gemma3:4b",
            summary_text=(
                "Quiet night, busy afternoon. Nothing stirred until 6:48 AM when "
                "Sarah left for work and her red Altima pulled out of the driveway. "
                "Mike came back around lunch and checked the mailbox. A courier "
                "dropped a package on the porch at 2:10 PM, and Emma spent the "
                "evening on the couch with the dog. The only odd moment was just "
                "after 2 AM, when an unrecognized person in a hooded jacket lingered "
                "near the driveway for about a minute before walking off."),
            facts={"observations": 10, "people_seen": 4, "vehicles_seen": 4,
                   "peak_window": "afternoon", "anomalies": 1}))

        conv = Conversation(
            camera_id=cam_id["Front Door"], started_at=t(3),
            ended_at_provisional=t(3) + timedelta(seconds=40),
            ended_at=t(3) + timedelta(seconds=40), transcript_count=3, finalized=True,
            summary_text="A courier confirmed the package was for this address and left it by the door.",
            summary_provider_name="gemma3:4b",
            speakers_seen={"speaker_count": 2, "person_ids": [str(people[1].id)]})
        db.add(conv)
        await db.flush()
        base = t(3)
        for i, (sp, txt) in enumerate([
            (people[1].id, "Hi, I've got a delivery for the Chen residence?"),
            (None, "Yes, that's us. You can leave it by the door."),
            (people[1].id, "Perfect, have a good one."),
        ]):
            db.add(Transcript(
                camera_id=cam_id["Front Door"], started_at=base + timedelta(seconds=i * 12),
                ended_at=base + timedelta(seconds=i * 12 + 8), text=txt, language="en",
                provider="faster_whisper", model="small.en", confidence=0.9,
                speaker_person_id=sp, speaker_source="video" if sp else "ambiguous",
                conversation_id=conv.id))

        await db.commit()
        print(f"seeded: {len(people)} people, {len(vehicles)} vehicles, "
              f"{len(scenes)} observations, {len(rules)} rules, 1 digest, 1 conversation")


if __name__ == "__main__":
    asyncio.run(main())
