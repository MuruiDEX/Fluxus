"""Stage 16 slice 5: recovery holds (never releases) possibly-billed budget."""

from decimal import Decimal

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage.reconciliation import reconcile_project_reservations
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_task, make_world


def _recover_url(task_id: str = "t_1") -> str:
    return f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/{task_id}/recover"


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


async def _reservation(session: AsyncSession) -> BudgetReservation:
    await session.rollback()
    res = await session.execute(select(BudgetReservation))
    return res.scalar_one()


async def test_recovery_marks_crashed_hold_ambiguous_not_released(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await BudgetReservationRepository().reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("3.00"),
    )
    await session.commit()
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '7200 seconds' WHERE id = 't_1'")
    )
    await session.commit()

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "recovered"
    hold = await _reservation(session)
    assert hold.status == "ambiguous"
    assert hold.reason == "recovery_stale"
    assert hold.amount == Decimal("3.00")  # exposure stays counted


async def test_fresh_recovery_touches_no_reservation(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await BudgetReservationRepository().reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("3.00"),
    )
    await session.commit()

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 409, res.text
    hold = await _reservation(session)
    assert hold.status == "active"  # live run untouched


async def test_project_reconciliation_holds_taskless_teamlead_hold(
    session: AsyncSession,
) -> None:
    await make_world(session)
    repo = BudgetReservationRepository()
    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=None,
        attempt_key="tl:b1:brief_finalize:g0",
        amount=Decimal("1.00"),
        mode="best_effort",
    )
    assert hold is not None
    await session.commit()

    held, released = await reconcile_project_reservations(session, BIZ, "proj_1")
    await session.commit()

    assert (held, released) == (1, 0)
    hold = await _reservation(session)
    assert hold.status == "ambiguous" and hold.reason == "recovery_stale"
    assert hold.amount == Decimal("1.00")


async def test_project_reconciliation_can_age_out_ambiguous(
    session: AsyncSession,
) -> None:
    await make_world(session)
    repo = BudgetReservationRepository()
    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=None,
        attempt_key="tl:b1:brief_finalize:g0",
        amount=Decimal("1.00"),
    )
    assert hold is not None
    await repo.set_status(
        session, BIZ, hold.id, from_statuses=("active",), to_status="ambiguous", reason="timeout"
    )
    await session.commit()
    await session.execute(
        text("UPDATE budget_reservations SET created_at = now() - interval '120 minutes'")
    )
    await session.commit()

    held, released = await reconcile_project_reservations(
        session, BIZ, "proj_1", ambiguous_release_minutes=60
    )
    await session.commit()

    assert (held, released) == (0, 1)
    assert (await _reservation(session)).status == "released"


async def test_ambiguous_release_is_disabled_by_default(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()
    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("3.00"),
    )
    assert hold is not None
    await repo.set_status(
        session, BIZ, hold.id, from_statuses=("active",), to_status="ambiguous", reason="timeout"
    )
    await session.commit()
    await session.execute(
        text("UPDATE budget_reservations SET created_at = now() - interval '365 days'")
    )
    await session.commit()

    released = await repo.release_aged_ambiguous(session, BIZ, older_than_minutes=0)

    assert released == 0
    assert (await _reservation(session)).status == "ambiguous"


async def test_ambiguous_release_when_operator_ages_it_out(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()
    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("3.00"),
    )
    assert hold is not None
    await repo.set_status(
        session, BIZ, hold.id, from_statuses=("active",), to_status="ambiguous", reason="timeout"
    )
    await session.commit()
    await session.execute(
        text("UPDATE budget_reservations SET created_at = now() - interval '120 minutes'")
    )
    await session.commit()

    released = await repo.release_aged_ambiguous(session, BIZ, older_than_minutes=60)
    await session.commit()

    assert released == 1
    hold = await _reservation(session)
    assert hold.status == "released" and hold.reason == "aged_out"
