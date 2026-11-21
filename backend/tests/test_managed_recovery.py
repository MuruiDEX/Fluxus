"""Managed recovery and one-shot retries on PostgreSQL."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.execution import recovery
from app.llm.types import ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.integration import IntegrationAction
from app.persistence.models.project_run import ProjectRun
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import MembershipRepository
from app.persistence.repositories.usage import BudgetReservationRepository
from tests.factories import BIZ, make_approval
from tests.test_integrations_api import _make_connection
from tests.test_project_runs import RUN, _command, _seed
from tests.test_scheduler import _due, _provider, _read, _start, _tick


async def _expired(session, *, past_wall=False):
    run = await session.scalar(select(ProjectRun))
    task = await session.get(Task, "t_1")
    now = await session.scalar(select(func.now()))
    task.status = "working"
    task.execution_generation = 1
    task.lease_owner = "lost_worker"
    task.lease_expires_at = now - timedelta(seconds=5)
    task.lease_deadline_at = now + timedelta(seconds=-1 if past_wall else 60)
    run.active_task_id, run.active_task_generation = task.id, 1
    hold = await BudgetReservationRepository().reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=task.id,
        attempt_key="crash",
        amount=Decimal("1"),
        execution_generation=1,
    )
    assert hold is not None
    await session.commit()
    return hold.id


async def _retry(client, headers, version, generation):
    return await client.post(
        RUN + "/retry",
        headers=headers,
        json={"expectedVersion": version, "taskId": "t_1", "expectedTaskGeneration": generation},
    )


@pytest.mark.parametrize("past_wall", [False, True])
async def test_crashed_claim_preserves_hold_and_no_worker_replays(
    session,
    api_client,
    test_engine,
    past_wall,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    hold_id = await _expired(session, past_wall=past_wall)
    factory = session_factory(test_engine)
    provider = _provider()
    await asyncio.gather(_tick(factory, provider), _tick(factory, provider))
    run = await _read(factory)
    assert run.state == "needs_attention"
    assert run.reason_code == ("deadline_exceeded" if past_wall else "ambiguous_execution")
    assert run.active_task_generation == 1
    assert run.recovered_task_generation == (None if past_wall else 2)
    async with factory() as db:
        task = await db.get(Task, "t_1")
        hold = await db.get(BudgetReservation, hold_id)
        assert task.execution_generation == (1 if past_wall else 2)
        assert hold.status == ("active" if past_wall else "ambiguous")
        assert hold.amount == Decimal("1")
    assert (
        await _retry(api_client, headers, run.version, task.execution_generation)
    ).status_code == 409
    assert not await _tick(factory, provider)
    assert provider.calls == []


async def test_recovery_rolls_back_as_one_transaction_and_survives_revocation(
    session,
    api_client,
    test_engine,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    hold_id = await _expired(session)
    factory = session_factory(test_engine)
    provider = _provider()

    async def fail_reconciliation(*args, **kwargs):
        raise RuntimeError("simulated recovery transaction failure")

    with monkeypatch.context() as patch:
        patch.setattr(recovery, "reconcile_task_reservations", fail_reconciliation)
        with pytest.raises(RuntimeError, match="simulated recovery"):
            await _tick(factory, provider)
    async with factory() as db:
        assert (await db.get(Task, "t_1")).execution_generation == 1
        assert (await db.get(BudgetReservation, hold_id)).status == "active"
        await MembershipRepository().revoke(db, "u_operator", BIZ)
        await db.commit()
    await _due(factory)
    assert await _tick(factory, provider)
    run = await _read(factory)
    assert run.recovered_task_generation == 2 and run.state == "needs_attention"
    assert (await _retry(api_client, headers, run.version, 2)).status_code == 404
    assert provider.calls == []


async def test_interrupted_provider_call_is_held_after_restart(
    session,
    api_client,
    test_engine,
    monkeypatch,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider()
    entered = asyncio.Event()

    async def interruptible(request):
        provider.calls.append(request)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "complete", interruptible)
    flight = asyncio.create_task(_tick(factory, provider))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    finally:
        flight.cancel()
        with pytest.raises(asyncio.CancelledError):
            await flight
    await _due(factory)
    assert await _tick(factory, provider)
    run = await _read(factory)
    assert run.reason_code == "ambiguous_execution" and run.active_task_generation == 1
    async with factory() as db:
        assert await db.scalar(select(BudgetReservation.status)) == "ambiguous"
    assert not await _tick(factory, provider)
    assert len(provider.calls) == 1


async def test_retry_is_explicit_atomic_and_limited(session, api_client, test_engine):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider(errors=[ProviderError("definite refusal")] * 3)
    assert await _tick(factory, provider)
    for generation in (1, 2):
        run = await _read(factory)
        assert run.state == "needs_attention" and run.reason_code == "task_failed"
        resume = await _command(api_client, headers, "resume", run.version)
        assert resume.status_code == 409 and resume.json()["error"]["code"] == "retry_required"
        wrong = await _retry(api_client, headers, run.version, generation + 1)
        assert wrong.status_code == 409
        replies = await asyncio.gather(
            _retry(api_client, headers, run.version, generation),
            _retry(api_client, headers, run.version, generation),
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409], [
            reply.text for reply in replies
        ]
        await asyncio.gather(_tick(factory, provider), _tick(factory, provider))
        run = await _read(factory)
        assert run.retry_task_id is None
    rejected = await _retry(api_client, headers, run.version, 3)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "retry_attempts_exhausted"
    assert len(provider.calls) == 3
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(BudgetReservation)) == 3


@pytest.mark.parametrize("external", [False, True])
async def test_explicit_reconciliation_then_retry_respects_gmail_history(
    session,
    api_client,
    test_engine,
    external,
):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    hold_id = await _expired(session)
    if external:
        await _make_connection(session)
        await make_approval(session, status="approved")
        session.add(
            IntegrationAction(
                id="send_unknown",
                business_id=BIZ,
                project_id="proj_1",
                task_id="t_1",
                approval_id="ap_1",
                connection_id="ic_1",
                capability="gmail.messages.send",
                status="unknown",
                payload_encrypted="synthetic",
            )
        )
        await session.commit()
    factory = session_factory(test_engine)
    provider = _provider()
    assert await _tick(factory, provider)
    resolved = await api_client.post(
        f"/api/v1/businesses/{BIZ}/usage/ambiguous-reservations/{hold_id}/confirm-no-charge",
        headers=headers,
        json={
            "taskId": "t_1",
            "executionGeneration": 1,
            "attemptKey": "crash",
            "evidence": "Reviewed provider request logs and invoice",
            "attestation": True,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert await _tick(factory, provider)
    run = await _read(factory)
    assert run.reason_code == "retry_required" and run.active_task_id is None
    assert provider.calls == []
    response = await _retry(api_client, headers, run.version, 2)
    if external:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "retry_external_action"
        assert not await _tick(factory, provider)
        assert provider.calls == []
    else:
        assert response.status_code == 200, response.text
        assert await _tick(factory, provider)
        assert await _tick(factory, provider)
        assert (await _read(factory)).state == "completed"
        assert len(provider.calls) == 1


@pytest.mark.parametrize("reason", ["inconsistent_lease", "task_generation_mismatch"])
async def test_corrupt_active_slot_never_replays(session, api_client, test_engine, reason):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    hold_id = await _expired(session)
    task = await session.get(Task, "t_1")
    if reason == "inconsistent_lease":
        task.lease_owner = task.lease_expires_at = task.lease_deadline_at = None
    else:
        task.execution_generation = 2
    await session.commit()
    factory = session_factory(test_engine)
    provider = _provider()
    assert await _tick(factory, provider)
    assert (await _read(factory)).reason_code == reason
    async with factory() as db:
        assert (await db.get(BudgetReservation, hold_id)).status == "active"
    assert provider.calls == []


@pytest.mark.parametrize(
    "state", ["running", "waiting_approval", "paused", "cancelled", "rejected"]
)
async def test_approval_only_wakes_runnable_intent(
    session,
    api_client,
    test_engine,
    state,
):
    headers = await _seed(session, api_client)
    await make_approval(session)
    task = await session.get(Task, "t_1")
    task.approval_gate_id = "ap_1"
    await session.commit()
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider()
    if state == "waiting_approval":
        assert await _tick(factory, provider)
        assert (await _read(factory)).state == "waiting_approval"
        assert not await _tick(factory, provider)
    if state in ("paused", "cancelled"):
        action = "pause" if state == "paused" else "cancel"
        assert (await _command(api_client, headers, action, 1)).status_code == 200
    action = "reject" if state == "rejected" else "approve"
    decision = await api_client.post(
        f"/api/v1/businesses/{BIZ}/approvals/ap_1/{action}", headers=headers
    )
    assert decision.status_code == 200
    if state in ("paused", "cancelled"):
        assert not await _tick(factory, provider)
        assert (await _read(factory)).state == state
        assert provider.calls == []
    elif state == "rejected":
        assert await _tick(factory, provider)
        assert (await _read(factory)).reason_code == "approval_rejected"
        assert provider.calls == []
    else:
        assert await _tick(factory, provider)
        assert len(provider.calls) == 1


async def test_resume_cannot_rearm_a_paused_retry(session, api_client, test_engine):
    headers = await _seed(session, api_client)
    await _start(api_client, headers)
    factory = session_factory(test_engine)
    provider = _provider(errors=[ProviderError("definite refusal")])
    await _tick(factory, provider)
    failed = await _read(factory)
    armed = await _retry(api_client, headers, failed.version, 1)
    assert armed.status_code == 200, armed.text
    paused = await _command(api_client, headers, "pause", armed.json()["version"])
    assert paused.status_code == 200
    version = paused.json()["version"]
    resume = await _command(api_client, headers, "resume", version)
    assert resume.status_code == 409 and resume.json()["error"]["code"] == "retry_required"
    assert not await _tick(factory, provider)
    assert (await _retry(api_client, headers, version, 1)).status_code == 200
    assert await _tick(factory, provider)
    assert len(provider.calls) == 2
