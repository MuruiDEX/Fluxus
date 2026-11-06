"""S2.2.4.2 executor claim integration + lease heartbeat (PostgreSQL).

The heartbeat is exercised against real PostgreSQL for ownership predicates
and timestamps. Lifecycle tests use event-driven synchronization (no arbitrary
sleeps): a provider blocks on an event that the test/heartbeat controls.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution import executor as executor_module
from app.execution import heartbeat as heartbeat_module
from app.execution.executor import ExecutionRequest, execute_task
from app.execution.heartbeat import LOST_HEARTBEAT_ERROR, LOST_OWNERSHIP, LeaseHeartbeat
from app.llm.mock import MockProvider
from app.persistence.engine import session_factory
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import TaskOwnership, TaskRepository
from tests.factories import BIZ, BIZ_OTHER, make_task, make_world
from tests.factories import tasks as tasks_repo

REPO = TaskRepository()


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {
        "execution_lease_ttl_s": 300.0,
        "execution_heartbeat_interval_s": 60.0,
        "execution_lease_wall_s": 2400.0,
    }
    data.update(over)
    return Settings(**data)


def _req(
    settings: Settings | None = None,
    *,
    task_id: str = "t_1",
    business_id: str = BIZ,
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
        from_statuses=("not_started",),
        to_status="working",
        lease_owner=owner,
        lease_ttl_seconds=ttl,
        lease_wall_seconds=wall,
    )
    assert ownership is not None
    return ownership


async def _current(session: AsyncSession, task_id: str = "t_1") -> Task:
    task = await tasks_repo.get(session, BIZ, task_id)
    assert task is not None
    await session.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Repository: renew_lease ownership predicates and timestamps
# ---------------------------------------------------------------------------


async def test_renew_lease_extends_expiry_only(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)

    now = datetime.now(UTC)
    await session.execute(
        update(Task)
        .where(Task.id == "t_1")
        .values(
            lease_expires_at=now + timedelta(seconds=10),
            updated_at=Task.updated_at,
        )
    )
    await session.flush()
    before = await _current(session)
    # Snapshot plain values: the conditional UPDATE below expires the ORM row.
    before_expires = before.lease_expires_at
    before_deadline = before.lease_deadline_at
    before_generation = before.execution_generation
    before_updated_at = before.updated_at

    renewal = await REPO.renew_lease(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        ttl_seconds=60,
    )

    assert renewal.renewed is True
    assert renewal.lease_expires_at is not None
    assert before_expires is not None
    assert renewal.lease_expires_at > before_expires
    assert renewal.lease_deadline_at == before_deadline
    after = await _current(session)
    assert after.lease_expires_at == renewal.lease_expires_at
    assert after.lease_deadline_at == before_deadline
    assert after.execution_generation == before_generation == 1
    assert after.status == "working"
    assert after.updated_at == before_updated_at


@pytest.mark.parametrize(
    "case",
    ["business", "task", "generation", "owner", "status"],
)
async def test_renew_lease_rejects_wrong_identity(session: AsyncSession, case: str) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)

    business_id, task_id = BIZ, "t_1"
    generation = ownership.execution_generation
    owner = ownership.lease_owner
    if case == "business":
        business_id = BIZ_OTHER
    elif case == "task":
        task_id = "t_missing"
    elif case == "generation":
        generation += 1
    elif case == "owner":
        owner = "worker-b"
    else:
        await session.execute(
            update(Task).where(Task.id == "t_1").values(status="failed", updated_at=Task.updated_at)
        )
        await session.flush()

    renewal = await REPO.renew_lease(
        session,
        business_id,
        task_id,
        execution_generation=generation,
        lease_owner=owner,
        ttl_seconds=60,
    )

    assert renewal.renewed is False


async def test_renew_lease_rejects_expired_or_overdue_lease(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    now = datetime.now(UTC)

    # Expired lease, absolute deadline still in the future: not renewable.
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
    assert not (
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner=ownership.lease_owner,
            ttl_seconds=60,
        )
    ).renewed

    # Expiry and absolute deadline both passed: never renewable.
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
    assert not (
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner=ownership.lease_owner,
            ttl_seconds=60,
        )
    ).renewed


async def test_renew_lease_clamps_to_absolute_deadline(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=60)

    await session.execute(
        update(Task)
        .where(Task.id == "t_1")
        .values(
            lease_expires_at=now + timedelta(seconds=10),
            lease_deadline_at=deadline,
            updated_at=Task.updated_at,
        )
    )
    await session.flush()

    renewal = await REPO.renew_lease(
        session,
        BIZ,
        "t_1",
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        ttl_seconds=300,  # would run past the wall
    )

    assert renewal.renewed is True
    assert renewal.lease_expires_at == renewal.lease_deadline_at == deadline
    after = await _current(session)
    assert after.lease_deadline_at == deadline


async def test_renew_lease_rejects_invalid_parameters(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)

    for ttl in (0, -1, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            await REPO.renew_lease(
                session,
                BIZ,
                "t_1",
                execution_generation=ownership.execution_generation,
                lease_owner=ownership.lease_owner,
                ttl_seconds=ttl,
            )
    with pytest.raises(ValueError):
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner="",
            ttl_seconds=60,
        )
    with pytest.raises(ValueError):
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=-1,
            lease_owner=ownership.lease_owner,
            ttl_seconds=60,
        )


# ---------------------------------------------------------------------------
# Heartbeat loop: loss signalling and shutdown
# ---------------------------------------------------------------------------


class _BrokenSession:
    async def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("database down")

    async def commit(self) -> None:
        raise AssertionError("commit must not run after a failed renewal")

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> "_BrokenSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _broken_factory() -> _BrokenSession:
    return _BrokenSession()


async def test_heartbeat_signals_ownership_lost_on_zero_rows(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    await session.commit()

    heartbeat = LeaseHeartbeat(
        session_factory=session_factory(session.bind),  # type: ignore[arg-type]
        ownership=ownership,
        ttl_seconds=0.2,
        interval_seconds=0.01,
    )
    await session.execute(
        update(Task)
        .where(Task.id == "t_1")
        .values(lease_owner="intruder", updated_at=Task.updated_at)
    )
    await session.commit()

    heartbeat.start()
    await asyncio.wait_for(heartbeat.wait_lost(), 5.0)
    await heartbeat.stop()

    assert heartbeat.ownership_lost is True
    assert heartbeat.lost_reason == LOST_OWNERSHIP
    assert heartbeat.running is False
    assert heartbeat.renewal_count == 0


async def test_heartbeat_db_error_is_not_a_successful_renewal(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    await session.commit()

    heartbeat = LeaseHeartbeat(
        session_factory=_broken_factory,  # type: ignore[arg-type]
        ownership=ownership,
        ttl_seconds=0.2,
        interval_seconds=0.01,
    )
    heartbeat.start()
    await asyncio.wait_for(heartbeat.wait_lost(), 5.0)
    await heartbeat.stop()

    assert heartbeat.ownership_lost is True
    assert heartbeat.lost_reason == LOST_HEARTBEAT_ERROR
    assert heartbeat.renewal_count == 0
    assert heartbeat.running is False


def test_heartbeat_validates_timing() -> None:
    ownership = TaskOwnership(
        business_id=BIZ,
        task_id="t_1",
        execution_generation=1,
        lease_owner="worker-a",
        lease_expires_at=datetime.now(UTC),
        lease_deadline_at=datetime.now(UTC),
    )
    for ttl, interval in (
        (0, 1),
        (-1, 1),
        (math.nan, 1),
        (math.inf, 1),
        (1, 0),
        (1, math.inf),
        (1, 1),
        (1, 2),
    ):
        with pytest.raises(ValueError):
            LeaseHeartbeat(
                session_factory=_broken_factory,  # type: ignore[arg-type]
                ownership=ownership,
                ttl_seconds=ttl,
                interval_seconds=interval,
            )


# ---------------------------------------------------------------------------
# Claim integration and lifecycle
# ---------------------------------------------------------------------------


async def test_production_execution_claims_before_provider(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    observed: dict[str, object] = {}

    class ObservingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            # A provider never shares the executor's session: inspect with an
            # independent one so no transaction is opened on the executor path.
            async with factory() as watcher:
                task = await tasks_repo.get(watcher, BIZ, "t_1")
                assert task is not None
                observed.update(
                    status=task.status,
                    generation=task.execution_generation,
                    owner=task.lease_owner,
                    expires=task.lease_expires_at,
                    deadline=task.lease_deadline_at,
                )
            return await super().complete(request)

    provider = ObservingProvider(texts=['{"summary": "claimed"}'])

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert len(provider.calls) == 1
    assert observed["status"] == "working"
    assert observed["generation"] == 1
    assert str(observed["owner"]).startswith("executor:")
    assert observed["expires"] is not None and observed["deadline"] is not None

    final = await _current(session)
    assert final.status == "completed"
    assert final.execution_generation == 1
    assert final.lease_owner == observed["owner"]


async def test_failed_claim_never_invokes_provider(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    original_claim = executor_module._tasks.claim_owned

    async def losing_claim(*args, **kwargs):  # type: ignore[no-untyped-def]
        # A competing worker wins the row between Phase 0 and our claim; our
        # own conditional claim then matches zero rows.
        async with factory() as rival:
            won = await REPO.claim_owned(
                rival,
                BIZ,
                "t_1",
                from_statuses=("not_started",),
                to_status="working",
                lease_owner="rival",
                lease_ttl_seconds=300,
                lease_wall_seconds=2400,
            )
            assert won is not None
            await rival.commit()
        return await original_claim(*args, **kwargs)

    monkeypatch.setattr(executor_module._tasks, "claim_owned", losing_claim)
    provider = MockProvider(texts=['{"summary": "must not run"}'])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "already_running"
    assert provider.calls == []
    task = await _current(session)
    assert task.execution_generation == 1
    assert task.lease_owner == "rival"


async def test_claim_is_business_scoped(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    provider = MockProvider(texts=['{"summary": "x"}'])

    result = await execute_task(session, provider, _req(business_id=BIZ_OTHER))

    assert result.outcome == "refused" and result.error_code == "not_found"
    assert provider.calls == []
    task = await _current(session)
    assert task.status == "not_started"
    assert task.execution_generation == 0


async def test_execution_heartbeat_renews_while_provider_runs(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)

    renewed = asyncio.Event()
    snapshots: list[dict[str, object]] = []
    original_renew = heartbeat_module._tasks.renew_lease

    async def renew_and_observe(*args, **kwargs):  # type: ignore[no-untyped-def]
        renewal = await original_renew(*args, **kwargs)
        if renewal.renewed:
            renew_session = args[0]
            row = (
                await renew_session.execute(
                    select(
                        Task.status,
                        Task.execution_generation,
                        Task.lease_deadline_at,
                        Task.updated_at,
                    ).where(Task.id == "t_1")
                )
            ).one()
            snapshots.append(
                {
                    "status": row[0],
                    "generation": row[1],
                    "deadline": row[2],
                    "updated_at": row[3],
                    "expires_at": renewal.lease_expires_at,
                }
            )
            renewed.set()
        return renewal

    monkeypatch.setattr(heartbeat_module._tasks, "renew_lease", renew_and_observe)

    observed: dict[str, object] = {}

    class WaitingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            async with factory() as watcher:
                task = await tasks_repo.get(watcher, BIZ, "t_1")
                assert task is not None
                observed.update(
                    generation=task.execution_generation,
                    deadline=task.lease_deadline_at,
                    updated_at=task.updated_at,
                )
            await asyncio.wait_for(renewed.wait(), 5.0)
            return await super().complete(request)

    provider = WaitingProvider(texts=['{"summary": "renewed"}'])
    settings = _settings(execution_heartbeat_interval_s=0.01, execution_lease_ttl_s=60)

    result = await execute_task(session, provider, _req(settings=settings))

    assert result.outcome == "completed"
    assert snapshots, "heartbeat never renewed during the provider call"
    for snapshot in snapshots:
        assert snapshot["status"] == "working"
        assert snapshot["generation"] == observed["generation"] == 1
        assert snapshot["deadline"] == observed["deadline"]
        assert snapshot["updated_at"] == observed["updated_at"]
        assert snapshot["expires_at"] <= snapshot["deadline"]  # type: ignore[operator]
    final = await _current(session)
    assert final.status == "completed"
    assert final.execution_generation == 1


async def test_heartbeat_stops_and_is_awaited_after_completion(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    instances: list[LeaseHeartbeat] = []
    original_start = LeaseHeartbeat.start

    def recording_start(self: LeaseHeartbeat) -> None:
        instances.append(self)
        original_start(self)

    monkeypatch.setattr(LeaseHeartbeat, "start", recording_start)
    provider = MockProvider(texts=['{"summary": "done"}'])

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert len(instances) == 1
    assert instances[0].running is False


async def test_heartbeat_stops_on_provider_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm.types import ProviderError

    await make_world(session)
    await make_task(session, status="not_started")
    instances: list[LeaseHeartbeat] = []
    original_start = LeaseHeartbeat.start

    def recording_start(self: LeaseHeartbeat) -> None:
        instances.append(self)
        original_start(self)

    monkeypatch.setattr(LeaseHeartbeat, "start", recording_start)
    provider = MockProvider(errors=[ProviderError("boom")])

    result = await _execute(session, provider, _req())

    assert result.outcome == "failed"
    assert len(instances) == 1
    assert instances[0].running is False


async def test_heartbeat_stops_on_cancellation(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    instances: list[LeaseHeartbeat] = []
    original_start = LeaseHeartbeat.start

    def recording_start(self: LeaseHeartbeat) -> None:
        instances.append(self)
        original_start(self)

    monkeypatch.setattr(LeaseHeartbeat, "start", recording_start)
    entered = asyncio.Event()
    provider = MockProvider(latency_s=30.0)
    real_complete = provider.complete

    async def gated(request):  # type: ignore[no-untyped-def]
        entered.set()
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", gated)
    run = asyncio.ensure_future(execute_task(session, provider, _req()))
    await asyncio.wait_for(entered.wait(), 5.0)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert len(instances) == 1
    assert instances[0].running is False
    task = await _current(session)
    assert task.status == "not_started"  # normal cancellation compensation
    assert task.attempts == 0


async def test_ownership_loss_is_propagated_without_persist(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)

    class TakeoverProvider(MockProvider):
        def __init__(self) -> None:
            super().__init__(texts=['{"summary": "must not persist"}'])
            self.release = asyncio.Event()

        async def complete(self, request):  # type: ignore[no-untyped-def]
            self.calls.append(request)
            # Simulate another owner taking the row after our claim.
            async with factory() as takeover:
                async with takeover.begin():
                    await takeover.execute(
                        update(Task)
                        .where(Task.id == "t_1")
                        .values(
                            lease_owner="intruder",
                            execution_generation=Task.execution_generation + 1,
                            updated_at=Task.updated_at,
                        )
                    )
            # The heartbeat should cancel this wait by proving loss.
            await asyncio.wait_for(self.release.wait(), timeout=5.0)
            return await super().complete(request)

    provider = TakeoverProvider()
    settings = _settings(execution_heartbeat_interval_s=0.01, execution_lease_ttl_s=60)

    result = await execute_task(session, provider, _req(settings=settings))

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost"
    assert result.ownership_lost is True
    assert len(provider.calls) == 1
    task = await _current(session)
    assert task.status == "working"  # no stale success/failure write
    assert task.execution_generation == 2
    assert task.lease_owner == "intruder"
    reservations = (
        (await session.execute(select(BudgetReservation).where(BudgetReservation.task_id == "t_1")))
        .scalars()
        .all()
    )
    assert len(reservations) == 1
    # The provider may have received the request: held, never released.
    assert reservations[0].status == "ambiguous"
    assert reservations[0].reason == "ownership_lost"


async def test_heartbeat_db_failure_does_not_retry_provider(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()

    instances: list[LeaseHeartbeat] = []
    original_start = LeaseHeartbeat.start

    def recording_start(self: LeaseHeartbeat) -> None:
        instances.append(self)
        original_start(self)

    monkeypatch.setattr(LeaseHeartbeat, "start", recording_start)

    async def failing_renew(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("heartbeat store down")

    monkeypatch.setattr(heartbeat_module._tasks, "renew_lease", failing_renew)

    class WaitingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            await asyncio.wait_for(instances[0].wait_lost(), timeout=5.0)
            return await super().complete(request)

    provider = WaitingProvider(texts=['{"summary": "must not persist"}'])
    settings = _settings(execution_heartbeat_interval_s=0.01, execution_lease_ttl_s=60)

    result = await execute_task(session, provider, _req(settings=settings))

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost"
    assert result.ownership_lost is True
    assert len(provider.calls) == 1
    assert instances[0].lost_reason == LOST_HEARTBEAT_ERROR
    assert instances[0].running is False
    task = await _current(session)
    assert task.status == "working"


# ---------------------------------------------------------------------------
# Concurrency: independent sessions
# ---------------------------------------------------------------------------


async def test_second_worker_cannot_claim_or_renew(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)

    async with factory() as owner_session:
        ownership = await REPO.claim_owned(
            owner_session,
            BIZ,
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-a",
            lease_ttl_seconds=300,
            lease_wall_seconds=2400,
        )
        assert ownership is not None
        await owner_session.commit()

    async with factory() as intruder:
        stolen = await REPO.claim_owned(
            intruder,
            BIZ,
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-b",
            lease_ttl_seconds=300,
            lease_wall_seconds=2400,
        )
        assert stolen is None
        wrong_owner = await REPO.renew_lease(
            intruder,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner="worker-b",
            ttl_seconds=60,
        )
        assert wrong_owner.renewed is False
        wrong_generation = await REPO.renew_lease(
            intruder,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation + 1,
            lease_owner="worker-a",
            ttl_seconds=60,
        )
        assert wrong_generation.renewed is False
        await intruder.commit()

    async with factory() as owner_again:
        valid = await REPO.renew_lease(
            owner_again,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner="worker-a",
            ttl_seconds=60,
        )
        assert valid.renewed is True
        await owner_again.commit()


async def test_stale_generation_cannot_renew_after_ownership_change(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)

    async with factory() as owner_session:
        ownership = await REPO.claim_owned(
            owner_session,
            BIZ,
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-a",
            lease_ttl_seconds=300,
            lease_wall_seconds=2400,
        )
        assert ownership is not None
        await owner_session.commit()

    async with factory() as takeover:
        async with takeover.begin():
            await takeover.execute(
                update(Task)
                .where(Task.id == "t_1")
                .values(
                    lease_owner="worker-b",
                    execution_generation=Task.execution_generation + 1,
                    updated_at=Task.updated_at,
                )
            )

    async with factory() as stale:
        renewal = await REPO.renew_lease(
            stale,
            BIZ,
            "t_1",
            execution_generation=ownership.execution_generation,
            lease_owner="worker-a",
            ttl_seconds=60,
        )
        assert renewal.renewed is False
        await stale.commit()
