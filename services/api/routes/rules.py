import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.models import Rule, User
from shared.schemas import RuleCreate, RuleResponse, RuleUpdate

router = APIRouter()
logger = logging.getLogger("nurby.api.rules")


async def _publish_invalidation(rule_id: uuid.UUID | str) -> None:
    """Best-effort. perception listens on ``nurby:rules:invalidate`` and
    re-loads the rule set on the next evaluate() tick. Failures here
    only mean the perception engine waits up to its 30s passive TTL
    instead of refreshing within ~1s.
    """
    try:
        import redis.asyncio as aioredis

        from services.events.engine import RULES_INVALIDATE_CHANNEL

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.publish(RULES_INVALIDATE_CHANNEL, str(rule_id))
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    except Exception:
        logger.debug("rule invalidation publish failed", exc_info=True)


@router.get("", response_model=list[RuleResponse])
async def list_rules(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rule).order_by(Rule.created_at))
    return result.scalars().all()


@router.post("", response_model=RuleResponse, status_code=201)
async def create_rule(body: RuleCreate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = Rule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await _publish_invalidation(rule.id)
    return rule


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(rule_id: uuid.UUID, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: uuid.UUID, body: RuleUpdate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    await _publish_invalidation(rule.id)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    await _publish_invalidation(rule_id)
