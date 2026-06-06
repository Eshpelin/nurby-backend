"""Ground vehicle descriptions against their real frame, robustly. Uses a
fresh DB session per vehicle so a slow VLM call can never leave a session
open long enough for the connection to drop. Run in the api container."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request

from sqlalchemy import select

from shared.database import async_session
from shared.models import Vehicle

OLLAMA = "http://ollama:11434"
MODEL = "gemma3:4b"


def vlm(path: str, prompt: str) -> str:
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode()
    payload = json.dumps({"model": MODEL, "prompt": prompt, "images": [b],
                          "stream": False, "options": {"num_predict": 24}}).encode()
    last = ""
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["response"].strip()
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:50]}"
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(last)


def titlecase(s: str) -> str:
    return " ".join(w.upper() if w.lower() == "suv" else w.capitalize() for w in s.split())


async def main():
    async with async_session() as db:
        ids = [v.id for v in (await db.execute(select(Vehicle).order_by(Vehicle.created_at))).scalars()]

    for vid in ids:
        # read
        async with async_session() as db:
            v = await db.get(Vehicle, vid)
            path, plate = v.photo_path, v.license_plate
        if not path or not os.path.exists(path):
            print(f"[veh] {plate}: no photo", flush=True)
            continue
        # VLM (no session held)
        try:
            ans = vlm(path, "Security camera frame. If a vehicle is visible reply "
                            "'<color> <type>' like 'white SUV' or 'red sedan'. If none, 'none'."
                      ).lower().strip().strip(".")
        except Exception as e:
            print(f"[veh] {plate}: SKIP {e}", flush=True)
            continue
        # write (fresh session)
        async with async_session() as db:
            v = await db.get(Vehicle, vid)
            if "none" in ans or len(ans) > 36 or not ans:
                v.description = "Vehicle sighting captured on a camera."
                v.display_name = f"Plate {plate}" if plate else "Vehicle"
                print(f"[veh] {plate}: no clear vehicle -> generic", flush=True)
            else:
                words = ans.split()
                if words:
                    v.color = words[0]
                if len(words) > 1:
                    v.vehicle_type = words[-1]
                v.make = v.model = None
                v.display_name = titlecase(ans)
                v.description = f"{titlecase(ans)} captured on a camera, identified by plate {plate}."
                print(f"[veh] {plate}: {ans} -> {v.display_name}", flush=True)
            await db.commit()
    print("[done]", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
