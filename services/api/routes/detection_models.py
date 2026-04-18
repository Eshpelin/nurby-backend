"""Read class names from Ultralytics detection models so the UI can
show a dynamic label picker tied to whichever model the camera uses.

Loaded models are cached via the shared ObjectDetector cache, so
asking for class names does not re-download weights."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from shared.auth import get_current_user
from shared.models import User

logger = logging.getLogger("nurby.api.detection_models")

router = APIRouter()

# Module-level name cache keyed by model filename. {model: [class_name, ...]}.
_NAMES_CACHE: dict[str, list[str]] = {}


def _load_names(model_name: str) -> list[str]:
    """Return sorted class names for a given ultralytics model name."""
    if model_name in _NAMES_CACHE:
        return _NAMES_CACHE[model_name]

    try:
        from ultralytics import YOLO
        model = YOLO(model_name)
        names_dict: dict[int, str] | Any = getattr(model, "names", {}) or {}
        if isinstance(names_dict, dict):
            names = list(names_dict.values())
        else:
            names = list(names_dict)
        names = sorted({str(n).strip() for n in names if n})
        _NAMES_CACHE[model_name] = names
        logger.info("Loaded %d classes from model '%s'", len(names), model_name)
        return names
    except Exception as exc:
        logger.warning("Failed to load classes for '%s'. %s", model_name, exc)
        return []


@router.get("/classes")
async def get_classes(
    model: list[str] = Query(default_factory=list),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the union of class names across the requested models.

    Response shape. {"classes": ["apple", "backpack", ...],
                     "per_model": {"yolov8n.pt": [...], ...}}.
    """
    if not model:
        return {"classes": [], "per_model": {}}

    per_model: dict[str, list[str]] = {}
    union: set[str] = set()
    loop = asyncio.get_event_loop()
    for m in model:
        # Run in thread pool so first-run model downloads do not block the loop.
        names = await loop.run_in_executor(None, _load_names, m)
        per_model[m] = names
        union.update(names)

    return {
        "classes": sorted(union),
        "per_model": per_model,
    }
