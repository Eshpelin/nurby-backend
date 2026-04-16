"""One-click local AI deployment via Ollama.

Handles checking Ollama status, pulling vision models, and
auto-creating a Provider record so users don't need to configure
anything manually.
"""

import asyncio
import logging
import platform
import shutil

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import require_admin
from shared.database import get_db
from shared.models import Provider, User

logger = logging.getLogger("nurby.ollama_deploy")

router = APIRouter()

OLLAMA_URL = "http://localhost:11434"

# Curated list of vision-capable models with RAM requirements.
# Ollama has no public catalog API, so we maintain this list.
# Sorted by quality descending within each family.
VISION_MODELS = [
    # Gemma 3 (Google, all sizes support vision)
    {"name": "gemma3:27b", "label": "Gemma 3 27B", "family": "Gemma", "ram_gb": 20, "quality": "best", "vision": True, "description": "Highest quality vision model from Google"},
    {"name": "gemma3:12b", "label": "Gemma 3 12B", "family": "Gemma", "ram_gb": 10, "quality": "great", "vision": True, "description": "Great balance of quality and speed"},
    {"name": "gemma3:4b", "label": "Gemma 3 4B", "family": "Gemma", "ram_gb": 4, "quality": "good", "vision": True, "description": "Good quality, runs on most machines"},
    {"name": "gemma3:1b", "label": "Gemma 3 1B", "family": "Gemma", "ram_gb": 2, "quality": "fast", "vision": True, "description": "Ultra-light, works on low-end hardware"},
    # LLaVA (vision-language model)
    {"name": "llava:34b", "label": "LLaVA 34B", "family": "LLaVA", "ram_gb": 24, "quality": "best", "vision": True, "description": "Top-tier vision understanding"},
    {"name": "llava:13b", "label": "LLaVA 13B", "family": "LLaVA", "ram_gb": 10, "quality": "great", "vision": True, "description": "Strong vision model, well-tested"},
    {"name": "llava:7b", "label": "LLaVA 7B", "family": "LLaVA", "ram_gb": 5, "quality": "good", "vision": True, "description": "Proven vision model, moderate resources"},
    # LLaVA-Llama3
    {"name": "llava-llama3", "label": "LLaVA-Llama3 8B", "family": "LLaVA", "ram_gb": 6, "quality": "good", "vision": True, "description": "LLaVA fine-tuned on Llama 3"},
    # BakLLaVA
    {"name": "bakllava", "label": "BakLLaVA 7B", "family": "LLaVA", "ram_gb": 5, "quality": "good", "vision": True, "description": "Mistral-based vision model"},
    # Moondream (tiny, edge-optimized)
    {"name": "moondream", "label": "Moondream 1.8B", "family": "Moondream", "ram_gb": 2, "quality": "fast", "vision": True, "description": "Tiny vision model for edge devices"},
    # Llama 3.2 Vision
    {"name": "llama3.2-vision:11b", "label": "Llama 3.2 Vision 11B", "family": "Llama", "ram_gb": 8, "quality": "great", "vision": True, "description": "Meta's multimodal Llama with vision"},
    {"name": "llama3.2-vision:90b", "label": "Llama 3.2 Vision 90B", "family": "Llama", "ram_gb": 55, "quality": "best", "vision": True, "description": "Largest Llama vision model"},
    # MiniCPM-V
    {"name": "minicpm-v", "label": "MiniCPM-V 8B", "family": "MiniCPM", "ram_gb": 6, "quality": "good", "vision": True, "description": "Compact vision model from OpenBMB"},
]


class OllamaStatus(BaseModel):
    installed: bool
    running: bool
    models: list[str]
    recommended_model: str | None
    system_ram_gb: float | None
    available_models: list[dict]


class DeployRequest(BaseModel):
    model: str = "gemma3:4b"


class DeployStatus(BaseModel):
    stage: str  # checking, installing, pulling, registering, done, error
    message: str
    progress: float | None = None  # 0-100 for pull progress


def _get_system_ram_gb() -> float | None:
    """Get total system RAM in GB."""
    try:
        import os
        if platform.system() == "Darwin":
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            return int(result.stdout.strip()) / (1024 ** 3)
        else:
            mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return mem_bytes / (1024 ** 3)
    except Exception:
        return None


def _recommend_model(ram_gb: float | None) -> str:
    """Pick the best model that fits in available RAM."""
    if ram_gb is None:
        return "gemma3:4b"  # safe default
    for model in VISION_MODELS:
        if ram_gb >= model["ram_gb"] * 1.5:  # leave headroom
            return model["name"]
    return "gemma3:1b"


async def _is_ollama_running() -> bool:
    """Check if Ollama API is responding."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def _get_installed_models() -> list[str]:
    """Get list of models installed in Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []


@router.get("/status", response_model=OllamaStatus)
async def get_ollama_status(_current_user: User = Depends(require_admin)):
    """Check Ollama installation status and recommend a model."""
    installed = shutil.which("ollama") is not None
    running = await _is_ollama_running()
    models = await _get_installed_models() if running else []
    ram_gb = _get_system_ram_gb()
    recommended = _recommend_model(ram_gb)

    return OllamaStatus(
        installed=installed,
        running=running,
        models=models,
        recommended_model=recommended,
        system_ram_gb=round(ram_gb, 1) if ram_gb else None,
        available_models=VISION_MODELS,
    )


@router.post("/deploy", response_model=DeployStatus)
async def deploy_model(
    body: DeployRequest,
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deploy a vision model via Ollama and auto-register as provider.

    This endpoint orchestrates the full flow. check Ollama, start it
    if needed, pull the model, and create a Provider record.
    """
    model_name = body.model

    # Validate model name format (allow any model, not just curated list)
    if not model_name or "/" in model_name or ".." in model_name:
        return DeployStatus(stage="error", message="Invalid model name")

    # Step 1. Check if Ollama is installed
    if not shutil.which("ollama"):
        return DeployStatus(
            stage="error",
            message="Ollama is not installed. Install it from https://ollama.com/download then try again.",
        )

    # Step 2. Start Ollama if not running
    if not await _is_ollama_running():
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "serve",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Give it a moment to start
            for _ in range(10):
                await asyncio.sleep(1)
                if await _is_ollama_running():
                    break
            else:
                return DeployStatus(stage="error", message="Ollama started but API not responding after 10 seconds")
        except Exception as exc:
            return DeployStatus(stage="error", message=f"Failed to start Ollama. {str(exc)}")

    # Step 3. Check if model already pulled
    installed = await _get_installed_models()
    if model_name not in installed and not any(m.startswith(model_name.split(":")[0]) for m in installed if ":" in model_name):
        # Pull the model
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "pull", model_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=600,  # 10 min timeout for large models
            )
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "Unknown error"
                return DeployStatus(stage="error", message=f"Failed to pull {model_name}. {error_msg}")
        except asyncio.TimeoutError:
            return DeployStatus(stage="error", message=f"Model pull timed out after 10 minutes. Try running 'ollama pull {model_name}' manually.")
        except Exception as exc:
            return DeployStatus(stage="error", message=f"Pull failed. {str(exc)}")

    # Step 4. Check if provider already exists
    result = await db.execute(
        select(Provider).where(Provider.kind == "ollama", Provider.base_url == OLLAMA_URL)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update the model if different
        if existing.default_model != model_name:
            existing.default_model = model_name
            existing.active = True
            await db.commit()
        return DeployStatus(
            stage="done",
            message=f"{model_name} is ready. Updated existing Ollama provider.",
        )

    # Step 5. Create provider record
    provider = Provider(
        name=f"Ollama ({model_name})",
        kind="ollama",
        base_url=OLLAMA_URL,
        api_key=None,
        default_model=model_name,
        active=True,
    )
    db.add(provider)
    await db.commit()

    return DeployStatus(
        stage="done",
        message=f"{model_name} is ready. Provider auto-configured.",
    )
