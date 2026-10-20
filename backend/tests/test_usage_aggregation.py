"""Stage 16.1 slice 2: taskless project usage + budget ceiling aggregation."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import ProjectRepository
from app.persistence.repositories.spend import (
    recalculate_project_spend,
    recalculate_task_cost,
)
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from tests.factories import BIZ, make_task, make_world, spend_of_project

_usage = UsageRepository()
_reservations = BudgetReservationRepository()


def _row(**over) -> dict:
    data = {
        "idempotency_key": "usage:k1",
        "attempt_key": f"{BIZ}:t_1:a0",
        "attempt": 0,
        "provider": "mock",
        "model": "mock",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_amount": Decimal("0.10"),
        "cost_source": "tariff",
        "usage_status": "confirmed",
        "occurred_at": datetime.now(UTC),
    }
    data.update(over)
    return data


async def test_taskless_confirmed_is_added_exactly_once(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    await _usage.append(
        session,
        BIZ,
        _row(idempotency_key="u-task", project_id="proj_1", task_id="t_1"),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-final",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=Decimal("0.07"),
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-est",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=Decimal("9.99"),
            usage_status="estimated",
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-unknown",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=None,
            cost_source="unknown",
            usage_status="unknown",
        ),
    )

    assert await recalculate_task_cost(session, BIZ, "t_1") == Decimal("0.10")
    first = await recalculate_project_spend(session, BIZ, "proj_1")
    second = await recalculate_project_spend(session, BIZ, "proj_1")

    assert first == second == Decimal("0.17")
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0.17")
    assert await _usage.taskless_confirmed_total(session, BIZ, "proj_1") == Decimal("0.07")
    assert await _usage.taskless_estimated_total(session, BIZ, "proj_1") == Decimal("9.99")


async def test_business_scoped_rows_never_enter_project_spend(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-business",
            attempt_key="tl:b1:plan_generation:g0",
            project_id=None,
            task_id=None,
            call_kind="plan_generation",
        ),
    )

    assert await recalculate_project_spend(session, BIZ, "proj_1") == Decimal("0")
    assert await _usage.taskless_confirmed_total(session, BIZ, "proj_1") == Decimal("0")


async def test_reserve_ceiling_counts_confirmed_and_taskless_estimated(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("1.00")})
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-final",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=Decimal("0.20"),
        ),
    )
    await recalculate_project_spend(session, BIZ, "proj_1")
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-est",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=Decimal("0.60"),
            usage_status="estimated",
        ),
    )

    blocked = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=None,
        attempt_key="tl:b1:brief_finalize:g0",
        amount=Decimal("0.30"),
    )
    assert blocked is None

    ok = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=None,
        attempt_key="tl:b1:brief_finalize:g0",
        amount=Decimal("0.20"),
    )
    assert ok is not None and ok.amount == Decimal("0.20")
