"""Recovery endpoint tests: stale reset, fresh refusal, audit atomicity."""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
    from decimal import Decimal

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
