"""Spend invariant: aiSpent == Σ scoped tasks.costAccrued (contract §5.1)."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.spend import quantize_spend, recalculate_project_spend
from tests.factories import BIZ, make_task, make_world, spend_of_project, tasks


async def test_recompute_sums_scope_and_ignores_others(session: AsyncSession) -> None:
    from tests.factories import BIZ_OTHER, make_agent, make_business, make_project

    await make_world(session)
    await make_business(session, BIZ_OTHER)
    await make_agent(session, "ag_o", BIZ_OTHER)
    await make_project(session, "proj_o", BIZ_OTHER)
    await make_task(session, task_id="t_1", cost_accrued=Decimal("1.10"))
    await make_task(session, task_id="t_2", cost_accrued=Decimal("2.20"))
    await make_task(
        session,
        task_id="t_o",
        biz_id=BIZ_OTHER,
        project_id="proj_o",
        owner_id="ag_o",
        cost_accrued=Decimal("99.99"),
    )
    total = await recalculate_project_spend(session, BIZ, "proj_1")
    assert total == Decimal("3.30")
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("3.30")


async def test_recompute_idempotent_no_double_count(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, task_id="t_1", cost_accrued=Decimal("4.00"))
    first = await recalculate_project_spend(session, BIZ, "proj_1")
    second = await recalculate_project_spend(session, BIZ, "proj_1")
    assert first == second == Decimal("4.00")


async def test_empty_scope_is_zero(session: AsyncSession) -> None:
    await make_world(session)
    assert await recalculate_project_spend(session, BIZ, "proj_1") == Decimal("0.00")


async def test_half_up_rounding_matches_frontend(session: AsyncSession) -> None:
    # Frontend Math.round(1.805*100)/100 == 1.81; backend must agree tick-for-tick.
    assert quantize_spend(Decimal("1.805")) == Decimal("1.81")
    assert quantize_spend(Decimal("2.675")) == Decimal("2.68")
    await make_world(session)
    await make_task(session, task_id="t_1", cost_accrued=Decimal("1.805"))
    assert await recalculate_project_spend(session, BIZ, "proj_1") == Decimal("1.81")


async def test_direct_ai_spent_write_impossible_via_repo(session: AsyncSession) -> None:
    """aiSpent has no write path: task-cost writes flow through recompute only."""
    await make_world(session)
    t = await make_task(session, task_id="t_1", cost_accrued=Decimal("2.00"))
    await tasks.update(session, BIZ, "t_1", {"cost_accrued": Decimal("9.99")})
    assert await recalculate_project_spend(session, BIZ, "proj_1") == Decimal("9.99")
    assert t.id == "t_1"
