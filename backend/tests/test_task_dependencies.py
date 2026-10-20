"""Task dependency edges on the read API (Stage 8.4, contract v1.10).

Contract §1 always required `dependsOn` on the Task wire shape; the read
API omitted it. These tests pin the compliance fix: list + single get +
plan response carry the stored edges, scoped to the caller's business.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import TaskRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_task, make_world

_tasks = TaskRepository()


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started", depends_on=["t_1"])
    await make_task(session, task_id="t_3", status="not_started", depends_on=["t_1", "t_2"])
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _tasks_url(project_id: str = "proj_1") -> str:
    return f"/api/v1/businesses/{BIZ}/projects/{project_id}/tasks"


async def test_dependency_map_orders_edges(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started", depends_on=["t_1"])
    await make_task(session, task_id="t_3", status="not_started", depends_on=["t_1", "t_2"])
    await session.commit()
    edges = await _tasks.dependency_map(session, ["t_1", "t_2", "t_3"])
    assert edges == {"t_1": [], "t_2": ["t_1"], "t_3": ["t_1", "t_2"]}
    assert await _tasks.dependency_map(session, []) == {}


async def test_list_tasks_carries_depends_on(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(_tasks_url(), headers=headers)
    assert res.status_code == 200, res.text
    by_id = {t["id"]: t for t in res.json()["items"]}
    assert by_id["t_1"]["dependsOn"] == []
    assert by_id["t_2"]["dependsOn"] == ["t_1"]
    assert by_id["t_3"]["dependsOn"] == ["t_1", "t_2"]


async def test_read_task_carries_depends_on(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(f"{_tasks_url()}/t_3", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["dependsOn"] == ["t_1", "t_2"]
