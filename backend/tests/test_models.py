"""CRUD + defaults + clamping per aggregate (Stage 2 persistence)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import InvalidIdError
from tests.factories import (
    BIZ,
    agents,
    brain,
    businesses,
    make_agent,
    make_business,
    make_task,
    make_world,
    marketplace,
    projects,
)


async def test_business_crud(session: AsyncSession) -> None:
    b = await make_business(session, "biz_crud", name="Acme", health_score=90)
    assert b.health_score == 90
    got = await businesses.get(session, "biz_crud", "biz_crud")
    assert got is not None and got.name == "Acme"
    updated = await businesses.update(session, "biz_crud", "biz_crud", {"name": "Acme 2"})
    assert updated is not None and updated.name == "Acme 2"
    assert await businesses.delete(session, "biz_crud", "biz_crud") is True
    assert await businesses.get(session, "biz_crud", "biz_crud") is None


async def test_agent_defaults(session: AsyncSession) -> None:
    await make_world(session)
    assert await agents.count(session, BIZ) == 1


async def test_project_progress_clamped_and_spend_not_writable(session: AsyncSession) -> None:
    await make_world(session)
    p = await projects.create(
        session, BIZ, {"id": "proj_x", "title": "X", "progress": 250, "ai_spent": 999}
    )
    assert p.progress == 100  # clamped, never wraps
    assert float(p.ai_spent) == 0.0  # derived only — write ignored


async def test_task_money_quantized_and_gate_defaults(session: AsyncSession) -> None:
    await make_world(session)
    t = await make_task(session, cost_accrued=1.805)
    assert str(t.cost_accrued) == "1.81"  # ROUND_HALF_UP to cents
    assert t.gate_at == 100
    assert t.attempts == 0


async def test_invalid_id_rejected(session: AsyncSession) -> None:
    await make_business(session)
    with pytest.raises(InvalidIdError):
        await make_agent(session, agent_id="bad id!")
    with pytest.raises(InvalidIdError):
        await make_agent(session, agent_id="")


async def test_brain_and_marketplace_crud(session: AsyncSession) -> None:
    await make_business(session)
    entry = await brain.create(
        session, BIZ, {"id": "b_1", "category": "goals", "title": "G", "body": "B"}
    )
    assert entry.category == "goals"
    item = await marketplace.create(
        session,
        BIZ,
        {"id": "seo-specialist", "name": "SEO", "category": "employee"},
    )
    assert item.id == "seo-specialist"
    assert await marketplace.count(session, BIZ) == 1
    # slug immutable
    from app.persistence.exceptions import ImmutableFieldError

    with pytest.raises(ImmutableFieldError):
        await marketplace.update(session, BIZ, "seo-specialist", {"id": "other"})
    assert await brain.delete(session, BIZ, "b_1") is True
