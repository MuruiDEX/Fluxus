"""Managed scheduling with real PostgreSQL admission and mocked providers."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.execution import executor, scheduler
from app.execution.executor import ExecutionRequest, WorkerAdmission
from app.execution.scheduler import select_task
from app.execution.worker import serve
from app.llm.mock import MockProvider
from app.llm.types import ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.approval import Approval
from app.persistence.models.project_run import ProjectRun
from app.persistence.models.task import Task, TaskDependency
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import (
    AgentRepository,
    BriefRepository,
    BusinessRepository,
    MembershipRepository,
)
from app.persistence.repositories.project_runs import get_project_run
from app.usage import pricing
from app.usage.tariffs import Tariff
from tests.factories import BIZ, approvals, make_approval, make_project, make_task
from tests.test_project_runs import _command, _seed

SETTINGS = Settings(execution_worker_poll_s=0.1, execution_worker_drain_s=2)


def _provider(count=1, **kwargs):
    return MockProvider(texts=['{"summary":"done"}'] * count, **kwargs)


async def _start(api_client, headers):
    response = await _command(api_client, headers, "start", 0)
    assert response.status_code == 200, response.text


async def _add_project(session, project_id, task_id):
    await make_project(session, project_id)
    await BriefRepository().create(
        session,
        BIZ,
        {
            "id": f"brief_{project_id}",
            "project_id": project_id,
            "goal": "Test",
            "status": "planned",
        },
    )
    await make_task(session, task_id, project_id=project_id, status="not_started")
    await session.commit()


async def _due(factory, project_id="proj_1"):
    async with factory() as session, session.begin():
        await session.execute(
            update(ProjectRun)
            .where(ProjectRun.project_id == project_id)
            .values(next_check_at=func.now())
        )


async def _read(factory, project_id="proj_1"):
    async with factory() as session:
        return await get_project_run(session, BIZ, project_id)


async def _tick(factory, provider, stop=None):
    return await scheduler.run_once(factory, SETTINGS, lambda _: provider, stop or asyncio.Event())


async def _execute_candidate(factory, candidate, provider, stopping=None):
    assert candidate is not None
    scope = candidate.scope
    async with factory() as session:
        return await executor.execute_task(
            session,
            provider,
            ExecutionRequest(
                scope.business_id,
                scope.project_id,
                candidate.task_id,
                settings=SETTINGS,
                session_factory=factory,
                worker=WorkerAdmission(
                    scope.id,
                    candidate.version,
                    candidate.generation,
                    stopping or asyncio.Event(),
                ),
            ),
        )


def test_selection_is_deterministic_and_stays_behind_human_gate():
    tasks = [
        Task(id="c", status="not_started", execution_generation=0, attempts=0),
        Task(id="b", status="not_started", execution_generation=0, attempts=0),
        Task(
            id="a",
            status="not_started",
            execution_generation=0,
            attempts=0,
            approval_gate_id="gate",
        ),
    ]
    gate = Approval(id="gate", status="pending")
    assert select_task(tasks, {}, [gate], []).task.id == "b"
    gate.status = "approved"
    action = {"approval_id": "gate", "id": "send", "status": "executing"}
    assert select_task(tasks, {}, [gate], [action]).task.id == "b"
    action["status"] = "unknown"
    assert select_task(tasks, {}, [gate], [action]).reason == "external_action_unknown"
    gate.status = "rejected"
    tasks[0].status = tasks[1].status = "completed"
    selection = select_task(tasks, {}, [gate], [])
    assert selection.task is None and selection.reason == "approval_rejected"


async def test_started_diamond_runs_independent_branch_and_waits_for_approval(
    session,
    api_client,
    test_engine,
):
    headers = await _seed(session, api_client)
    await make_approval(session)
    await make_task(
        session, "t_gated", status="not_started", depends_on=["t_1"], approval_gate_id="ap_1"
    )
    await make_task(session, "t_independent", status="not_started", depends_on=["t_1"])
    await make_task(
        session, "t_join", status="not_started", depends_on=["t_gated", "t_independent"]
    )
    await session.commit()
    factory = session_factory(test_engine)
    provider = _provider(4)
    assert not await _tick(factory, provider)
    assert provider.calls == []
    await _start(api_client, headers)
    for _ in range(3):
        assert await _tick(factory, provider)
    run = await _read(factory)
    assert run.state == "waiting_approval" and run.blocker_id == "ap_1"
    assert len(provider.calls) == 2
    assert not await _tick(factory, provider)
    async with factory() as db:
        assert (await db.get(Task, "t_independent")).status == "completed"
        assert (await db.get(Task, "t_join")).status == "not_started"
        assert await approvals.decide(db, BIZ, "ap_1", to_status="approved")
        await db.commit()
    await _due(factory)
    for _ in range(3):
        assert await _tick(factory, provider)
    assert (await _read(factory)).state == "completed"
    assert len(provider.calls) == 4
    assert not await _tick(factory, provider)


async def test_worker_concurrency_and_duplicate_discovery(
    session,
    api_client,
    test_engine,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await make_task(session, "t_second", status="not_started")
    await _add_project(session, "proj_2", "t_other")
    await _add_project(session, "proj_idle", "idle_task")
    await _start(api_client, headers)
    url = f"/api/v1/businesses/{BIZ}/projects/proj_2/run/start"
    response = await api_client.post(url, headers=headers, json={"expectedVersion": 0})
    assert response.status_code == 200, response.text
    factory = session_factory(test_engine)
    provider = _provider(3)
    complete = provider.complete
    entered, release, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
    arrivals = 0

    async def hold(request):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            entered.set()
        await release.wait()
        return await complete(request)

    monkeypatch.setattr(provider, "complete", hold)
    worker = asyncio.create_task(
        serve(factory, SETTINGS, stopping, provider_factory=lambda _: provider)
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        one = await _read(factory)
        two = await _read(factory, "proj_2")
        assert one.active_task_id == "t_1" and two.active_task_id == "t_other"
        await _due(factory)
        assert await _tick(factory, provider)
        assert arrivals == 2
        stopping.set()
    finally:
        stopping.set()
        release.set()
        await asyncio.wait_for(worker, 10)
    async with factory() as db:
        assert (await db.get(Task, "t_second")).status == "not_started"
        assert (await db.get(Task, "idle_task")).status == "not_started"
    assert len(provider.calls) == 2


async def test_complete_graph_includes_task_after_first_200(session, api_client, test_engine):
    headers = await _seed(session, api_client, status="completed")
    session.add_all(
        [
            Task(
                id=f"done_{index:03}",
                business_id=BIZ,
                project_id="proj_1",
                title="Done",
                owner_agent_id="ag_1",
                status="completed",
            )
            for index in range(201)
        ]
    )
    await make_task(session, "zzz", status="not_started")
    await session.commit()
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider()
    assert await _tick(factory, provider)
    assert await _tick(factory, provider)
    assert len(provider.calls) == 1 and (await _read(factory)).state == "completed"


@pytest.mark.parametrize("reason", ["cyclic_plan", "invalid_plan"])
async def test_corrupt_graph_parks_run(session, api_client, test_engine, reason):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    dependency = "t_1"
    if reason == "invalid_plan":
        dependency = "t_other"
        await _add_project(session, "proj_2", dependency)
    session.add(TaskDependency(task_id="t_1", depends_on_id=dependency))
    await session.commit()
    factory = session_factory(test_engine)
    provider = _provider()
    assert await _tick(factory, provider)
    run = await _read(factory)
    assert (run.state, run.reason_code) == ("needs_attention", reason)
    assert provider.calls == []


@pytest.mark.parametrize("control", ["pause", "cancel", "resume", "revoked", "shutdown"])
async def test_stale_worker_cannot_admit_after_control_change(
    session,
    api_client,
    test_engine,
    control,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    scope = await scheduler.claim_due_run(factory)
    assert scope is not None
    candidate = await scheduler.prepare_candidate(factory, scope)
    stopping = asyncio.Event()
    provider = _provider()
    ready, release = asyncio.Event(), asyncio.Event()
    admission_lock = executor.lock_project_admission

    async def hold_admission(*args):
        ready.set()
        await release.wait()
        await admission_lock(*args)

    monkeypatch.setattr(executor, "lock_project_admission", hold_admission)
    flight = asyncio.create_task(_execute_candidate(factory, candidate, provider, stopping))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        if control == "revoked":
            await MembershipRepository().revoke(session, "u_operator", BIZ)
            await session.commit()
            code = "access_revoked"
        elif control == "shutdown":
            stopping.set()
            code = "worker_stopping"
        else:
            action = "pause" if control == "resume" else control
            assert (await _command(api_client, headers, action, 1)).status_code == 200
            if control == "resume":
                assert (await _command(api_client, headers, "resume", 2)).status_code == 200
            code = "run_candidate_stale"
    finally:
        release.set()
        result = await asyncio.wait_for(flight, 10)
    assert result.error_code == code
    assert provider.calls == []
    assert (await _read(factory)).active_task_id is None
    if control == "revoked":
        await _due(factory)
        assert await _tick(factory, provider)
        assert (await _read(factory)).reason_code == "access_revoked"


async def test_two_workers_with_same_candidate_admit_one_task(
    session,
    api_client,
    test_engine,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    scope = await scheduler.claim_due_run(factory)
    assert scope is not None
    candidate = await scheduler.prepare_candidate(factory, scope)
    provider = _provider()
    complete = provider.complete
    release = asyncio.Event()

    async def hold(request):
        result = await complete(request)
        await release.wait()
        return result

    monkeypatch.setattr(provider, "complete", hold)
    flights = [
        asyncio.create_task(_execute_candidate(factory, candidate, provider)) for _ in range(2)
    ]
    try:
        done, _ = await asyncio.wait(flights, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1
        result = done.pop().result()
        assert result.outcome == "already_running" or result.error_code == "run_candidate_stale"
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*flights), 10)
    assert len(provider.calls) == 1
    assert (await _read(factory)).active_task_generation == 1


async def test_expired_slot_parks_without_recovery_or_replay(session, api_client, test_engine):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    run = await get_project_run(session, BIZ, "proj_1")
    task = await session.get(Task, "t_1")
    now = await session.scalar(select(func.now()))
    task.status = "working"
    task.execution_generation = 1
    task.lease_owner = "worker_lost"
    task.lease_expires_at = now - timedelta(seconds=1)
    task.lease_deadline_at = now + timedelta(minutes=1)
    run.active_task_id = task.id
    run.active_task_generation = 1
    await session.commit()
    factory = session_factory(test_engine)
    provider = _provider()
    assert await _tick(factory, provider)
    parked = await _read(factory)
    assert parked.state == "needs_attention" and parked.reason_code == "lease_recovery_required"
    assert parked.active_task_generation == 1
    assert not await _tick(factory, provider)
    assert provider.calls == []


async def test_worker_rechecks_revoked_grants_before_tool_side_effect(
    session,
    api_client,
    test_engine,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await AgentRepository().update(session, BIZ, "ag_1", {"tools": ["approvals"]})
    await session.commit()
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = MockProvider(
        texts=[
            json.dumps(
                {
                    "summary": "Requesting approval.",
                    "tool_requests": [
                        {
                            "capability": "approvals.request",
                            "arguments": {"title": "Please approve"},
                        }
                    ]
                }
            ),
            '{"summary":"done"}',
        ]
    )
    complete = provider.complete

    async def revoke(request):
        if not provider.calls:
            async with factory() as db, db.begin():
                await AgentRepository().update(db, BIZ, "ag_1", {"tools": []})
        return await complete(request)

    monkeypatch.setattr(provider, "complete", revoke)
    assert await _tick(factory, provider)
    assert len(provider.calls) == 2
    assert "not_authorized" in provider.calls[1].messages[-1].content
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Approval)) == 0


@pytest.mark.parametrize("kind", ["budget", "failed", "ambiguous"])
async def test_worker_parks_without_replaying_failed_attempts(
    session,
    api_client,
    test_engine,
    kind,
):
    headers = await _seed(session, api_client)
    if kind == "budget":
        pricing.register_tariff(Tariff("mock", "mock", Decimal("5"), Decimal("10")))
        await BusinessRepository().update(session, BIZ, BIZ, {"ai_budget": Decimal("0.01")})
        await session.commit()
    await _start(api_client, headers)
    error = ProviderError("synthetic failure")
    error.ambiguous = kind == "ambiguous"
    provider = _provider(errors=[error])
    factory = session_factory(test_engine)
    assert await _tick(factory, provider)
    run = await _read(factory)
    reasons = {
        "budget": "budget_exceeded",
        "failed": "task_failed",
        "ambiguous": "ambiguous_execution",
    }
    assert run.state == "needs_attention" and run.reason_code == reasons[kind]
    assert (run.active_task_id is not None) == (kind == "ambiguous")
    assert len(provider.calls) == (0 if kind == "budget" else 1)
    assert not await _tick(factory, provider)


@pytest.mark.parametrize("before_commit", [True, False])
async def test_restart_after_crash_does_not_duplicate_provider_calls(
    session,
    api_client,
    test_engine,
    monkeypatch,
    before_commit,
):
    headers = await _seed(session, api_client)
    await make_task(session, "t_second", status="not_started", depends_on=["t_1"])
    await session.commit()
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider(2)
    reserve = executor._budget_reservations.reserve

    async def crash(*args, **kwargs):
        if before_commit:
            await reserve(*args, **kwargs)
        raise RuntimeError("simulated worker termination")

    with monkeypatch.context() as patch:
        if before_commit:
            patch.setattr(executor._budget_reservations, "reserve", crash)
        else:
            patch.setattr(scheduler, "_finish_candidate", crash)
        with pytest.raises(RuntimeError, match="simulated worker"):
            await _tick(factory, provider)
    assert len(provider.calls) == (0 if before_commit else 1)
    run = await _read(factory)
    assert run.active_task_id == (None if before_commit else "t_1")
    async with factory() as db:
        count = await db.scalar(select(func.count()).select_from(BudgetReservation))
        assert count == (0 if before_commit else 1)
    await _due(factory)
    for _ in range(3):
        await _tick(factory, provider)
    assert len(provider.calls) == 2
    assert (await _read(factory)).state == "completed"
