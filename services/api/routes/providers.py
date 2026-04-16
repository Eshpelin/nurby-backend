import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Provider
from shared.schemas import ProviderCreate, ProviderResponse


class ProviderTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    models: list[str] | None = None

router = APIRouter()


@router.get("", response_model=list[ProviderResponse])
async def list_providers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Provider).order_by(Provider.created_at))
    return result.scalars().all()


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(body: ProviderCreate, db: AsyncSession = Depends(get_db)):
    provider = Provider(**body.model_dump())
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderCreate,
    db: AsyncSession = Depends(get_db),
):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(provider, field, value)

    await db.commit()
    await db.refresh(provider)
    return provider


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Test connectivity to a VLM provider by hitting its API."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider.kind == "openai":
                resp = await client.get(
                    f"{provider.base_url}/v1/models",
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                )
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    model_ids = [m["id"] for m in data.get("data", [])[:10]]
                    return ProviderTestResult(
                        ok=True,
                        message=f"Connected. {len(data.get('data', []))} models available",
                        latency_ms=latency,
                        models=model_ids,
                    )
                return ProviderTestResult(
                    ok=False,
                    message=f"API returned {resp.status_code}. {resp.text[:200]}",
                    latency_ms=latency,
                )

            elif provider.kind == "anthropic":
                # Anthropic has no /models list. Send a minimal request.
                resp = await client.post(
                    f"{provider.base_url}/v1/messages",
                    headers={
                        "x-api-key": provider.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": provider.default_model or "claude-sonnet-4-20250514",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    return ProviderTestResult(
                        ok=True,
                        message="Connected. API key valid",
                        latency_ms=latency,
                    )
                elif resp.status_code == 401:
                    return ProviderTestResult(
                        ok=False, message="Invalid API key", latency_ms=latency
                    )
                return ProviderTestResult(
                    ok=False,
                    message=f"API returned {resp.status_code}",
                    latency_ms=latency,
                )

            elif provider.kind == "google":
                resp = await client.get(
                    f"{provider.base_url}/v1beta/models",
                    headers={"x-goog-api-key": provider.api_key},
                )
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    model_ids = [
                        m.get("name", "").split("/")[-1]
                        for m in data.get("models", [])[:10]
                    ]
                    return ProviderTestResult(
                        ok=True,
                        message=f"Connected. {len(data.get('models', []))} models available",
                        latency_ms=latency,
                        models=model_ids,
                    )
                return ProviderTestResult(
                    ok=False,
                    message=f"API returned {resp.status_code}",
                    latency_ms=latency,
                )

            elif provider.kind == "ollama":
                resp = await client.get(f"{provider.base_url}/api/tags")
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    model_names = [m["name"] for m in data.get("models", [])]
                    return ProviderTestResult(
                        ok=True,
                        message=f"Connected. {len(model_names)} models installed",
                        latency_ms=latency,
                        models=model_names,
                    )
                return ProviderTestResult(
                    ok=False,
                    message=f"Ollama returned {resp.status_code}",
                    latency_ms=latency,
                )

            else:
                return ProviderTestResult(ok=False, message=f"Unknown provider kind '{provider.kind}'")

    except httpx.ConnectError:
        latency = int((time.monotonic() - start) * 1000)
        return ProviderTestResult(
            ok=False,
            message=f"Connection refused. Is the server running at {provider.base_url}?",
            latency_ms=latency,
        )
    except httpx.TimeoutException:
        return ProviderTestResult(
            ok=False, message="Connection timed out after 15s", latency_ms=15000
        )
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return ProviderTestResult(
            ok=False, message=f"Error. {str(exc)[:200]}", latency_ms=latency
        )


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()
