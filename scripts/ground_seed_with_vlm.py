"""(1) Wire the pulled gemma3:4b as a real VLM Provider and assign it to
every camera, and (2) ground the seeded people/vehicles against their
ACTUAL images using the vision model, so a card never claims a silver
Sprinter over a white SUV or a female name over a male face.

Run in the api container. The captioning is CPU-slow (~1-2 min/image),
so this takes a while. Run it in the background.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.request

from sqlalchemy import select

from shared.database import async_session
from shared.models import Camera, Person, Vehicle

OLLAMA = "http://ollama:11434"
MODEL = "gemma3:4b"

MALE = ["Mike Rivera", "David Park", "James Carter", "Daniel Osei", "Tom Walsh"]
FEMALE = ["Sarah Chen", "Emma Doyle", "Olivia Brooks", "Priya Nair", "Grace Kim"]
REL = ["Family", "Family", "Neighbor", "Family", "Neighbor"]


import time


def vlm(path: str, prompt: str) -> str:
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode()
    payload = json.dumps({"model": MODEL, "prompt": prompt, "images": [b],
                          "stream": False, "options": {"num_predict": 40}}).encode()
    last = ""
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                f"{OLLAMA}/api/generate", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["response"].strip()
        except Exception as e:  # 500s under load, transient
            last = f"{type(e).__name__}: {str(e)[:80]}"
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"vlm failed after retries: {last}")


async def main():
    async with async_session() as db:
        # ---- (1) wire provider ------------------------------------------
        from shared.models import Provider
        prov = (await db.execute(select(Provider).where(Provider.kind == "ollama"))).scalars().first()
        if prov is None:
            prov = Provider(name="Gemma 3 4B (local)", kind="ollama",
                            base_url=OLLAMA, default_model=MODEL, active=True,
                            max_input_tokens=4096, max_output_tokens=512)
            db.add(prov)
            await db.flush()
        cams = list((await db.execute(select(Camera))).scalars())
        for c in cams:
            c.vlm_provider_id = prov.id
            c.digest_provider_id = prov.id
            c.summary_provider_id = prov.id
        await db.commit()
        print(f"[provider] wired '{prov.name}' to {len(cams)} cameras", flush=True)

        # ---- (2) ground people by apparent gender -----------------------
        people = list((await db.execute(select(Person).order_by(Person.created_at))).scalars())
        # Clear leftover names to temporary-unique values so reassignment
        # never collides with a name still held by another row mid-pass.
        for p in people:
            p.display_name = f"tmp-{p.id}"
        await db.commit()
        used = set()  # lowercased names already taken (unique constraint)
        unk = 0

        def pick(pool):
            for nm in pool:
                if nm.lower() not in used:
                    used.add(nm.lower())
                    return nm
            return None

        ri = 0
        for p in people:
            if not p.photo_path or not os.path.exists(p.photo_path):
                continue
            try:
                g = vlm(p.photo_path,
                        "This is a cropped face from a security camera. Reply with ONE word "
                        "only: male, female, or unclear.").lower()
                desc = vlm(p.photo_path,
                           "Describe this person from the security camera crop in one short "
                           "sentence (hair, clothing, anything visible). Do not guess a name.")
            except Exception as e:
                print(f"[person] SKIP {p.id}: {e}", flush=True)
                continue
            name = pick(FEMALE) if "female" in g else pick(MALE) if "male" in g else None
            if not name:
                unk += 1
                name = "Unknown person" if unk == 1 else f"Unknown person {unk}"
                rel = None
            else:
                rel = REL[ri % len(REL)]; ri += 1
            p.display_name = name
            p.nickname = name.split()[0] if name != "Unknown person" else None
            p.relationship = rel
            p.recap_cached_status = desc[:300]
            await db.commit()
            print(f"[person] {g!r} -> {name} :: {desc[:70]}", flush=True)

        # ---- (2) ground vehicles by actual frame ------------------------
        vehicles = list((await db.execute(select(Vehicle).order_by(Vehicle.created_at))).scalars())
        for v in vehicles:
            if not v.photo_path or not os.path.exists(v.photo_path):
                continue
            try:
                ans = vlm(v.photo_path,
                          "This is a security camera frame. If a vehicle is visible, reply as "
                          "'<color> <type>' (e.g. 'white SUV', 'red sedan', 'silver van'). "
                          "If no vehicle is clearly visible reply 'none'.").lower().strip().strip(".")
            except Exception as e:
                print(f"[vehicle] SKIP {v.license_plate}: {e}", flush=True)
                continue
            if "none" in ans or len(ans) > 40 or not ans:
                v.description = "Vehicle sighting captured on a camera."
                print(f"[vehicle] {v.license_plate}: no clear vehicle -> generic", flush=True)
            else:
                words = ans.split()
                if words:
                    v.color = words[0]
                if len(words) > 1:
                    v.vehicle_type = words[-1]
                v.display_name = ans.title()
                v.make = v.model = None
                v.description = (f"{ans.capitalize()} captured on a camera, "
                                 f"identified by plate {v.license_plate}.")
                print(f"[vehicle] {v.license_plate}: {ans} -> {v.display_name}", flush=True)
            await db.commit()
        print("[done] grounded", len(people), "people and", len(vehicles), "vehicles", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
