"""PostgreSQL integration tests for explicit intent and claim fencing."""

import asyncio

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import briefs
from app.core.passwords import hash_password
from app.execution import executor
from app.llm.mock import MockProvider
from app.persistence.models.activity_event import ActivityEvent
from app.persistence.models.project_run import ProjectRun
from app.persistence.models.task import Task
from app.persistence.models.user import User
from app.persistence.repositories.concrete import (
    ActivityRepository,
    BriefRepository,
    MembershipRepository,
)
from app.persistence.repositories.project_runs import get_project_run
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_business, make_project, make_task, make_world
from tests.test_finalize import _final_text, _final_url

PROJECT = f"/api/v1/businesses/{BIZ}/projects/proj_1"
RUN = PROJECT + "/run"
TASK = PROJECT + "/tasks/t_1"


async def _seed(session: AsyncSession, api_client: httpx.AsyncClient, *, status="not_started"):
    await make_world(session)
    await make_task(session, status=status)
    await BriefRepository().create(
        session,
        BIZ,
        {"id": "brief_1", "goal": "Grow fast", "project_id": "proj_1", "status": "planned"},
    )
    await seed_operator(session)
    return {"X-CSRF-Token": await login_as(api_client)}


async def _command(client, headers, action, version, **fields):
    return await client.post(
        f"{RUN}/{action}", headers=headers, json={"expectedVersion": version, **fields}
    )


async def _count_runs(session):
    return await session.scalar(select(func.count()).select_from(ProjectRun))


async def test_run_foreign_keys_enforce_project_and_active_task_scope(session):
    await make_world(session)
    await make_business(session, BIZ_OTHER)
    await make_project(session, "proj_2")
    await make_task(session, project_id="proj_2")
    await session.commit()
    for values in (
        {"business_id": BIZ_OTHER},
        {"business_id": BIZ, "active_task_id": "t_1", "active_task_generation": 1},
        {"business_id": BIZ, "active_task_generation": 1},
    ):
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    ProjectRun(
                        id="run_bad",
                        project_id="proj_1",
                        authorizing_user_id="u_operator",
                        state="running",
                        version=1,
                        **values,
                    )
                )


async def test_explicit_start_duplicate_and_versioned_controls(session, api_client):
    headers = await _seed(session, api_client)
    idle = (await api_client.get(RUN)).json()
    assert (idle["state"], idle["version"], idle["schedulingMode"]) == ("idle", 0, "manual")
    assert await _count_runs(session) == 0

    first, duplicate = await asyncio.gather(
        _command(api_client, headers, "start", 0), _command(api_client, headers, "start", 0)
    )
    assert first.status_code == duplicate.status_code == 200
    started = first.json()
    assert started == duplicate.json() == (await api_client.get(RUN)).json()
    assert started["state"] == "running" and started["schedulingMode"] == "managed"
    assert started["version"] == 1 and started["activeTaskId"] is None
    assert await _count_runs(session) == 1

    stale = await _command(api_client, headers, "pause", 0)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "run_version_conflict"
    for action, version, state in [
        ("pause", 1, "paused"),
        ("resume", 2, "running"),
        ("cancel", 3, "cancelled"),
    ]:
        response = await _command(api_client, headers, action, version)
        assert response.status_code == 200, response.text
        assert (response.json()["state"], response.json()["version"]) == (state, version + 1)
    assert (await _command(api_client, headers, "resume", 4)).status_code == 409
    assert (await _command(api_client, headers, "start", 0)).status_code == 409
    assert await session.scalar(select(func.count()).select_from(ActivityEvent)) == 4
    run = await get_project_run(session, BIZ, "proj_1")
    assert run.authorizing_user_id == "u_operator" and run.next_check_at is None


async def test_auth_csrf_scope_revocation_and_owner_takeover(session, api_client):
    assert (await api_client.get(RUN)).status_code == 401
    owner_headers = await _seed(session, api_client)
    assert (await _command(api_client, {}, "start", 0)).status_code == 403
    await make_business(session, BIZ_OTHER)
    session.add(
        User(
            id="u_member",
            email="member@example.com",
            password_hash=hash_password("member-pass-1"),
            display_name="Member",
        )
    )
    await session.flush()
    memberships = MembershipRepository()
    await memberships.grant(session, "u_member", BIZ)
    await session.commit()
    foreign = await api_client.get(RUN.replace(BIZ, BIZ_OTHER))
    assert foreign.status_code == 404
    assert (await api_client.get(RUN.replace("proj_1", "missing"))).status_code == 404

    async with httpx.AsyncClient(
        transport=api_client._transport, base_url=str(api_client.base_url)
    ) as member:
        member_headers = {
            "X-CSRF-Token": await login_as(member, "member@example.com", "member-pass-1")
        }
        assert (await _command(api_client, owner_headers, "start", 0)).status_code == 200
        assert (await member.get(RUN)).json()["allowedActions"] == []
        assert (await _command(member, member_headers, "pause", 1)).status_code == 403
        # The other member cannot authorize a resume until they become owner.
        assert (await _command(api_client, owner_headers, "pause", 1)).status_code == 200
        await memberships.revoke(session, "u_operator", BIZ)
        await session.commit()
        assert (await _command(api_client, owner_headers, "resume", 2)).status_code == 404
        await memberships.revoke(session, "u_member", BIZ)
        await memberships.grant(session, "u_member", BIZ, role="owner")
        await session.commit()
        assert (await _command(member, member_headers, "resume", 2)).status_code == 409
        resumed = await _command(member, member_headers, "resume", 2, assumeResponsibility=True)
        assert resumed.status_code == 200, resumed.text
        run = await get_project_run(session, BIZ, "proj_1")
        assert run.authorizing_user_id == "u_member"


async def test_managed_task_edits_and_old_browser_claims(session, api_client, monkeypatch):
    headers = await _seed(session, api_client)
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda _: provider)
    assert (await _command(api_client, headers, "start", 0)).status_code == 200
    response = await api_client.post(TASK + "/executions", headers=headers, json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "run_managed"
    assert (await api_client.post(TASK + "/recover", headers=headers)).status_code == 409
    assert provider.calls == []
    rejected = await api_client.patch(TASK, headers=headers, json={"summary": "new prompt"})
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "run_plan_locked"
    assert (
        await api_client.patch(PROJECT, headers=headers, json={"goal": "new goal"})
    ).status_code == 409
    assert (
        await api_client.patch(TASK, headers=headers, json={"etaMinutes": 10})
    ).status_code == 200
    assert (
        await api_client.patch(PROJECT, headers=headers, json={"deadline": "later"})
    ).status_code == 200
    finalize = await api_client.post(_final_url("brief_1"), headers=headers)
    assert finalize.status_code == 409 and finalize.json()["error"]["code"] == "run_plan_locked"

    assert (await _command(api_client, headers, "pause", 1)).status_code == 200
    # Simulate a previously admitted lease draining after a pause.
    task = await session.get(Task, "t_1")
    task.status = "working"
    await session.commit()
    assert (await api_client.patch(TASK, headers=headers, json={"title": "new"})).status_code == 409
    task.status = "failed"
    await session.commit()
    assert (await api_client.patch(TASK, headers=headers, json={"title": "new"})).status_code == 200
    assert (await _command(api_client, headers, "resume", 2)).status_code == 409
    assert (await _command(api_client, headers, "resume", 3)).status_code == 200


async def test_manual_claim_commits_before_start(session, api_client, monkeypatch):
    headers = await _seed(session, api_client)
    provider = MockProvider(texts=['{"summary": "done"}'])
    complete = provider.complete
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(request):
        entered.set()
        await release.wait()
        return await complete(request)

    monkeypatch.setattr(provider, "complete", hold)
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda _: provider)
    flight = asyncio.create_task(api_client.post(TASK + "/executions", headers=headers, json={}))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        refused = await _command(api_client, headers, "start", 0)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "run_task_in_flight"
        assert (await api_client.post(_final_url("brief_1"), headers=headers)).status_code == 409
        assert await _count_runs(session) == 0
    finally:
        release.set()
        completed = await asyncio.wait_for(flight, 10)
    assert completed.status_code == 200 and completed.json()["outcome"] == "completed"


@pytest.mark.parametrize("action", ["start", "pause"])
async def test_run_control_wins_against_old_browser(session, api_client, monkeypatch, action):
    headers = await _seed(session, api_client)
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda _: provider)
    if action == "pause":
        assert (await _command(api_client, headers, "start", 0)).status_code == 200

        # Model a stale preflight in a request that entered before Start.
        async def stale_read(*_):
            return None

        monkeypatch.setattr(executor, "get_project_run", stale_read)

    committing, claim_attempt, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    append = ActivityRepository.append
    admission = executor.lock_project_admission

    async def hold_commit(self, *args):
        event = await append(self, *args)
        committing.set()
        await release.wait()
        return event

    async def enter_admission(*args):
        claim_attempt.set()
        await admission(*args)

    monkeypatch.setattr(ActivityRepository, "append", hold_commit)
    monkeypatch.setattr(executor, "lock_project_admission", enter_admission)
    control = asyncio.create_task(_command(api_client, headers, action, int(action == "pause")))
    claim = None
    try:
        await asyncio.wait_for(committing.wait(), 10)
        claim = asyncio.create_task(api_client.post(TASK + "/executions", headers=headers, json={}))
        await asyncio.wait_for(claim_attempt.wait(), 10)
        assert not claim.done()
    finally:
        release.set()
        assert (await asyncio.wait_for(control, 10)).status_code == 200
        if claim is not None:
            refused = await asyncio.wait_for(claim, 10)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "run_managed"
    assert provider.calls == []


async def test_finalization_wins_against_claim(session, api_client, monkeypatch):
    headers = await _seed(session, api_client, status="failed")
    report = MockProvider(texts=[_final_text([{"id": "t_1", "title": "T"}], failed_ids={"t_1"})])
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr(briefs, "create_provider", lambda _: report)
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda _: provider)
    committing, claim_attempt, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finalize = briefs._finalizing.finalize
    admission = executor.lock_project_admission

    async def hold_finalization(*args, **kwargs):
        committing.set()
        await release.wait()
        return await finalize(*args, **kwargs)

    async def enter_admission(*args):
        claim_attempt.set()
        await admission(*args)

    monkeypatch.setattr(briefs._finalizing, "finalize", hold_finalization)
    monkeypatch.setattr(executor, "lock_project_admission", enter_admission)
    final = asyncio.create_task(api_client.post(_final_url("brief_1"), headers=headers))
    claim = None
    try:
        await asyncio.wait_for(committing.wait(), 10)
        claim = asyncio.create_task(api_client.post(TASK + "/executions", headers=headers, json={}))
        await asyncio.wait_for(claim_attempt.wait(), 10)
    finally:
        release.set()
        response = await asyncio.wait_for(final, 10)
        assert response.status_code == 200, response.text
        if claim is not None:
            refused = await asyncio.wait_for(claim, 10)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "project_finalized"
    assert provider.calls == []
