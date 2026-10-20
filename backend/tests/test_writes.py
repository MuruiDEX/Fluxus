"""Management writes (Stage 7.1): manual Brain entries + metadata patches.

Covers: success shapes + audit events, invalid enum/body, forbidden
executor-derived fields (status/progress/cost/ai_spent), dangling refs,
anonymous 401, missing CSRF 403, foreign scope 404, missing 404, spend
invariant preservation. No DELETE exists in this slice by design.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import ActivityRepository
from tests.conftest import OPERATOR_EMAIL, login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_agent, make_task, make_world
from tests.factories import brain as brain_repo
from tests.factories import projects as projects_repo
from tests.factories import tasks as tasks_repo

_activity = ActivityRepository()


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Seed world + operator, log in (cookie stays in the client jar)."""
    await make_world(session)
    await make_agent(session, agent_id="ag_2", status="available")
    await make_task(session, task_id="t_1", status="not_started")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _brain_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/brain"


def _project_url(biz: str = BIZ, project_id: str = "proj_1") -> str:
    return f"/api/v1/businesses/{biz}/projects/{project_id}"


def _task_url(biz: str = BIZ, project_id: str = "proj_1", task_id: str = "t_1") -> str:
    return f"/api/v1/businesses/{biz}/projects/{project_id}/tasks/{task_id}"


async def _system_events(session: AsyncSession, biz: str):
    return [e for e in await _activity.list(session, biz) if e.kind == "system"]


# --- Brain POST ---


async def test_brain_create_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(
        _brain_url(),
        json={"category": "goals", "title": "Ship it", "body": "Details"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["category"] == "goals" and body["title"] == "Ship it"
    assert body["businessId"] == BIZ and body["contract"] == "v1"
    assert body["id"]  # server-generated

    row = await brain_repo.get(session, BIZ, body["id"])
    assert row is not None and row.body == "Details"
    events = await _system_events(session, BIZ)
    assert len(events) == 1
    assert events[0].agent_name == OPERATOR_EMAIL and body["id"] in events[0].text


async def test_brain_create_bad_category(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(
        _brain_url(), json={"category": "telepathy", "title": "T"}, headers=headers
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "invalid_enum"


async def test_brain_create_blank_title(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(
        _brain_url(), json={"category": "goals", "title": "   "}, headers=headers
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"


async def test_brain_create_unauthenticated(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await session.commit()
    res = await api_client.post(_brain_url(), json={"category": "goals", "title": "T"})
    assert res.status_code == 401, res.text


async def test_brain_create_csrf_403(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _authed(session, api_client)
    res = await api_client.post(_brain_url(), json={"category": "goals", "title": "T"})
    assert res.status_code == 403, res.text


async def test_brain_create_foreign_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(
        _brain_url(BIZ_OTHER), json={"category": "goals", "title": "T"}, headers=headers
    )
    assert res.status_code == 404, res.text


# --- Project PATCH ---


async def test_project_patch_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(
        _project_url(),
        json={
            "title": "New title",
            "goal": "New goal",
            "status": "review",
            "budget": "100.50",
            "aiBudget": 20,
            "deadline": "30 May 2026",
            "teamIds": ["ag_1", "ag_2"],
            "milestones": [{"label": "M1", "done": False}],
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "New title" and body["status"] == "review"
    assert body["contract"] == "v1"
    assert body["aiSpent"] in ("0", "0.00", 0, 0.0)

    row = await projects_repo.get(session, BIZ, "proj_1")
    assert row is not None and row.title == "New title" and float(row.budget) == 100.50
    assert [a for a in row.team_ids] == ["ag_1", "ag_2"]
    events = await _system_events(session, BIZ)
    assert len(events) == 1 and events[0].project_id == "proj_1"


async def test_project_patch_bad_status(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_project_url(), json={"status": "teleporting"}, headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "invalid_enum"


async def test_project_patch_forbidden_fields(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Derived/executor fields are never accepted on write (extra=forbid)."""
    headers = await _authed(session, api_client)
    for payload in (
        {"aiSpent": 5},
        {"progress": 50},
        {"id": "proj_x"},
        {"businessId": BIZ_OTHER},
    ):
        res = await api_client.patch(_project_url(), json=payload, headers=headers)
        assert res.status_code == 422, (payload, res.text)
        assert res.json()["error"]["code"] == "validation_error", (payload, res.text)


async def test_project_patch_dangling_team(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_project_url(), json={"teamIds": ["ag_ghost"]}, headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "dangling_reference"


async def test_project_patch_negative_budget(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_project_url(), json={"budget": -10}, headers=headers)
    assert res.status_code == 422, res.text


async def test_project_patch_missing_and_foreign(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(
        _project_url(project_id="ghost"), json={"title": "T"}, headers=headers
    )
    assert res.status_code == 404, res.text
    res = await api_client.patch(_project_url(BIZ_OTHER), json={"title": "T"}, headers=headers)
    assert res.status_code == 404, res.text


async def test_project_patch_auth(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    res = await api_client.patch(_project_url(), json={"title": "T"})
    assert res.status_code == 401, res.text
    await _authed(session, api_client)
    res = await api_client.patch(_project_url(), json={"title": "T"})
    assert res.status_code == 403, res.text


# --- Task PATCH ---


async def test_task_patch_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(
        _task_url(),
        json={
            "title": "Retitled",
            "summary": "New summary",
            "etaMinutes": 45,
            "ownerAgentId": "ag_2",
            "gateAt": 80,
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "Retitled" and body["ownerAgentId"] == "ag_2"
    assert body["etaMinutes"] == 45 and body["gateAt"] == 80
    assert body["status"] == "not_started" and body["contract"] == "v1"
    assert body["dependsOn"] == []

    row = await tasks_repo.get(session, BIZ, "t_1")
    assert row is not None and row.summary == "New summary"
    events = await _system_events(session, BIZ)
    assert len(events) == 1 and events[0].project_id == "proj_1"


async def test_task_patch_forbidden_fields(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Execution-owned fields can never change through metadata patch."""
    headers = await _authed(session, api_client)
    for payload in (
        {"status": "completed"},
        {"progress": 100},
        {"costAccrued": 5},
        {"attempts": 3},
        {"approvalGateId": "ap_1"},
        {"costRate": 1},
        {"projectId": "proj_2"},
    ):
        res = await api_client.patch(_task_url(), json=payload, headers=headers)
        assert res.status_code == 422, (payload, res.text)


async def test_task_patch_dangling_owner(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_task_url(), json={"ownerAgentId": "ag_ghost"}, headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "dangling_reference"


async def test_task_patch_missing_and_scope(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_task_url(task_id="ghost"), json={"title": "T"}, headers=headers)
    assert res.status_code == 404, res.text
    # Wrong project scope for an existing task is a uniform 404.
    res = await api_client.patch(
        _task_url(project_id="proj_2"), json={"title": "T"}, headers=headers
    )
    assert res.status_code == 404, res.text
    res = await api_client.patch(_task_url(BIZ_OTHER), json={"title": "T"}, headers=headers)
    assert res.status_code == 404, res.text


async def test_task_patch_auth(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    res = await api_client.patch(_task_url(), json={"title": "T"})
    assert res.status_code == 401, res.text
    await _authed(session, api_client)
    res = await api_client.patch(_task_url(), json={"title": "T"})
    assert res.status_code == 403, res.text


async def test_writes_preserve_spend_invariant(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Metadata patches never move money: aiSpent stays a pure recompute."""
    headers = await _authed(session, api_client)
    res = await api_client.patch(_project_url(), json={"budget": 9999}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["aiSpent"] in ("0", "0.00", 0, 0.0)
    res = await api_client.patch(_task_url(), json={"title": "T2"}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["costAccrued"] in ("0", "0.00", 0, 0.0)
    proj = await projects_repo.get(session, BIZ, "proj_1")
    assert proj is not None and float(proj.ai_spent) == 0.0
