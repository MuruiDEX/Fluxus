"""S2.2.4.3: generation/lease fencing of task execution persistence.

Real PostgreSQL throughout: the ownership fence is a conditional UPDATE whose
predicates, row locks and database-time comparisons are the subject of these
tests. Lifecycle tests synchronize on asyncio events (no arbitrary sleeps).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution import executor as executor_module
from app.execution.executor import ExecutionRequest, execute_task
from app.execution.heartbeat import LeaseHeartbeat
from app.llm.mock import MockProvider
from app.llm.types import LlmResponse, LlmUsage, ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.project import Project
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import TaskOwnership, TaskRepository
from app.persistence.repositories.spend import recalculate_project_spend
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage.settlement import record_success
from tests.factories import BIZ, BIZ_OTHER, make_task, make_world
from tests.factories import tasks as tasks_repo

REPO = TaskRepository()
RESERVATIONS = BudgetReservationRepository()


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {
        "execution_lease_ttl_s": 300.0,
        "execution_heartbeat_interval_s": 60.0,
        "execution_lease_wall_s": 2400.0,
    }
    data.update(over)
    return Settings(**data)


def _req(
    settings: Settings | None = None, *, task_id: str = "t_1", business_id: str = BIZ
) -> ExecutionRequest:
    return ExecutionRequest(
        business_id=business_id,
        project_id="proj_1",
        task_id=task_id,
        timeout_s=10.0,
        settings=settings or _settings(),
    )


async def _execute(session: AsyncSession, provider: MockProvider, req: ExecutionRequest) -> object:
    await session.commit()
    return await execute_task(session, provider, req)


async def _claim(
    session: AsyncSession,
    *,
    task_id: str = "t_1",
    owner: str = "worker-a",
    ttl: float = 300,
    wall: float = 2400,
) -> TaskOwnership:
    ownership = await REPO.claim_owned(
        session,
        BIZ,
        task_id,
        from_statuses=("not_started", "failed"),
        to_status="working",
        lease_owner=owner,
        lease_ttl_seconds=ttl,
        lease_wall_seconds=wall,
    )
    assert ownership is not None
    return ownership


async def _fresh(session: AsyncSession, task_id: str = "t_1") -> Task:
    task = await tasks_repo.get(session, BIZ, task_id)
    assert task is not None
    await session.refresh(task)
    return task


async def _spend(session: AsyncSession) -> Decimal:
    return (
        await session.execute(
            select(Project.ai_spent).where(Project.business_id == BIZ, Project.id == "proj_1")
        )
    ).scalar_one()


async def _reservations(session: AsyncSession, task_id: str = "t_1") -> list[BudgetReservation]:
    rows = (
        await session.execute(
            select(BudgetReservation).where(
                BudgetReservation.business_id == BIZ,
                BudgetReservation.task_id == task_id,
            )
        )
    ).scalars()
    return list(rows)


async def _usage(session: AsyncSession, task_id: str = "t_1") -> list[UsageRecord]:
    rows = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.business_id == BIZ, UsageRecord.task_id == task_id
            )
        )
    ).scalars()
    return list(rows)


async def _result_events(session: AsyncSession) -> list[object]:
    from app.persistence.repositories.concrete import ActivityRepository

    events = await ActivityRepository().list(session, BIZ, project_id="proj_1")
    return [event for event in events if event.kind == "result"]


async def _supersede(
    factory, *, owner: str = "intruder", ambiguous_reservation: bool = False
) -> None:
    """Commit a newer generation/owner, as a controlled takeover would."""
    async with factory() as takeover:
        async with takeover.begin():
            await takeover.execute(
                update(Task)
                .where(Task.business_id == BIZ, Task.id == "t_1")
                .values(
                    execution_generation=Task.execution_generation + 1,
                    lease_owner=owner,
                    updated_at=Task.updated_at,
                )
            )
            if ambiguous_reservation:
                await takeover.execute(
                    update(BudgetReservation)
                    .where(
                        BudgetReservation.business_id == BIZ,
                        BudgetReservation.task_id == "t_1",
                        BudgetReservation.status == "active",
                    )
                    .values(status="ambiguous", reason="test_takeover")
                )


def _stale_stop(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    """Supersede ownership right after the heartbeat stops, before persist."""
    original = LeaseHeartbeat.stop

    async def stop_then_supersede(self: LeaseHeartbeat) -> None:
        await original(self)
        if not self.ownership_lost:
            await _supersede(factory)

    monkeypatch.setattr(LeaseHeartbeat, "stop", stop_then_supersede)


# ---------------------------------------------------------------------------
# Ownership fence primitive
# ---------------------------------------------------------------------------


async def test_fence_applies_transition_only_for_exact_owner(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", summary="original")
    ownership = await _claim(session)
    await session.commit()
    before = await _fresh(session)
    before_updated = before.updated_at
    before_deadline = before.lease_deadline_at

    fenced = await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": "completed", "summary": "done", "progress": 100},
    )
    await session.commit()

    assert fenced is True
    after = await _fresh(session)
    assert after.status == "completed"
    assert after.summary == "done"
    assert after.progress == 100
    assert after.execution_generation == ownership.execution_generation == 1
    assert after.lease_owner == ownership.lease_owner
    assert after.lease_deadline_at == before_deadline
    assert after.updated_at > before_updated


@pytest.mark.parametrize("case", ["business", "task", "generation", "owner"])
async def test_fence_rejects_wrong_identity(session: AsyncSession, case: str) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    await session.commit()

    business_id, task_id = BIZ, "t_1"
    generation = ownership.execution_generation
    owner = ownership.lease_owner
    if case == "business":
        business_id = BIZ_OTHER
    elif case == "task":
        task_id = "t_missing"
    elif case == "generation":
        generation += 1
    else:
        owner = "worker-b"

    fenced = await REPO.fence_owned(
        session,
        business_id,
        task_id,
        execution_generation=generation,
        lease_owner=owner,
        values={"status": "completed"},
    )

    assert fenced is False
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == 1


async def test_fence_rejects_non_working_status(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    await session.execute(
        update(Task).where(Task.id == "t_1").values(status="failed", updated_at=Task.updated_at)
    )
    await session.flush()

    fenced = await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": "completed"},
    )

    assert fenced is False
    assert (await _fresh(session)).status == "failed"


async def test_fence_rejects_expired_lease_and_past_deadline(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    now = datetime.now(UTC)

    await session.execute(
        update(Task)
        .where(Task.id == "t_1")
        .values(
            lease_expires_at=now - timedelta(seconds=1),
            lease_deadline_at=now + timedelta(seconds=600),
            updated_at=Task.updated_at,
        )
    )
    await session.flush()
    assert not await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": "completed"},
    )

    # Absolute deadline passed. Schema enforces expires <= deadline, so a past
    # deadline necessarily comes with a past expiry too; both are checked.
    await session.execute(
        update(Task)
        .where(Task.id == "t_1")
        .values(
            lease_expires_at=now - timedelta(seconds=10),
            lease_deadline_at=now - timedelta(seconds=5),
            updated_at=Task.updated_at,
        )
    )
    await session.flush()
    assert not await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": "completed"},
    )


async def test_stale_owner_cannot_fence_after_takeover(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session, owner="worker-a")
    await session.commit()

    await _supersede(session_factory(test_engine), owner="worker-b")

    fenced = await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=stale.execution_generation,
        lease_owner="worker-a",
        values={"status": "completed"},
    )

    assert fenced is False
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == 2
    assert task.lease_owner == "worker-b"


async def test_fence_rejects_illegal_transitions_and_fields(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    kwargs = {
        "execution_generation": ownership.execution_generation,
        "lease_owner": ownership.lease_owner,
    }

    with pytest.raises(ValueError, match="only transitions from 'working'"):
        await REPO.fence_owned(
            session, BIZ, "t_1", expected_status="completed", values={"status": "failed"}, **kwargs
        )
    for target in ("working", "waiting_approval", "bogus"):
        with pytest.raises(ValueError, match="illegal fence target"):
            await REPO.fence_owned(session, BIZ, "t_1", values={"status": target}, **kwargs)
    for field in ("execution_generation", "lease_owner", "lease_expires_at", "lease_deadline_at"):
        with pytest.raises(ValueError, match="cannot write fields"):
            await REPO.fence_owned(
                session, BIZ, "t_1", values={"status": "completed", field: 1}, **kwargs
            )


# ---------------------------------------------------------------------------
# Success persistence
# ---------------------------------------------------------------------------


async def test_valid_owner_persists_fenced_success(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "done"}'])

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    task = await _fresh(session)
    assert task.status == "completed" and task.summary == "done"
    assert task.execution_generation == 1
    assert len(await _result_events(session)) == 1
    assert len(await _usage(session)) == 1
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"


async def test_stale_owner_cannot_persist_success(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", summary="original")
    await make_task(session, task_id="t_prior", status="completed", cost_accrued=Decimal("2.50"))
    await recalculate_project_spend(session, BIZ, "proj_1")
    await session.commit()
    spend_before = await _spend(session)
    await session.rollback()
    _stale_stop(monkeypatch, session_factory(test_engine))
    provider = MockProvider(texts=['{"summary": "must not persist"}'])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost"
    assert result.ownership_lost is True
    assert len(provider.calls) == 1
    task = await _fresh(session)
    assert task.status == "working"
    assert task.summary == "original"
    assert task.execution_generation == 2
    assert task.lease_owner == "intruder"
    assert await _result_events(session) == []
    assert await _usage(session) == []
    assert await _spend(session) == spend_before
    reservations = await _reservations(session)
    # The provider answered: only our exact-generation hold settles, as held.
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"
    assert reservations[0].reason == "ownership_lost"


async def test_stale_owner_cannot_persist_failure(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    _stale_stop(monkeypatch, session_factory(test_engine))
    provider = MockProvider(errors=[ProviderError("boom")])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost"
    assert result.ownership_lost is True
    task = await _fresh(session)
    assert task.status == "working"
    assert task.attempts == 0
    assert task.execution_generation == 2
    assert task.lease_owner == "intruder"
    assert await _result_events(session) == []
    assert await _usage(session) == []
    reservations = await _reservations(session)
    # The provider error proves the request was never sent: the old execution's
    # own hold may be released even though the task row is no longer ours.
    assert len(reservations) == 1 and reservations[0].status == "released"
    assert reservations[0].reason == "ownership_lost_pre_send"


async def test_persist_failure_after_fence_rolls_back_all_effects(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("ledger down")

    monkeypatch.setattr(executor_module, "settle_success", boom)
    with pytest.raises(RuntimeError, match="ledger down"):
        await _execute(session, provider, _req())

    task = await _fresh(session)
    # Rolled back, then compensated by the SAME ownership: retryable row.
    assert task.status == "not_started"
    assert task.summary == ""
    assert task.execution_generation == 1
    assert task.attempts == 0
    assert task.cost_accrued == Decimal("0")
    assert await _result_events(session) == []
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    reservations = await _reservations(session)
    # The provider answered: held, never consumed or released.
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_ownership_loss_cancellation_writes_no_stale_state(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    entered = asyncio.Event()

    class BlockingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            entered.set()
            await asyncio.sleep(30.0)
            return await super().complete(request)

    provider = BlockingProvider()
    run = asyncio.ensure_future(execute_task(session, provider, _req()))
    await asyncio.wait_for(entered.wait(), 5.0)
    # Supersede and make the hold ambiguous, then cancel while the heartbeat
    # has not ticked: the cancellation path must still fence.
    await _supersede(factory, ambiguous_reservation=True)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == 2
    assert task.lease_owner == "intruder"
    assert await _result_events(session) == []
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"


# ---------------------------------------------------------------------------
# Reservation, ledger and idempotency
# ---------------------------------------------------------------------------


async def test_reservation_cas_is_scoped_to_task_and_attempt(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    reservation = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("1.00"),
    )
    assert reservation is not None

    # Wrong task: no settlement.
    assert not await RESERVATIONS.set_status(
        session,
        BIZ,
        reservation.id,
        from_statuses=("active",),
        to_status="consumed",
        task_id="t_other",
        attempt_key=f"{BIZ}:t_1:a0",
    )
    # Wrong attempt: no settlement.
    assert not await RESERVATIONS.set_status(
        session,
        BIZ,
        reservation.id,
        from_statuses=("active",),
        to_status="consumed",
        task_id="t_1",
        attempt_key=f"{BIZ}:t_1:a9",
    )
    assert reservation.status == "active"
    # Exact scope succeeds, and cannot succeed twice.
    assert await RESERVATIONS.set_status(
        session,
        BIZ,
        reservation.id,
        from_statuses=("active",),
        to_status="consumed",
        task_id="t_1",
        attempt_key=f"{BIZ}:t_1:a0",
    )
    assert not await RESERVATIONS.set_status(
        session,
        BIZ,
        reservation.id,
        from_statuses=("active",),
        to_status="consumed",
        task_id="t_1",
        attempt_key=f"{BIZ}:t_1:a0",
    )
    assert ownership.execution_generation == 1


async def test_duplicate_settlement_does_not_double_charge(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "done"}'])
    assert (await _execute(session, provider, _req())).outcome == "completed"

    reservation = await RESERVATIONS.get_for_task(session, BIZ, "t_1")
    assert reservation is not None and reservation.status == "consumed"
    reservation_id = reservation.id
    reservation_key = reservation.idempotency_key
    attempt_key = reservation.attempt_key
    spend_before = await _spend(session)
    usage_before = len(await _usage(session))
    await session.rollback()
    response = LlmResponse(
        text='{"summary": "done"}',
        data={"summary": "done"},
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True),
        model="mock",
        attempts=1,
    )
    async with session.begin():
        _, won = await record_success(
            session,
            BIZ,
            reservation_id=reservation_id,
            reservation_key=reservation_key,
            task_id="t_1",
            project_id="proj_1",
            agent_id="ag_1",
            call_kind="task_execution",
            attempt=0,
            attempt_key=attempt_key,
            provider="mock",
            model="mock",
            response=response,
        )
    assert won is False
    assert len(await _usage(session)) == usage_before == 1
    assert (await _reservations(session))[0].status == "consumed"
    assert await _spend(session) == spend_before


async def test_completed_task_rejects_duplicate_fence(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    assert await REPO.fence_owned(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": "completed", "summary": "first"},
    )
    await session.commit()

    factory = session_factory(test_engine)
    async with factory() as duplicate:
        assert not await REPO.fence_owned(
            duplicate,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner=ownership.lease_owner,
            values={"status": "completed", "summary": "second"},
        )
        await duplicate.commit()

    task = await _fresh(session)
    assert task.status == "completed" and task.summary == "first"
    assert task.execution_generation == 1


async def test_duplicate_execution_is_not_provider_call(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "done"}'])
    assert (await _execute(session, provider, _req())).outcome == "completed"
    assert len(provider.calls) == 1

    again = await execute_task(session, provider, _req())

    assert again.outcome == "already_completed"
    assert len(provider.calls) == 1
    assert len(await _result_events(session)) == 1
    assert len(await _usage(session)) == 1


# ---------------------------------------------------------------------------
# Valid failure, normal cancellation, stale compensation
# ---------------------------------------------------------------------------


async def test_valid_owner_persists_fenced_failure(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(errors=[ProviderError("boom")])

    result = await _execute(session, provider, _req())

    assert result.outcome == "failed"
    task = await _fresh(session)
    assert task.status == "failed" and task.attempts == 1
    assert task.execution_generation == 1
    # A proven pre-send failure is still releasable under the same ownership.
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "released"
    assert await _usage(session) == []
    assert await _result_events(session) == []


async def test_normal_cancellation_compensates_under_own_ownership(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    entered = asyncio.Event()

    class BlockingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            entered.set()
            await asyncio.sleep(30.0)
            return await super().complete(request)

    provider = BlockingProvider()
    run = asyncio.ensure_future(execute_task(session, provider, _req()))
    await asyncio.wait_for(entered.wait(), 5.0)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    task = await _fresh(session)
    assert task.status == "not_started"
    assert task.execution_generation == 1
    assert task.lease_owner is not None
    # Cancellation mid-flight is possibly billed: held, never released.
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"


async def test_stale_compensation_cannot_overwrite_newer_execution(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", summary="original")
    ownership = await _claim(session, owner="worker-a")
    await session.commit()
    claim = executor_module._Claim(
        task_id="t_1",
        pre_status="not_started",
        pre_attempts=0,
        project_id="proj_1",
        attempt_key=f"{BIZ}:t_1:a0",
        ownership=ownership,
    )

    await _supersede(session_factory(test_engine), owner="worker-b")
    await session.rollback()
    assert await executor_module._compensate_claim(session, BIZ, claim) is False

    task = await _fresh(session)
    assert task.status == "working"
    assert task.summary == "original"
    assert task.execution_generation == 2
    assert task.lease_owner == "worker-b"


# ---------------------------------------------------------------------------
# Concurrency: two independent PostgreSQL sessions
# ---------------------------------------------------------------------------


async def test_concurrent_fences_exactly_one_wins(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Worker A holds the row lock; worker B's concurrent fence must lose.

    B is started (and inside its transaction) before A commits, so its UPDATE
    blocks on A's row lock and re-evaluates the status predicate after commit.
    No sleeps: the only synchronization is the event A awaits.
    """
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session, owner="worker-a")
    await session.commit()
    factory = session_factory(test_engine)
    started = asyncio.Event()

    async def contender() -> bool:
        async with factory() as other:
            async with other.begin():
                started.set()
                return await REPO.fence_owned(
                    other,
                    BIZ,
                    "t_1",
                    execution_generation=ownership.execution_generation,
                    lease_owner=ownership.lease_owner,
                    values={"status": "failed", "attempts": Task.attempts + 1},
                )

    async with session.begin():
        assert await REPO.fence_owned(
            session,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner=ownership.lease_owner,
            values={"status": "completed", "summary": "winner"},
        )
        loser = asyncio.ensure_future(contender())
        await asyncio.wait_for(started.wait(), 5.0)

    assert await asyncio.wait_for(loser, 10.0) is False

    task = await _fresh(session)
    assert task.status == "completed" and task.summary == "winner"
    assert task.attempts == 0
    assert task.execution_generation == 1
