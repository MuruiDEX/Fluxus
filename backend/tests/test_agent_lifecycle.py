"""Agent lifecycle (Stage 14): hire → activate/disable → assign → execute.

`Agent.hired` is the active flag (no enum change, no migration): hire sets
it true; disable sets it false; rows, links and history are never deleted.
Staffing compatibility is scope + active only — no skill model exists, so
nothing is invented. Disabled owners take no NEW work (PATCH owner,
team_ids, plan save, execution claim refuse); in-flight runs, terminal
answers and stored history are untouched.
"""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.executor import ExecutionRequest, execute_task
from app.llm.mock import MockProvider
from app.persistence.repositories.concrete import ActivityRepository, AgentRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_agent, make_task, make_world
from tests.factories import agents as agents_repo
from tests.factories import projects as projects_repo
from tests.factories import tasks as tasks_repo

_activity = ActivityRepository()
VALID = '{"summary": "Researched 12 rivals.", "findings": ["a", "b"]}'


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Seed world + task, owner operator, log in (cookie in jar)."""
    await make_world(session)
    await make_agent(session, agent_id="ag_2", status="available")
    await make_task(session, task_id="t_1", status="not_started")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _activate_url(biz: str = BIZ, agent_id: str = "ag_1") -> str:
    return f"/api/v1/businesses/{biz}/agents/{agent_id}/activate"


def _disable_url(biz: str = BIZ, agent_id: str = "ag_1") -> str:
    return f"/api/v1/businesses/{biz}/agents/{agent_id}/disable"


def _task_url(biz: str = BIZ, project_id: str = "proj_1", task_id: str = "t_1") -> str:
    return f"/api/v1/businesses/{biz}/projects/{project_id}/tasks/{task_id}"


async def _system_events(session: AsyncSession, biz: str):
    return [e for e in await _activity.list(session, biz) if e.kind == "system"]


async def _refetch(session: AsyncSession, repo, biz: str, id: str):
    """Re-read past the session identity map (API calls use their own sessions)."""
    session.expire_all()
    return await repo.get(session, biz, id)


async def test_disable_enable_roundtrip_with_audit(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_disable_url(), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["hired"] is False
    assert res.json()["contract"] == "v1"
    row = await _refetch(session, agents_repo, BIZ, "ag_1")
    assert row is not None and row.hired is False

    # Idempotent repeat: same 200, no conflict, exactly one new audit event.
    again = await api_client.post(_disable_url(), headers=headers)
    assert again.status_code == 200 and again.json()["hired"] is False
    events = [e for e in await _system_events(session, BIZ) if "ag_1 disabled" in e.text]
    assert len(events) == 2

    back = await api_client.post(_activate_url(), headers=headers)
    assert back.status_code == 200 and back.json()["hired"] is True
    row = await _refetch(session, agents_repo, BIZ, "ag_1")
    assert row is not None and row.hired is True


async def test_toggle_foreign_or_missing_is_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.post(_disable_url(BIZ_OTHER), headers=headers)).status_code == 404
    res = await api_client.post(_disable_url(BIZ, "ghost"), headers=headers)
    assert res.status_code == 404, res.text
    assert (await api_client.post(_activate_url(BIZ_OTHER), headers=headers)).status_code == 404


async def test_toggle_requires_auth_and_csrf(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    # No seed needed: missing session fails before any business lookup.
    assert (await api_client.post(_disable_url())).status_code == 401
    await _authed(session, api_client)
    assert (await api_client.post(_disable_url())).status_code == 403


async def test_assign_disabled_owner_rejected(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.post(_disable_url(BIZ, "ag_2"), headers=headers)).status_code == 200
    res = await api_client.patch(_task_url(), json={"ownerAgentId": "ag_2"}, headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "agent_disabled"
    row = await _refetch(session, agents_repo, BIZ, "ag_2")
    assert row is not None and row.hired is False
    # Re-activating re-opens staffing through the same path.
    assert (await api_client.post(_activate_url(BIZ, "ag_2"), headers=headers)).status_code == 200
    ok = await api_client.patch(_task_url(), json={"ownerAgentId": "ag_2"}, headers=headers)
    assert ok.status_code == 200, ok.text


async def test_assign_foreign_agent_is_dangling(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.patch(_task_url(), json={"ownerAgentId": "ag_ghost"}, headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "dangling_reference"


async def test_team_staffing_rejects_disabled(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.post(_disable_url(BIZ, "ag_2"), headers=headers)).status_code == 200
    res = await api_client.patch(
        f"/api/v1/businesses/{BIZ}/projects/proj_1", json={"teamIds": ["ag_2"]}, headers=headers
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "agent_disabled"


async def test_plan_rejects_disabled_owner(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Plan save path enforces the same staffing rule (via repositories)."""
    from app.persistence.repositories.concrete import PlanningRepository

    await make_world(session)
    await make_agent(session, agent_id="ag_off", status="available")
    await session.commit()
    planners = PlanningRepository()
    async with session.begin():
        agent = await agents_repo.get(session, BIZ, "ag_off")
        assert agent is not None
        agent.hired = False
        await session.flush()
    from app.persistence.exceptions import AgentDisabledError

    with pytest.raises(AgentDisabledError):
        async with session.begin():
            await planners.create_plan_graph(
                session,
                BIZ,
                {
                    "project": {"title": "P"},
                    "tasks": [{"key": "a", "title": "T", "owner_agent_id": "ag_off"}],
                },
            )
    await session.rollback()


async def test_concurrent_toggles_stay_consistent(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    results = await asyncio.gather(
        api_client.post(_disable_url(), headers=headers),
        api_client.post(_disable_url(), headers=headers),
    )
    assert sorted(r.status_code for r in results) == [200, 200]
    row = await _refetch(session, agents_repo, BIZ, "ag_1")
    assert row is not None and row.hired is False


async def test_hired_agent_executes_end_to_end(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Assignment flows into the real engine with the agent's own context."""
    await _authed(session, api_client)
    provider = MockProvider(texts=[VALID])
    result = await execute_task(
        session,
        provider,
        ExecutionRequest(business_id=BIZ, project_id="proj_1", task_id="t_1", timeout_s=10.0),
    )
    assert result.outcome == "completed", result
    assert len(provider.calls) == 1
    system_prompt = provider.calls[0].messages[0].content
    assert "Tester" in system_prompt  # owner role reaches the model
    async with session.begin():
        row = await agents_repo.get(session, BIZ, "ag_1")
        assert row is not None and row.hired is True


async def test_disabled_owner_refuses_new_claim(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _authed(session, api_client)
    provider = MockProvider(texts=[VALID])
    async with session.begin():
        agent = await agents_repo.get(session, BIZ, "ag_1")
        assert agent is not None
        agent.hired = False
        await session.flush()
    refused = await execute_task(
        session,
        provider,
        ExecutionRequest(business_id=BIZ, project_id="proj_1", task_id="t_1", timeout_s=10.0),
    )
    assert refused.outcome == "refused" and refused.error_code == "agent_disabled"
    assert provider.calls == []  # refused before any network call
    # Task untouched: still not_started, zero attempts, zero spend.
    async with session.begin():
        task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "not_started" and task.attempts == 0
    proj = await projects_repo.get(session, BIZ, "proj_1")
    assert proj is not None and float(proj.ai_spent) == 0.0


async def test_gate_still_enforced_for_active_owner(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Disabled-check ordering never masks the approval gate path."""
    await _authed(session, api_client)
    from tests.factories import make_approval

    async with session.begin():
        await make_approval(session, approval_id="ap_gate", gates_task_id="t_1")
        from tests.factories import tasks as _tasks

        await _tasks.update(session, BIZ, "t_1", {"approval_gate_id": "ap_gate"})
    provider = MockProvider(texts=[VALID])
    result = await execute_task(
        session,
        provider,
        ExecutionRequest(business_id=BIZ, project_id="proj_1", task_id="t_1", timeout_s=10.0),
    )
    # Active owner + pending gate: the gate refuses first, no LLM call.
    assert result.outcome == "refused" and result.error_code == "approval_required"
    assert provider.calls == []


async def test_lifecycle_persists_across_restart(
    session: AsyncSession, api_client: httpx.AsyncClient, test_db_url: str
) -> None:
    """Disable/enable survive an engine dispose + recreate cycle."""
    from app.persistence.engine import create_engine_from_url, session_factory

    headers = await _authed(session, api_client)
    assert (await api_client.post(_disable_url(), headers=headers)).status_code == 200
    engine = create_engine_from_url(test_db_url)
    try:
        factory = session_factory(engine)
        async with factory() as fresh:
            row = await AgentRepository().get(fresh, BIZ, "ag_1")
            assert row is not None and row.hired is False
    finally:
        await engine.dispose()
