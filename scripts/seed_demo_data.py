"""Seed the database with realistic demo data for UI testing.

Usage:
    python -m scripts.seed_demo_data           # add demo data
    python -m scripts.seed_demo_data --clean   # wipe existing data first, then seed

Design notes.
- Observations are generated from scenarios (delivery, family arrival,
  empty scene, animal, etc.) rather than uniform random noise.
- Each scenario creates a burst of 2-5 correlated observations over
  20-60 seconds, matching how a real camera captures an event.
- Activity follows a circadian weighting. Quiet 1-5 AM, busy 7-9 AM
  and 4-9 PM.
- VLM descriptions actually match the object detections.
- vlm_provider reflects a realistic local model name.
- Cross-camera narratives. a delivery person appears on Driveway,
  then Front Door within 30 seconds.
"""

import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from services.perception.incident_tracker import assign_incident
from shared.database import async_session
from shared.models import (
    Camera,
    CameraStatusLog,
    DigestEntry,
    Event,
    FaceCluster,
    FaceClusterSample,
    Incident,
    Journey,
    Observation,
    Person,
    Rule,
)

UTC = timezone.utc
NOW = datetime.now(UTC)

# Realistic local VLM provider name
VLM_PROVIDER = "ollama-llava:7b"

# ── Camera definitions ──

CAMERAS = [
    {
        "name": "Front Door",
        "stream_url": "rtsp://192.168.1.100:554/stream1",
        "stream_type": "rtsp",
        "location_label": "Main entrance",
        "status": "live",
        "width": 1920,
        "height": 1080,
        "fps": 15.0,
        "recording_mode": "on_motion",
        "scene_mode": "outdoor",
        "digest_enabled": True,
        "digest_period": "24h",
    },
    {
        "name": "Backyard",
        "stream_url": "rtsp://192.168.1.101:554/stream1",
        "stream_type": "rtsp",
        "location_label": "Garden area",
        "status": "live",
        "width": 2560,
        "height": 1440,
        "fps": 20.0,
        "recording_mode": "on_motion",
        "scene_mode": "outdoor",
        "digest_enabled": True,
        "digest_period": "24h",
    },
    {
        "name": "Garage",
        "stream_url": "rtsp://192.168.1.102:554/stream1",
        "stream_type": "rtsp",
        "location_label": "Garage interior",
        "status": "recording",
        "width": 1920,
        "height": 1080,
        "fps": 10.0,
        "recording_mode": "on_object",
        "recording_trigger_objects": ["person", "car"],
        "scene_mode": "indoor",
        "digest_enabled": True,
        "digest_period": "24h",
    },
    {
        "name": "Kitchen",
        "stream_url": "rtsp://192.168.1.103:554/stream1",
        "stream_type": "rtsp",
        "location_label": "Indoor kitchen",
        "status": "live",
        "width": 1280,
        "height": 720,
        "fps": 15.0,
        "recording_mode": "on_motion",
        "scene_mode": "indoor",
        "digest_enabled": False,
    },
    {
        "name": "Driveway",
        "stream_url": "rtsp://192.168.1.104:554/stream1",
        "stream_type": "rtsp",
        "location_label": "Front driveway",
        "status": "live",
        "width": 1920,
        "height": 1080,
        "fps": 15.0,
        "recording_mode": "clip",
        "recording_clip_pre": 5,
        "recording_clip_post": 15,
        "scene_mode": "outdoor",
        "digest_enabled": True,
        "digest_period": "6h",
    },
]

# ── Household members ──

PERSONS = [
    {"display_name": "Sarah Chen", "nickname": "Mom", "relationship": "Family", "consent_given": True},
    {"display_name": "Mike Rodriguez", "nickname": "Dad", "relationship": "Family", "consent_given": True},
    {"display_name": "Emma Wilson", "nickname": "Em", "relationship": "Family", "consent_given": True},
    {"display_name": "James Park", "relationship": "Neighbor", "consent_given": True},
]

# ── Scenarios ──
# Each scenario is a sequence of observations that tells a coherent story.
# Observations are spaced a few seconds apart.

def bbox_person(offset_x: int = 0) -> list[int]:
    """Realistic person bbox. offset shifts horizontally for multi-person frames."""
    x1 = 200 + offset_x + random.randint(-30, 30)
    y1 = 80 + random.randint(-20, 20)
    return [x1, y1, x1 + 180, y1 + 380]


def bbox_vehicle(size: str = "car") -> list[int]:
    """Realistic vehicle bbox."""
    if size == "truck":
        return [80, 180, 820, 620]
    return [180, 220, 680, 540]


def bbox_license_plate(parent_bbox: list[int]) -> list[int]:
    """License plate bbox anchored to a vehicle bbox."""
    x1, _, x2, y2 = parent_bbox
    cx = (x1 + x2) // 2
    return [cx - 70, y2 - 60, cx + 70, y2 - 20]


def bbox_animal(kind: str) -> list[int]:
    """Bbox for animals."""
    if kind == "dog":
        return [random.randint(300, 500), random.randint(300, 400), random.randint(600, 750), random.randint(480, 560)]
    if kind == "cat":
        return [random.randint(400, 600), random.randint(350, 450), random.randint(520, 680), random.randint(480, 560)]
    if kind == "bird":
        return [random.randint(800, 1000), random.randint(100, 200), random.randint(880, 1080), random.randint(200, 300)]
    return [400, 400, 500, 500]


def bbox_face(person_bbox: list[int]) -> list[int]:
    """Face bbox anchored to top of person bbox."""
    x1, y1, x2, _ = person_bbox
    cx = (x1 + x2) // 2
    face_size = (x2 - x1) // 2
    return [cx - face_size // 2, y1 + 10, cx + face_size // 2, y1 + 10 + face_size]


# Scenario builders. Each returns a list of dicts describing observation steps.
# Each step has. delay_sec, detections, description, faces (optional list of person_name or None for unknown)

def scenario_delivery_driveway_then_door():
    """Delivery van arrives in driveway, then person walks up to front door."""
    van_bbox = bbox_vehicle("truck")
    person_bbox = bbox_person()
    return [
        # Driveway camera. van arrives
        {
            "camera": "Driveway",
            "delay_sec": 0,
            "detections": [
                {"label": "truck", "confidence": 0.94, "bbox": van_bbox},
                {"label": "license_plate", "confidence": 0.83, "bbox": bbox_license_plate(van_bbox), "plate_text": "FDX 4521"},
            ],
            "description": "A FedEx delivery truck has pulled into the driveway. The driver side door is visible.",
            "faces": None,
        },
        {
            "camera": "Driveway",
            "delay_sec": 12,
            "detections": [
                {"label": "truck", "confidence": 0.93, "bbox": van_bbox},
                {"label": "person", "confidence": 0.89, "bbox": person_bbox},
            ],
            "description": "A delivery driver has stepped out of the FedEx truck and is walking toward the house carrying a small package.",
            "faces": [None],  # unknown face
        },
        # Front Door. same person arrives
        {
            "camera": "Front Door",
            "delay_sec": 28,
            "detections": [
                {"label": "person", "confidence": 0.91, "bbox": bbox_person()},
                {"label": "backpack", "confidence": 0.62, "bbox": [280, 200, 360, 330]},
            ],
            "description": "A delivery driver in a FedEx uniform is at the front door holding a small package. They appear to be checking the address.",
            "faces": [None],
        },
        {
            "camera": "Front Door",
            "delay_sec": 45,
            "detections": [],
            "description": "The front porch is empty. A small package has been placed near the doormat.",
            "faces": None,
        },
    ]


def scenario_family_arrival_evening(person_name: str):
    """Family member arrives home. Driveway > Garage > Kitchen."""
    return [
        {
            "camera": "Driveway",
            "delay_sec": 0,
            "detections": [
                {"label": "car", "confidence": 0.95, "bbox": bbox_vehicle()},
                {"label": "license_plate", "confidence": 0.88, "bbox": bbox_license_plate(bbox_vehicle()), "plate_text": "7ABC 123"},
            ],
            "description": "A sedan is pulling into the driveway. Headlights are on, indicating evening arrival.",
            "faces": None,
        },
        {
            "camera": "Garage",
            "delay_sec": 35,
            "detections": [
                {"label": "car", "confidence": 0.94, "bbox": bbox_vehicle()},
                {"label": "license_plate", "confidence": 0.86, "bbox": bbox_license_plate(bbox_vehicle()), "plate_text": "7ABC 123"},
            ],
            "description": "A car is parking in the garage. Brake lights are visible. The garage door is closing.",
            "faces": None,
        },
        {
            "camera": "Garage",
            "delay_sec": 70,
            "detections": [
                {"label": "person", "confidence": 0.88, "bbox": bbox_person()},
                {"label": "car", "confidence": 0.92, "bbox": bbox_vehicle()},
            ],
            "description": f"{person_name} has stepped out of the parked car and is walking toward the house entry.",
            "faces": [person_name],
        },
        {
            "camera": "Kitchen",
            "delay_sec": 95,
            "detections": [
                {"label": "person", "confidence": 0.90, "bbox": bbox_person()},
            ],
            "description": f"{person_name} has entered the kitchen and is setting down a bag on the counter.",
            "faces": [person_name],
        },
    ]


def scenario_family_morning(person_name: str):
    """Morning routine in the kitchen."""
    return [
        {
            "camera": "Kitchen",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.89, "bbox": bbox_person()},
                {"label": "cup", "confidence": 0.71, "bbox": [520, 320, 580, 410]},
            ],
            "description": f"{person_name} is making coffee at the kitchen counter. A mug is visible next to the coffee machine.",
            "faces": [person_name],
        },
        {
            "camera": "Kitchen",
            "delay_sec": 180,
            "detections": [
                {"label": "person", "confidence": 0.91, "bbox": bbox_person()},
            ],
            "description": f"{person_name} is now sitting at the kitchen island drinking coffee and looking at a phone.",
            "faces": [person_name],
        },
    ]


def scenario_family_breakfast(name_a: str, name_b: str):
    """Two people having breakfast."""
    return [
        {
            "camera": "Kitchen",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.90, "bbox": bbox_person(offset_x=-100)},
                {"label": "person", "confidence": 0.87, "bbox": bbox_person(offset_x=100)},
                {"label": "cup", "confidence": 0.68, "bbox": [480, 340, 540, 420]},
            ],
            "description": f"{name_a} and {name_b} are sitting at the kitchen counter having breakfast. Coffee mugs and plates are visible on the counter.",
            "faces": [name_a, name_b],
        },
    ]


def scenario_backyard_leisure(person_name: str):
    """Person relaxing in the backyard."""
    return [
        {
            "camera": "Backyard",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.86, "bbox": bbox_person()},
            ],
            "description": f"{person_name} is sitting on the patio reading a book. A glass of water is on the side table.",
            "faces": [person_name],
        },
    ]


def scenario_backyard_dog():
    """Dog running in backyard."""
    return [
        {
            "camera": "Backyard",
            "delay_sec": 0,
            "detections": [
                {"label": "dog", "confidence": 0.93, "bbox": bbox_animal("dog")},
            ],
            "description": "A medium-sized dog is running across the backyard lawn, chasing something near the fence.",
            "faces": None,
        },
        {
            "camera": "Backyard",
            "delay_sec": 18,
            "detections": [
                {"label": "dog", "confidence": 0.91, "bbox": bbox_animal("dog")},
            ],
            "description": "The dog has stopped near the garden bed and is sniffing the ground.",
            "faces": None,
        },
    ]


def scenario_front_door_cat():
    """Cat on front porch."""
    return [
        {
            "camera": "Front Door",
            "delay_sec": 0,
            "detections": [
                {"label": "cat", "confidence": 0.78, "bbox": bbox_animal("cat")},
            ],
            "description": "A tabby cat is sitting on the front porch railing, grooming itself in the sunlight.",
            "faces": None,
        },
    ]


def scenario_neighbor_visit(neighbor_name: str):
    """Neighbor stops by at front door."""
    return [
        {
            "camera": "Front Door",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.90, "bbox": bbox_person()},
            ],
            "description": f"{neighbor_name} is at the front door and appears to have just rung the doorbell.",
            "faces": [neighbor_name],
        },
        {
            "camera": "Front Door",
            "delay_sec": 40,
            "detections": [
                {"label": "person", "confidence": 0.88, "bbox": bbox_person(offset_x=-80)},
                {"label": "person", "confidence": 0.85, "bbox": bbox_person(offset_x=80)},
            ],
            "description": f"{neighbor_name} is having a brief conversation with someone at the open front door.",
            "faces": [neighbor_name, random.choice(["Sarah Chen", "Mike Rodriguez"])],
        },
    ]


def scenario_unknown_visitor():
    """Unknown person at front door. Creates face cluster material."""
    return [
        {
            "camera": "Front Door",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.87, "bbox": bbox_person()},
                {"label": "backpack", "confidence": 0.69, "bbox": [320, 220, 400, 350]},
            ],
            "description": "A person wearing a hooded jacket and carrying a backpack is walking up to the front door. They appear to be checking something on a phone.",
            "faces": [None],
        },
    ]


def scenario_jogger_driveway():
    """Person jogging past the driveway."""
    return [
        {
            "camera": "Driveway",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.84, "bbox": bbox_person()},
            ],
            "description": "A jogger in athletic wear is running past the end of the driveway. Wearing headphones.",
            "faces": None,
        },
    ]


def scenario_kids_driveway():
    """Children playing in the driveway."""
    return [
        {
            "camera": "Driveway",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.82, "bbox": bbox_person(offset_x=-120)},
                {"label": "person", "confidence": 0.79, "bbox": bbox_person(offset_x=120)},
                {"label": "bicycle", "confidence": 0.81, "bbox": [350, 380, 510, 560]},
            ],
            "description": "Two children are riding bicycles in the driveway. Chalk drawings are visible on the pavement.",
            "faces": None,
        },
    ]


def scenario_backyard_bird():
    """Bird at feeder."""
    return [
        {
            "camera": "Backyard",
            "delay_sec": 0,
            "detections": [
                {"label": "bird", "confidence": 0.71, "bbox": bbox_animal("bird")},
            ],
            "description": "A small songbird has landed on the bird feeder near the garden fence.",
            "faces": None,
        },
    ]


def scenario_garage_workbench(person_name: str):
    """Person doing work in the garage."""
    return [
        {
            "camera": "Garage",
            "delay_sec": 0,
            "detections": [
                {"label": "person", "confidence": 0.86, "bbox": bbox_person()},
            ],
            "description": f"{person_name} is at the garage workbench, sorting through tools. Several boxes are stacked nearby.",
            "faces": [person_name],
        },
    ]


# ── Scenario pool with weights and valid hour windows ──
# (scenario_fn_or_name, weight, valid_hours_set)
# Hours are local 24h range when this scenario plausibly happens.

MORNING_HOURS = set(range(6, 11))     # 6 AM - 10:59 AM
DAYTIME_HOURS = set(range(8, 18))     # 8 AM - 5:59 PM
EVENING_HOURS = set(range(16, 22))    # 4 PM - 9:59 PM
DAY_ALL_HOURS = set(range(6, 23))     # 6 AM - 10:59 PM
ANY_HOUR = set(range(0, 24))


def pick_scenario(hour: int) -> list[dict]:
    """Pick a random scenario appropriate for the given hour."""
    # (scenario_factory, weight, valid_hours)
    pool = [
        (scenario_delivery_driveway_then_door, 4, DAYTIME_HOURS),
        (lambda: scenario_family_arrival_evening(random.choice(["Sarah Chen", "Mike Rodriguez"])), 5, EVENING_HOURS),
        (lambda: scenario_family_morning(random.choice(["Sarah Chen", "Mike Rodriguez", "Emma Wilson"])), 4, MORNING_HOURS),
        (lambda: scenario_family_breakfast("Sarah Chen", "Mike Rodriguez"), 2, MORNING_HOURS),
        (lambda: scenario_backyard_leisure(random.choice(["Sarah Chen", "Emma Wilson"])), 3, DAYTIME_HOURS),
        (scenario_backyard_dog, 4, DAY_ALL_HOURS),
        (scenario_front_door_cat, 3, DAY_ALL_HOURS),
        (lambda: scenario_neighbor_visit("James Park"), 2, DAYTIME_HOURS),
        (scenario_unknown_visitor, 3, DAY_ALL_HOURS),
        (scenario_jogger_driveway, 3, DAY_ALL_HOURS),
        (scenario_kids_driveway, 2, DAYTIME_HOURS),
        (scenario_backyard_bird, 3, DAY_ALL_HOURS),
        (lambda: scenario_garage_workbench(random.choice(["Mike Rodriguez", "Sarah Chen"])), 2, DAYTIME_HOURS),
    ]
    eligible = [(fn, w) for fn, w, hrs in pool if hour in hrs]
    if not eligible:
        return []
    fns, weights = zip(*eligible)
    fn = random.choices(fns, weights=weights, k=1)[0]
    return fn()


def weighted_time_in_last_hours(hours_back: int) -> datetime:
    """Pick a random time in the last N hours, weighted by daily activity pattern."""
    # Build hour weights. quiet overnight, peaks morning/evening
    hour_weights = {
        0: 0.2, 1: 0.1, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.2,
        6: 0.5, 7: 1.5, 8: 2.5, 9: 2.0, 10: 1.5, 11: 1.3,
        12: 1.5, 13: 1.3, 14: 1.3, 15: 1.5, 16: 2.0, 17: 2.5,
        18: 2.5, 19: 2.3, 20: 1.8, 21: 1.3, 22: 0.8, 23: 0.4,
    }
    # Try up to 10 times to pick a time whose hour matches the weight distribution
    for _ in range(20):
        minutes_back = random.uniform(0, hours_back * 60)
        t = NOW - timedelta(minutes=minutes_back)
        local_hour = t.hour  # UTC hour. good enough for demo
        accept_prob = hour_weights[local_hour] / 2.5
        if random.random() < accept_prob:
            return t
    # Fallback
    return NOW - timedelta(minutes=random.uniform(0, hours_back * 60))


def person_detections_from_faces(face_names: list[str | None], obj_detections: list[dict], person_id_map: dict[str, uuid.UUID]) -> dict | None:
    """Build a person_detections JSON blob from face labels.

    Attaches faces to person bboxes from object detections where available.
    """
    if not face_names:
        return None
    person_boxes = [d["bbox"] for d in obj_detections if d["label"] == "person"]
    faces = []
    for i, name in enumerate(face_names):
        # Anchor face to nth person bbox if available
        if i < len(person_boxes):
            face_bbox = bbox_face(person_boxes[i])
        else:
            face_bbox = [300, 100, 400, 200]
        if name is None:
            faces.append({
                "bbox": face_bbox,
                "person_id": None,
                "person_name": None,
                "match_distance": None,
            })
        else:
            faces.append({
                "bbox": face_bbox,
                "person_id": str(person_id_map.get(name)) if person_id_map.get(name) else None,
                "person_name": name,
                "match_distance": round(random.uniform(0.15, 0.38), 3),
            })
    return {"faces": faces, "count": len(faces)}


async def seed():
    """Populate database with demo data."""
    clean = "--clean" in sys.argv

    async with async_session() as db:
        if clean:
            print("Cleaning existing data.")
            for table in [
                Event, DigestEntry, Observation, Incident, Journey,
                CameraStatusLog, FaceClusterSample, FaceCluster,
                Person, Rule, Camera,
            ]:
                await db.execute(delete(table))
            await db.commit()
            print("Done cleaning.")

        # ── 1. Cameras ──
        print("Creating cameras.")
        camera_ids: dict[str, uuid.UUID] = {}
        camera_objs: dict[str, Camera] = {}
        for cam_def in CAMERAS:
            cam = Camera(**cam_def)
            db.add(cam)
            await db.flush()
            camera_ids[cam_def["name"]] = cam.id
            camera_objs[cam_def["name"]] = cam
        await db.commit()
        print(f"  Created {len(camera_ids)} cameras.")

        # ── 2. Persons ──
        print("Creating household members.")
        person_ids: dict[str, uuid.UUID] = {}
        for p_def in PERSONS:
            p = Person(**p_def)
            db.add(p)
            await db.flush()
            person_ids[p_def["display_name"]] = p.id
        await db.commit()
        print(f"  Created {len(person_ids)} persons.")

        # ── 3. Face clusters (pending unknown people suggestions) ──
        print("Creating face cluster suggestions.")
        fake_embedding_512 = [random.uniform(-0.1, 0.1) for _ in range(512)]
        clusters_created = 0
        cluster_cameras = ["Front Door", "Backyard"]
        for cam_name in cluster_cameras:
            cam_id = camera_ids[cam_name]
            sightings = random.randint(4, 9)
            first_seen = weighted_time_in_last_hours(60)
            last_seen = weighted_time_in_last_hours(3)
            cluster = FaceCluster(
                representative_embedding=fake_embedding_512,
                sighting_count=sightings,
                first_seen_at=min(first_seen, last_seen),
                last_seen_at=max(first_seen, last_seen),
                first_camera_id=cam_id,
                status="pending",
            )
            db.add(cluster)
            await db.flush()
            clusters_created += 1

            for _ in range(min(sightings, 5)):
                sample = FaceClusterSample(
                    cluster_id=cluster.id,
                    camera_id=cam_id,
                    embedding=fake_embedding_512,
                    captured_at=weighted_time_in_last_hours(48),
                )
                db.add(sample)

        await db.commit()
        print(f"  Created {clusters_created} unknown face clusters.")

        # ── 4. Scenario-based observations ──
        print("Generating scenarios.")
        obs_count = 0
        scenario_count = 0

        # Generate about 40-60 scenarios across the last 48 hours
        num_scenarios = random.randint(40, 60)
        for _ in range(num_scenarios):
            base_time = weighted_time_in_last_hours(48)
            hour = base_time.hour
            steps = pick_scenario(hour)
            if not steps:
                continue
            scenario_count += 1

            for step in steps:
                cam_id = camera_ids[step["camera"]]
                started = base_time + timedelta(seconds=step["delay_sec"])
                ended = started + timedelta(seconds=random.randint(3, 12))

                detections = step["detections"]
                obj_det = {
                    "objects": detections,
                    "count": len(detections),
                } if detections else None

                person_det = person_detections_from_faces(
                    step.get("faces") or [],
                    detections,
                    person_ids,
                )

                obs = Observation(
                    camera_id=cam_id,
                    started_at=started,
                    ended_at=ended,
                    object_detections=obj_det,
                    person_detections=person_det,
                    vlm_description=step["description"],
                    vlm_provider=VLM_PROVIDER,
                    confidence=round(random.uniform(0.75, 0.94), 2),
                )
                db.add(obs)
                obs_count += 1

        await db.commit()
        print(f"  Created {scenario_count} scenarios, {obs_count} observations.")

        # ── 4b. Deterministic anchor scenarios ──
        # Guarantee the journeys the smoke assertions rely on exist
        # regardless of the random draw. Times are anchored to NOW so
        # "today" / "this morning" questions land.
        print("Adding deterministic anchor scenarios.")
        person_obj = [{"label": "person", "confidence": 0.92, "bbox": bbox_person()}]

        def _anchor_obs(cam_name, started, faces, objs, desc):
            pd = person_detections_from_faces(faces, objs, person_ids)
            o = Observation(
                camera_id=camera_ids[cam_name],
                started_at=started,
                ended_at=started + timedelta(seconds=8),
                object_detections={"objects": objs, "count": len(objs)} if objs else None,
                person_detections=pd,
                vlm_description=desc,
                vlm_provider=VLM_PROVIDER,
                confidence=0.9,
            )
            db.add(o)

        # Co-presence. Mom and Dad together in the Kitchen this morning.
        morn = NOW - timedelta(hours=5)
        _anchor_obs("Kitchen", morn, ["Sarah Chen", "Mike Rodriguez"], person_obj,
                    "Two people at the kitchen table having breakfast together.")
        _anchor_obs("Kitchen", morn + timedelta(seconds=45), ["Sarah Chen", "Mike Rodriguez"], person_obj,
                    "Two people talking in the kitchen.")
        # Cross-camera journey. Mom enters the Front Door then the Kitchen.
        aft = NOW - timedelta(hours=3)
        _anchor_obs("Front Door", aft, ["Sarah Chen"], person_obj,
                    "A woman unlocks and enters through the front door.")
        _anchor_obs("Kitchen", aft + timedelta(minutes=2), ["Sarah Chen"], person_obj,
                    "A woman sets groceries on the kitchen counter.")
        await db.commit()

        # ── 4c. Build incidents + journeys via the production path ──
        # Replay every observation, in time order, through the real
        # incident_tracker. It writes Incident rows and stitches them
        # into Journeys with exactly the subject_key/segments shapes the
        # agent tools read. Time order matters for cross-camera stitching.
        print("Building incidents and journeys via prod aggregation.")
        cams = (await db.execute(select(Camera))).scalars().all()
        cam_by_id = {c.id: c for c in cams}
        obs_rows = (
            await db.execute(
                select(Observation).order_by(Observation.started_at.asc())
            )
        ).scalars().all()
        for o in obs_rows:
            cam = cam_by_id.get(o.camera_id)
            if cam is None:
                continue
            iid = await assign_incident(db, cam, o)
            if iid is not None:
                o.incident_id = iid
        await db.commit()
        journeys = (await db.execute(select(Journey))).scalars().all()
        person_journeys = [j for j in journeys if j.subject_kind == "person"]
        print(
            f"  Built {len(journeys)} journeys "
            f"({len(person_journeys)} person), from {len(obs_rows)} observations."
        )

        # ── 5. Camera status logs ──
        print("Creating status logs.")
        log_count = 0
        # Kitchen had a brief outage earlier today
        outage_start = NOW - timedelta(hours=8)
        outage_end = NOW - timedelta(hours=7, minutes=42)
        db.add(CameraStatusLog(
            camera_id=camera_ids["Kitchen"],
            status="offline",
            previous_status="live",
            reason="Stream disconnected",
            timestamp=outage_start,
        ))
        db.add(CameraStatusLog(
            camera_id=camera_ids["Kitchen"],
            status="live",
            previous_status="offline",
            reason="Stream reconnected",
            timestamp=outage_end,
        ))
        log_count += 2

        # Garage started recording when a car was detected
        for _ in range(3):
            t = weighted_time_in_last_hours(24)
            db.add(CameraStatusLog(
                camera_id=camera_ids["Garage"],
                status="recording",
                previous_status="live",
                reason="Object trigger fired",
                timestamp=t,
            ))
            log_count += 1

        # All cameras came online at startup
        startup = NOW - timedelta(hours=47, minutes=50)
        for cam_name, cam_id in camera_ids.items():
            db.add(CameraStatusLog(
                camera_id=cam_id,
                status="live",
                previous_status=None,
                reason="Stream connected",
                timestamp=startup + timedelta(seconds=random.randint(0, 60)),
            ))
            log_count += 1

        await db.commit()
        print(f"  Created {log_count} status logs.")

        # ── 6. Rules and events ──
        print("Creating rules and events.")
        rule1 = Rule(
            name="Unknown face at front door",
            enabled=True,
            trigger_pattern={"type": "face_unknown"},
            conditions={"camera_ids": [str(camera_ids["Front Door"])]},
            actions={"type": "notify", "severity": "warning", "message": "Unknown person at front door"},
            cooldown_seconds=180,
        )
        rule2 = Rule(
            name="Vehicle in driveway",
            enabled=True,
            trigger_pattern={"type": "object_detected", "labels": ["car", "truck"]},
            conditions={"camera_ids": [str(camera_ids["Driveway"])]},
            actions={"type": "notify", "severity": "info", "message": "Vehicle detected in driveway"},
            cooldown_seconds=300,
        )
        rule3 = Rule(
            name="Garage opened after hours",
            enabled=False,
            trigger_pattern={"type": "object_detected", "labels": ["person"]},
            conditions={"camera_ids": [str(camera_ids["Garage"])], "time_after": "22:00", "time_before": "06:00"},
            actions={"type": "notify", "severity": "critical", "message": "Person in garage after hours"},
            cooldown_seconds=120,
        )
        db.add_all([rule1, rule2, rule3])
        await db.flush()

        event_count = 0
        # Rule 1. fires when unknown person shown up (3-5 times)
        for _ in range(random.randint(3, 5)):
            ev = Event(
                rule_id=rule1.id,
                fired_at=weighted_time_in_last_hours(40),
                payload={"rule_name": rule1.name, "camera": "Front Door"},
                action_status="delivered",
                action_type="notify",
            )
            db.add(ev)
            event_count += 1
        # Rule 2. fires for delivery and family arrivals (6-10 times)
        for _ in range(random.randint(6, 10)):
            ev = Event(
                rule_id=rule2.id,
                fired_at=weighted_time_in_last_hours(40),
                payload={"rule_name": rule2.name, "camera": "Driveway"},
                action_status="delivered",
                action_type="notify",
            )
            db.add(ev)
            event_count += 1

        await db.commit()
        print(f"  Created 3 rules and {event_count} events.")

        # ── 7. Digest entries ──
        print("Creating digest entries.")
        digests = [
            {
                "camera": "Front Door",
                "period": "24h",
                "summary": (
                    "The front door saw steady foot traffic over the last 24 hours. "
                    "A FedEx delivery arrived in the early afternoon, and James Park "
                    "stopped by around 6 PM. One unknown visitor was captured in the "
                    "late morning and has been added to the review queue."
                ),
                "highlights": [
                    "FedEx delivery at 2:14 PM",
                    "Neighbor James Park visited at 6:08 PM",
                    "1 unknown visitor flagged for review",
                    "14 total motion events captured",
                ],
                "stats": {"person_detections": 12, "vehicle_detections": 1, "unknown_faces": 1, "avg_confidence": 0.87},
                "total": 14,
            },
            {
                "camera": "Backyard",
                "period": "24h",
                "summary": (
                    "Backyard activity was light. Sarah spent about 40 minutes reading "
                    "on the patio in the afternoon. The resident dog was active near "
                    "the garden bed in the morning and evening. Multiple bird visits "
                    "to the feeder were recorded throughout the day."
                ),
                "highlights": [
                    "Sarah Chen relaxed on patio for ~40 minutes",
                    "Dog activity in morning and evening",
                    "7 bird feeder visits logged",
                    "No unexpected human activity",
                ],
                "stats": {"person_detections": 4, "animal_detections": 11, "avg_confidence": 0.82},
                "total": 15,
            },
            {
                "camera": "Driveway",
                "period": "6h",
                "summary": (
                    "Two vehicles arrived in the last 6 hours. Mike came home in the "
                    "sedan at 6:22 PM. A FedEx truck delivered a package shortly after. "
                    "A jogger passed through the frame briefly around 7 PM."
                ),
                "highlights": [
                    "Mike Rodriguez arrived home at 6:22 PM",
                    "FedEx delivery at 7:08 PM",
                    "1 jogger captured",
                    "License plates read. 7ABC 123, FDX 4521",
                ],
                "stats": {"vehicle_detections": 2, "person_detections": 3, "plates_read": 2, "avg_confidence": 0.90},
                "total": 9,
            },
        ]
        for d in digests:
            db.add(DigestEntry(
                camera_id=camera_ids[d["camera"]],
                period=d["period"],
                summary=d["summary"],
                highlights=d["highlights"],
                stats=d["stats"],
                total_observations=d["total"],
                generated_at=NOW - timedelta(hours=random.uniform(0.5, 2.0)),
            ))
        await db.commit()
        print(f"  Created {len(digests)} digest entries.")

        print("\nSeed complete.")
        print(f"  Cameras       {len(camera_ids)}")
        print(f"  Persons       {len(person_ids)}")
        print(f"  Clusters      {clusters_created}")
        print(f"  Scenarios     {scenario_count}")
        print(f"  Observations  {obs_count}")
        print(f"  Status logs   {log_count}")
        print(f"  Events        {event_count}")
        print(f"  Digests       {len(digests)}")


if __name__ == "__main__":
    asyncio.run(seed())
