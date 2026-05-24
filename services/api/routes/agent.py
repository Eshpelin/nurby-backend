"""Agentic Q&A REST + WebSocket surface.

Routes mounted at ``/api/agent`` plus the websocket endpoint
``/ws/agent/{run_id}``. See docs/agent-design.md sections 10, 12, and
17 for protocol details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.agent import runs as runs_mod
from services.agent import ws as agent_ws
from services.agent.budget import check_budget, estimate_cost
from services.agent.driver import AgentDriver
from shared.app_settings import get_setting
from shared.auth import decode_access_token, get_current_user
from shared.database import async_session, get_db
from shared.models import (
    AgentDailyUsage,
    AgentRun,
    AgentToolCall,
    AgentVlmCall,
    Provider,
    User,
)
from shared.schemas import (
    AgentAskRequest,
    AgentAskResponse,
    AgentDailyUsageResponse,
    AgentRunDetailResponse,
    AgentRunResponse,
    AgentToolCallResponse,
    AgentVlmCallResponse,
)

logger = logging.getLogger("nurby.api.agent")

router = APIRouter()


# ── Provider helpers ────────────────────────────────────────────────


TOOL_USE_KINDS = {"anthropic", "claude", "openai", "gpt", "gemini", "google", "ollama"}
RECOMMENDED_PREFIXES = ("claude-sonnet-4",)


def _is_tool_use_kind(kind: str | None) -> bool:
    return (kind or "").strip().lower() in TOOL_USE_KINDS


def _normalize_question(q: str) -> str:
    return " ".join((q or "").strip().lower().split())


async def _broadcast(run_id: str, event: dict) -> None:
    await agent_ws.publish_event(run_id, event)


# ── POST /api/agent/ask ──────────────────────────────────────────────


@router.post("/ask", response_model=AgentAskResponse, status_code=202)
async def ask(
    body: AgentAskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentAskResponse:
    """Submit a question. Returns immediately with a run_id + ws_url.

    The driver runs as a background task; the caller subscribes to the
    websocket for streamed events. ``dry_run=True`` validates and
    returns without starting the driver.
    """
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    provider_id = body.provider_id
    if provider_id is None:
        default = await get_setting("agent_default_provider_id")
        if default:
            provider_id = uuid.UUID(str(default))
    if provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required (no default configured)")

    provider = await db.get(Provider, provider_id)
    if provider is None or not provider.active:
        raise HTTPException(status_code=404, detail="provider not found or inactive")
    if not _is_tool_use_kind(provider.kind):
        raise HTTPException(status_code=400, detail=f"provider kind {provider.kind!r} does not support tool-use")

    model = body.model or provider.default_model
    if not model:
        raise HTTPException(status_code=400, detail="model is required (provider has no default)")

    budget = await check_budget(current_user.id, db)
    if not budget.ok:
        raise HTTPException(status_code=429, detail=f"daily budget exhausted: {budget.reason}")

    # de-dupe in-flight identical question by hash within 60s window
    qhash = hashlib.sha256(_normalize_question(body.question).encode("utf-8")).hexdigest()
    since = datetime.now(timezone.utc) - timedelta(seconds=60)
    dupe_stmt = (
        select(AgentRun)
        .where(
            AgentRun.user_id == current_user.id,
            AgentRun.status == "running",
            AgentRun.started_at >= since,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(8)
    )
    for cand in (await db.execute(dupe_stmt)).scalars().all():
        cand_hash = hashlib.sha256(_normalize_question(cand.question).encode("utf-8")).hexdigest()
        if cand_hash == qhash:
            return AgentAskResponse(run_id=cand.id, ws_url=f"/ws/agent/{cand.id}")

    if body.dry_run:
        # Synthetic run_id; do not persist.
        synthetic = uuid.uuid4()
        return AgentAskResponse(run_id=synthetic, ws_url=f"/ws/agent/{synthetic}")

    run = await runs_mod.create_run(
        user_id=current_user.id,
        question=body.question.strip(),
        provider_id=provider.id,
        model=model,
        parent_run_id=body.parent_run_id,
        db=db,
    )

    # Snapshot user + provider so the background task does not race the
    # request-scoped session being closed.
    user_snapshot = current_user
    provider_snapshot = provider

    async def _run_driver() -> None:
        driver = AgentDriver(db_factory=async_session, broadcast=_broadcast)
        try:
            await driver.run(
                run_id=run.id,
                user=user_snapshot,
                question=run.question,
                provider=provider_snapshot,
                model=model,
                parent_run_id=body.parent_run_id,
            )
        except Exception:
            logger.exception("agent driver crashed for run %s", run.id)
        finally:
            await agent_ws.mark_terminal(str(run.id))

    asyncio.create_task(_run_driver())
    return AgentAskResponse(run_id=run.id, ws_url=f"/ws/agent/{run.id}")


# ── POST /api/agent/runs/{run_id}/cancel ────────────────────────────


_active_drivers: dict[uuid.UUID, asyncio.Event] = {}


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Request a cooperative cancellation. The driver finishes the
    current turn then exits. Idempotent on terminal runs."""
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="not your run")
    if run.status not in {"running"}:
        return {"status": run.status, "noop": True}
    # The driver loop checks its stop_event each turn; we signal via the
    # WS bus so any active subscriber sees the intent immediately and the
    # driver instance ignores future turns. The runs_mod.cancel_run
    # mutation lands either way once the driver completes its turn.
    await runs_mod.cancel_run(run_id, "user_cancelled", db)
    await agent_ws.publish_event(str(run_id), {
        "type": "cancelled",
        "seq": 9_999_999,
        "run_id": str(run_id),
        "reason": "user_cancelled",
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "cancelling"}


# ── GET /api/agent/runs/{run_id} ────────────────────────────────────


@router.get("/runs/{run_id}", response_model=AgentRunDetailResponse)
async def get_run_detail(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunDetailResponse:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="not your run")
    tools = (await db.execute(
        select(AgentToolCall).where(AgentToolCall.run_id == run_id).order_by(AgentToolCall.created_at)
    )).scalars().all()
    vlms = (await db.execute(
        select(AgentVlmCall).where(AgentVlmCall.run_id == run_id).order_by(AgentVlmCall.created_at)
    )).scalars().all()
    detail = AgentRunDetailResponse.model_validate(run)
    detail.plan = run.plan
    detail.tool_calls = [AgentToolCallResponse.model_validate(t) for t in tools]
    detail.vlm_calls = [AgentVlmCallResponse.model_validate(v) for v in vlms]
    return detail


# ── GET /api/agent/runs ─────────────────────────────────────────────


@router.get("/runs", response_model=list[AgentRunResponse])
async def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    user_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    target_user = current_user.id
    if user_id is not None and user_id != current_user.id:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="admins only")
        target_user = user_id
    stmt = select(AgentRun).where(AgentRun.user_id == target_user)
    if before:
        stmt = stmt.where(AgentRun.started_at < before)
    stmt = stmt.order_by(AgentRun.started_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [AgentRunResponse.model_validate(r) for r in rows]


# ── GET /api/agent/admin/runs ───────────────────────────────────────


@router.get("/admin/runs", response_model=list[AgentRunResponse])
async def admin_list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    stmt = select(AgentRun)
    if before:
        stmt = stmt.where(AgentRun.started_at < before)
    stmt = stmt.order_by(AgentRun.started_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [AgentRunResponse.model_validate(r) for r in rows]


# ── GET /api/agent/usage/today ──────────────────────────────────────


@router.get("/usage/today")
async def usage_today(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    today = datetime.now(timezone.utc).date()
    stmt = select(AgentDailyUsage).where(
        AgentDailyUsage.user_id == current_user.id,
        AgentDailyUsage.usage_date == today,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    budget = await check_budget(current_user.id, db)
    return {
        "usage": (
            AgentDailyUsageResponse.model_validate(row).model_dump()
            if row is not None else None
        ),
        "token_budget": budget.token_budget,
        "cost_budget_cents": budget.cost_budget_cents,
        "used_tokens": budget.used_tokens,
        "used_cost_cents": budget.used_cost_cents,
        "remaining_tokens": budget.remaining_tokens,
        "remaining_cost_cents": budget.remaining_cost_cents,
        "percent_used": int(max(
            (budget.used_tokens * 100 / budget.token_budget) if budget.token_budget else 0,
            (budget.used_cost_cents * 100 / budget.cost_budget_cents) if budget.cost_budget_cents else 0,
        )),
        "warn": budget.warn,
        "ok": budget.ok,
    }


# ── GET /api/agent/providers ────────────────────────────────────────


@router.get("/providers")
async def list_agent_providers(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Providers that advertise tool-use. Drives the model selector."""
    rows = (await db.execute(
        select(Provider).where(Provider.active == True).order_by(Provider.created_at)
    )).scalars().all()
    out: list[dict] = []
    for p in rows:
        if not _is_tool_use_kind(p.kind):
            continue
        # We expose only the configured default_model in v1 plus a
        # convention slot. The UI is free to let users type any model.
        default_model = p.default_model or ""
        models: list[dict] = []
        if default_model:
            models.append({
                "name": default_model,
                "label": default_model,
                "recommended": any(default_model.lower().startswith(pfx) for pfx in RECOMMENDED_PREFIXES),
            })
        out.append({
            "provider_id": str(p.id),
            "kind": p.kind,
            "label": p.name,
            "models": models,
        })
    return out


# ── WS /ws/agent/{run_id} ───────────────────────────────────────────


ws_router = APIRouter()


@ws_router.websocket("/ws/agent/{run_id}")
async def agent_ws_endpoint(
    websocket: WebSocket,
    run_id: uuid.UUID,
    token: str = Query(...),
    after_seq: int = Query(default=0),
) -> None:
    """Stream agent events for a single run.

    Auth via ``?token=<jwt>`` (same convention as the thumbnails and
    mic routes). Supports ``?after_seq=N`` for reconnect replay.
    Multiple concurrent subscribers per run are allowed.
    """
    user_id = decode_access_token(token)
    if user_id is None:
        await websocket.close(code=4401)
        return

    # Confirm user is allowed to view this run.
    async with async_session() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            await websocket.close(code=4404)
            return
        user = await db.get(User, user_id)
        if user is None or (run.user_id != user.id and user.role != "admin"):
            await websocket.close(code=4403)
            return

    await websocket.accept()
    queue, backlog = await agent_ws.subscribe(str(run_id), after_seq=after_seq)
    try:
        # flush backlog (replay)
        for ev in backlog:
            await websocket.send_text(json.dumps(ev))
            if ev.get("type") in {"done", "cancelled", "error"}:
                # Run already terminal at connect time. Replay then close.
                await websocket.close()
                return
        while True:
            ev = await queue.get()
            await websocket.send_text(json.dumps(ev))
            if ev.get("type") in {"done", "cancelled", "error"}:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("agent ws handler crashed run=%s", run_id)
    finally:
        await agent_ws.unsubscribe(str(run_id), queue)
        try:
            await websocket.close()
        except Exception:
            pass


__all__ = ["router", "ws_router"]
