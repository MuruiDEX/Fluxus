"""Usage ledger + budget reservation repository mechanics (Stage 16)."""

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


def _usage_data(**over) -> dict:
    data = {
        "idempotency_key": "usage:proj_1:t_1:a0:1",
        "attempt_key": "biz_test:t_1:a0",
        "attempt": 0,
        "provider": "mock",
        "model": "mock",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_amount": Decimal("0.03"),
        "cost_source": "tariff",
        "usage_status": "confirmed",
        "tariff_version": "test-1",
        "occurred_at": datetime.now(UTC),
    }
    data.update(over)
    return data


async def test_append_is_idempotent_on_key(session: AsyncSession) -> None:
    await make_world(session)
    first = await _usage.append(session, BIZ, _usage_data())
    second = await _usage.append(session, BIZ, _usage_data())
    assert first.id == second.id
    assert await _usage.count_for_business(session, BIZ) == 1


async def test_unknown_cost_is_null_never_zero(session: AsyncSession) -> None:
    await make_world(session)
    row = await _usage.append(
        session,
        BIZ,
        _usage_data(
            idempotency_key="usage:unknown",
            cost_amount=None,
            cost_source="unknown",
            usage_status="unknown",
            tariff_version=None,
        ),
    )
    assert row.cost_amount is None


async def test_confirmed_cost_recomputes_task_and_project(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    await _usage.append(
        session,
        BIZ,
        _usage_data(
            project_id="proj_1", task_id="t_1", agent_id="ag_1",
            cost_amount=Decimal("0.03"),
        ),
    )
    assert await recalculate_task_cost(session, BIZ, "t_1") == Decimal("0.03")
    assert await recalculate_project_spend(session, BIZ, "proj_1") == Decimal("0.03")
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0.03")


async def test_estimated_and_unknown_excluded_from_confirmed(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    await _usage.append(
        session,
        BIZ,
        _usage_data(
            idempotency_key="u-est",
            project_id="proj_1",
            task_id="t_1",
            cost_amount=Decimal("9.99"),
            cost_source="tariff",
            usage_status="estimated",
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _usage_data(
            idempotency_key="u-unk",
            project_id="proj_1",
            task_id="t_1",
            cost_amount=None,
            cost_source="unknown",
            usage_status="unknown",
        ),
    )
    assert await recalculate_task_cost(session, BIZ, "t_1") == Decimal("0")
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0")


async def test_reserve_blocks_over_budget(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("1.00")})

    ok = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key="biz_test:t_1:a0",
        amount=Decimal("0.60"),
    )
    assert ok is not None and ok.status == "active"

    # Second reservation would breach the ceiling (0.60 + 0.60 > 1.00).
    blocked = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=1,
        attempt_key="biz_test:t_1:a1",
        amount=Decimal("0.60"),
    )
    assert blocked is None


async def test_zero_budget_means_no_limit_but_still_reserves(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    reservation = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key="biz_test:t_1:a0",
        amount=Decimal("500.00"),
    )
    assert reservation is not None
    assert await _reservations.pending_total(session, BIZ, "proj_1") == Decimal("500.00")


async def test_set_status_is_compare_and_set(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session)
    reservation = await _reservations.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key="biz_test:t_1:a0",
        amount=Decimal("0.25"),
    )
    assert reservation is not None
    won = await _reservations.set_status(
        session, BIZ, reservation.id, from_statuses=("active",), to_status="released"
    )
    assert won is True
    lost = await _reservations.set_status(
        session, BIZ, reservation.id, from_statuses=("active",), to_status="consumed"
    )
    assert lost is False
