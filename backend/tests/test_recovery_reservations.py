"""Stage 16 slice 5 / S2.2.4.6: recovery never releases possibly-billed budget."""

from decimal import Decimal

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

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


async def _aged_ambiguous_task_hold(session: AsyncSession) -> Decimal:
    """Create a 365-day-old `ambiguous` task hold; return pending before recovery."""
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
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '7200 seconds' WHERE id = 't_1'")
    )
    await session.commit()
    return await repo.pending_total(session, BIZ, "proj_1")


async def test_ambiguous_hold_aged_one_year_stays_ambiguous_after_recovery(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    pending_before = await _aged_ambiguous_task_hold(session)

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    hold = await _reservation(session)
    assert hold.status == "ambiguous"
    assert hold.reason == "timeout"
    assert (
        await BudgetReservationRepository().pending_total(session, BIZ, "proj_1") == pending_before
    )


async def test_nonzero_deprecated_release_setting_has_no_effect(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    from app.core.config import Settings
    from app.main import create_app
    from app.persistence.engine import session_factory

    pending_before = await _aged_ambiguous_task_hold(session)
    settings = Settings(environment="test", usage_ambiguous_release_minutes=1)
    # Backwards-compatible parsing is preserved, but the value is ignored.
    assert settings.usage_ambiguous_release_minutes == 1

    app = create_app(settings)
    app.state.session_factory = session_factory(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        headers = await _authed(session, client)
        res = await client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    hold = await _reservation(session)
    assert hold.status == "ambiguous"
    assert (
        await BudgetReservationRepository().pending_total(session, BIZ, "proj_1") == pending_before
    )


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

    held = await reconcile_project_reservations(session, BIZ, "proj_1")
    await session.commit()

    assert held == 1
    hold = await _reservation(session)
    assert hold.status == "ambiguous" and hold.reason == "recovery_stale"
    assert hold.amount == Decimal("1.00")


async def test_project_reconciliation_never_ages_out_ambiguous(
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
        text("UPDATE budget_reservations SET created_at = now() - interval '365 days'")
    )
    await session.commit()
    pending_before = await repo.pending_total(session, BIZ, "proj_1")

    held = await reconcile_project_reservations(session, BIZ, "proj_1")
    await session.commit()

    assert held == 0
    hold = await _reservation(session)
    assert hold.status == "ambiguous" and hold.reason == "timeout"
    assert await repo.pending_total(session, BIZ, "proj_1") == pending_before
