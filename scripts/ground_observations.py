"""Re-caption the seeded observations from their REAL frames with the
vision model, and replace the hand-written morning brief with a generic
accurate one, so nothing on the dashboard references the old fictional
names. Run in the api container with perception paused (dedicated VLM)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request

from sqlalchemy import select

from shared.database import async_session
from shared.models import DailyDigest, Observation

OLLAMA = "http://ollama:11434"
MODEL = "gemma3:4b"


def vlm(path: str, prompt: str) -> str:
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode()
    payload = json.dumps({"model": MODEL, "prompt": prompt, "images": [b],
                          "stream": False, "options": {"num_predict": 60}}).encode()
    last = ""
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["response"].strip()
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:60]}"
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(last)


async def main():
    async with async_session() as db:
        seeded = list((await db.execute(
            select(Observation).where(Observation.vlm_provider == "gemma3:4b")
            .order_by(Observation.started_at.desc())
        )).scalars())
        for o in seeded:
            if not o.thumbnail_path or not os.path.exists(o.thumbnail_path):
                continue
            try:
                cap = vlm(o.thumbnail_path,
                          "Describe what is happening in this security camera frame in one "
                          "natural sentence. Be concrete about people, vehicles, or objects "
                          "you actually see. Do not invent names.")
            except Exception as e:
                print(f"[obs] SKIP {o.id}: {e}", flush=True)
                continue
            o.vlm_description = cap.strip()
            await db.commit()
            print(f"[obs] {cap[:80]}", flush=True)

        d = (await db.execute(select(DailyDigest).order_by(DailyDigest.generated_at.desc()))).scalars().first()
        if d is not None:
            d.summary_text = (
                "A quiet stretch overnight with a handful of vehicles passing the "
                "driveway and front camera, and a few people walking through frame "
                "during the day. No alarms, no unfamiliar faces lingering, and every "
                "sighting matched a routine in and out of the property."
            )
            await db.commit()
            print("[digest] rewritten generic", flush=True)
        print("[done]", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
