"""Recovery endpoint tests: stale reset, fresh refusal, audit atomicity."""

from decimal import Decimal

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.usage import BudgetReservationRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_task, make_world
from tests.factories import tasks as tasks_repo


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Owner login for the seeded world; CSRF headers (cookie in jar)."""
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _recover_url(task_id: str = "t_1") -> str:
    return f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/{task_id}/recover"


async def _age_task(session: AsyncSession, task_id: str, interval: str) -> None:
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - (:i || ' seconds')::interval WHERE id = :tid"),
        {"i": interval, "tid": task_id},
    )
    await session.commit()


async def _task_and_hold(session: AsyncSession, task_id: str):
    """Reload task + reservation from the DB after an API call."""
    await session.rollback()
    task = await tasks_repo.get(session, BIZ, task_id)
    hold = (
        await session.execute(
            select(BudgetReservation).where(
                BudgetReservation.business_id == BIZ,
                BudgetReservation.task_id == task_id,
            )
        )
    ).scalar_one()
    return task, hold


async def _set_status(session: AsyncSession, task_id: str, status: str, attempts: int = 0) -> None:
    await session.execute(
        text("UPDATE tasks SET status = :s, attempts = :a WHERE id = :tid"),
        {"s": status, "a": attempts, "tid": task_id},
    )
    await session.commit()


async def test_recover_stale_working_never_failed(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await session.commit()
    await _age_task(session, "t_1", "7200")

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "recovered" and body["status"] == "not_started"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "not_started" and task.attempts == 0

    from app.persistence.repositories.concrete import ActivityRepository

    events = await ActivityRepository().list(session, BIZ, project_id="proj_1")
    assert any("recovered from stale working state" in e.text for e in events)


async def test_recover_stale_working_keeps_failed_history(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await session.commit()
    await _set_status(session, "t_1", "working", attempts=2)
    await _age_task(session, "t_1", "7200")

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "failed"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.attempts == 2  # budget gate still applies


async def test_recover_fresh_working_refused_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await session.commit()

    from app.persistence.repositories.concrete import ActivityRepository

    before = len(await ActivityRepository().list(session, BIZ))
    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 409, res.text
    assert res.json()["outcome"] == "not_stale"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "working"
    after = len(await ActivityRepository().list(session, BIZ))
    assert after == before  # refused recovery writes nothing, not even an event


async def test_recover_non_working_reports_state(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="completed", progress=100)
    await session.commit()

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 409, res.text
    body = res.json()
    assert body["outcome"] == "not_stale" and body["status"] == "completed"


async def test_recover_missing_task_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await session.commit()
    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url("nope"), headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_recover_requires_auth(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_recover_url())
    assert res.status_code == 401


async def test_recover_requires_csrf_on_top_of_session(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Recovery is a mutation: cookie without CSRF token is 403 (Stage 6.1)."""
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    await _authed(session, api_client)

    missing = await api_client.post(_recover_url())
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "forbidden"
    wrong = await api_client.post(_recover_url(), headers={"X-CSRF-Token": "wrong"})
    assert wrong.status_code == 403


async def test_claim_advances_updated_at(session: AsyncSession) -> None:
    """Gate test for the recovery clock: claim MUST move updated_at.

    Bulk UPDATEs do not fire column onupdate РІР‚вЂќ claim_task sets it explicitly.
    If this fails, recovery staleness is unsound and recovery must not ship.
    """
    from app.persistence.repositories.concrete import TaskRepository

    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    # Age the row so any clock movement is unambiguous.
    await _age_task(session, "t_1", "7200")
    before = (await tasks_repo.get(session, BIZ, "t_1")).updated_at

    repo = TaskRepository()
    claimed = await repo.claim_task(
        session, BIZ, "t_1", from_statuses=("not_started",), to_status="working"
    )
    await session.commit()

    assert claimed is True
    after = (await tasks_repo.get(session, BIZ, "t_1")).updated_at
    assert after > before


async def test_spend_invariant_after_recovery(
    session: AsyncSession, api_client: httpx.AsyncClient, test_engine
) -> None:
    """Recovery touches status only: money must be exactly where it was."""
    from app.persistence.engine import session_factory
    from tests.factories import spend_of_project

    await make_world(session)
    await make_task(session, status="working", cost_accrued=Decimal("4.50"))
    await session.commit()
    await _age_task(session, "t_1", "7200")

    from app.persistence.repositories.spend import recalculate_project_spend

    await recalculate_project_spend(session, BIZ, "proj_1")
    await session.commit()

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "recovered"

    async with session_factory(test_engine)() as fresh:
        assert await spend_of_project(fresh, BIZ, "proj_1") == Decimal("4.50")
        task = await tasks_repo.get(fresh, BIZ, "t_1")
        assert task is not None and task.cost_accrued == Decimal("4.50")


async def test_recovery_cutoff_boundary_uses_database_clock(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Default 40m cutoff: 2399s fresh, 2400s/2401s stale, all in DB time."""
    await make_world(session)
    await make_task(session, "t_under", status="working")
    await make_task(session, "t_exact", status="working")
    await make_task(session, "t_over", status="working")
    repo = BudgetReservationRepository()
    for task_id in ("t_under", "t_exact", "t_over"):
        hold = await repo.reserve(
            session,
            BIZ,
            project_id="proj_1",
            task_id=task_id,
            attempt=0,
            attempt_key=f"{BIZ}:{task_id}:a0",
            amount=Decimal("2.00"),
        )
        assert hold is not None
    await session.commit()

    # Log in before aging so the one-second boundary is not consumed by auth.
    headers = await _authed(session, api_client)

    # Age with PostgreSQL now(): the endpoint must use database time.
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '2399 seconds' WHERE id = 't_under'")
    )
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '2400 seconds' WHERE id = 't_exact'")
    )
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '2401 seconds' WHERE id = 't_over'")
    )
    await session.commit()

    under = await api_client.post(_recover_url("t_under"), headers=headers)
    assert under.status_code == 409, under.text
    assert under.json()["outcome"] == "not_stale"
    task, hold = await _task_and_hold(session, "t_under")
    assert task is not None and task.status == "working"
    assert hold.status == "active"
    assert hold.amount == Decimal("2.00")

    # 40 minutes exactly is the configured cutoff. By the time the endpoint
    # executes, DB now() is strictly later, so the row must be recoverable.
    exact = await api_client.post(_recover_url("t_exact"), headers=headers)
    assert exact.status_code == 200, exact.text
    assert exact.json()["outcome"] == "recovered"
    task, hold = await _task_and_hold(session, "t_exact")
    assert task is not None and task.status == "not_started"
    assert hold.status == "ambiguous" and hold.reason == "recovery_stale"
    assert hold.amount == Decimal("2.00")

    over = await api_client.post(_recover_url("t_over"), headers=headers)
    assert over.status_code == 200, over.text
    assert over.json()["outcome"] == "recovered"
    task, hold = await _task_and_hold(session, "t_over")
    assert task is not None and task.status == "not_started"
    assert hold.status == "ambiguous" and hold.reason == "recovery_stale"
    assert hold.amount == Decimal("2.00")
