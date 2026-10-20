"""Transactions: atomic batches, rollback, concurrent spend recalculation."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.engine import session_factory
from app.persistence.repositories.concrete import ActivityRepository
from app.persistence.repositories.spend import recalculate_project_spend
from tests.factories import BIZ, make_task, make_world, tasks

activity = ActivityRepository()


async def test_batch_rolls_back_atomically(session: AsyncSession) -> None:
    """A mid-batch failure voids the whole batch via SAVEPOINT rollback."""
    await make_world(session)
    from app.persistence.models.task import Task

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Task(
                    id="t_tmp",
                    business_id=BIZ,
                    project_id="proj_1",
                    title="tmp",
                    owner_agent_id="ag_1",
                    status="exploding",  # violates ck_tasks_status
                )
            )
            await session.flush()
    assert await tasks.get(session, BIZ, "t_tmp") is None
    # The surrounding world is intact.
    assert await tasks.count(session, BIZ) == 0


async def test_concurrent_recalc_serializes(test_engine) -> None:
    """Two writers racing recalculate_project_spend converge on the true sum."""
    factory = session_factory(test_engine)
    async with factory() as s:
        async with s.begin():
            await make_world(s)
            await make_task(s, task_id="t_1", cost_accrued=Decimal("1.10"))
            await make_task(s, task_id="t_2", cost_accrued=Decimal("2.20"))

    async def bump(task_id: str, amount: Decimal) -> None:
        async with factory() as s:
            async with s.begin():
                t = await tasks.get(s, BIZ, task_id)
                assert t is not None
                t.cost_accrued = amount
                await s.flush()
                await recalculate_project_spend(s, BIZ, "proj_1")

    await asyncio.gather(bump("t_1", Decimal("5.00")), bump("t_2", Decimal("7.00")))

    async with factory() as s:
        total = await recalculate_project_spend(s, BIZ, "proj_1")
        assert total == Decimal("12.00")
