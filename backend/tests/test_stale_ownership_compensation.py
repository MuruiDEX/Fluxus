"""S2.2.4.6 slice 2: stale ownership-loss reservation compensation.

Real PostgreSQL throughout. Every reservation transition is the conditional
UPDATE owned by `BudgetReservationRepository.set_status`; these tests prove the
exact identity scope, the provider-outcome classification, accounting
neutrality, idempotency and recovery/compensation race behavior.

Test-only direct calls to `_settle_stale_reservation` exercise edge predicates
(NULL/missing/foreign/terminal reservations) that are hard to produce through a
full executor run; the main lifecycle paths use the real executor.
"""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.execution import heartbeat as heartbeat_module
from app.execution.executor import (
    ExecutionRequest,
    _Claim,
    _settle_stale_reservation,
    execute_task,
)
from app.execution.heartbeat import LeaseHeartbeat
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.mock import MockProvider
from app.llm.types import ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.project import Project
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import (
    ActivityRepository,
    TaskOwnership,
    TaskRepository,
)
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage.reconciliation import reconcile_task_reservations
from tests.factories import (
    BIZ,
    BIZ_OTHER,
    make_agent,
    make_business,
    make_project,
    make_task,
)

REPO = TaskRepository()
RESERVATIONS = BudgetReservationRepository()
AMOUNT = Decimal("1.00")
TASK_FIELDS = (
    "status",
    "attempts",
    "execution_generation",
    "lease_owner",
    "lease_expires_at",
    "lease_deadline_at",
    "summary",
    "cost_accrued",
)


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {
        "execution_lease_ttl_s": 300.0,
        "execution_heartbeat_interval_s": 60.0,
        "execution_lease_wall_s": 2400.0,
    }
    data.update(over)
    return Settings(**data)


def _req(settings: Settings | None = None, *, business_id: str = BIZ) -> ExecutionRequest:
    return ExecutionRequest(
        business_id=business_id,
        project_id="proj_1",
        task_id="t_1",
        timeout_s=10.0,
        settings=settings or _settings(),
    )


async def _world(
    session: AsyncSession,
    *,
    biz: str = BIZ,
    project_id: str = "proj_1",
    agent_id: str = "ag_1",
    budget: bool = True,
) -> None:
    """Coherent business + agent + project; budgeted by default (1.00 hold)."""
    await make_business(session, biz)
    await make_agent(session, agent_id, biz)
    await make_project(
        session,
        project_id,
        biz,
        ai_budget=Decimal("50.00") if budget else Decimal("0"),
        allow_unpriced_ai=budget,
    )


async def _claim(
    session: AsyncSession,
    *,
    task_id: str = "t_1",
    owner: str = "worker-a",
    biz: str = BIZ,
) -> TaskOwnership:
    ownership = await REPO.claim_owned(
        session,
        biz,
        task_id,
        from_statuses=("not_started", "failed"),
        to_status="working",
        lease_owner=owner,
        lease_ttl_seconds=300.0,
        lease_wall_seconds=2400.0,
    )
    assert ownership is not None
    return ownership


async def _reserve(
    session: AsyncSession,
    ownership: TaskOwnership,
    *,
    biz: str = BIZ,
    task_id: str = "t_1",
    attempt: int = 0,
    attempt_key: str | None = None,
    amount: Decimal = AMOUNT,
) -> BudgetReservation:
    hold = await RESERVATIONS.reserve(
        session,
        biz,
        project_id="proj_1",
        task_id=task_id,
        attempt=attempt,
        attempt_key=attempt_key or f"{biz}:{task_id}:a{attempt}",
        amount=amount,
        execution_generation=ownership.execution_generation,
    )
    assert hold is not None
    return hold


def _claim_of(ownership: TaskOwnership) -> _Claim:
    return _Claim(
        task_id="t_1",
        pre_status="not_started",
        pre_attempts=0,
        project_id="proj_1",
        attempt_key=f"{BIZ}:t_1:a0",
        ownership=ownership,
    )


async def _task_values(
    session: AsyncSession, *, biz: str = BIZ, task_id: str = "t_1"
) -> dict[str, object]:
    await session.rollback()
    row = (
        await session.execute(
            select(
                Task.status,
                Task.attempts,
                Task.execution_generation,
                Task.lease_owner,
                Task.lease_expires_at,
                Task.lease_deadline_at,
                Task.summary,
                Task.cost_accrued,
            ).where(Task.business_id == biz, Task.id == task_id)
        )
    ).one()
    await session.rollback()
    return dict(zip(TASK_FIELDS, row, strict=True))


async def _holds(session: AsyncSession, *, biz: str = BIZ) -> list[BudgetReservation]:
    await session.rollback()
    rows = (
        await session.execute(
            select(BudgetReservation)
            .where(BudgetReservation.business_id == biz)
            .order_by(BudgetReservation.attempt_key, BudgetReservation.id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _usage(session: AsyncSession, *, biz: str = BIZ) -> list[UsageRecord]:
    await session.rollback()
    rows = (
        await session.execute(
            select(UsageRecord)
            .where(UsageRecord.business_id == biz)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _spend(session: AsyncSession, *, biz: str = BIZ, project_id: str = "proj_1") -> Decimal:
    await session.rollback()
    value = (
        await session.execute(
            select(Project.ai_spent).where(Project.business_id == biz, Project.id == project_id)
        )
    ).scalar_one()
    await session.rollback()
    return value


async def _pending(session: AsyncSession, *, biz: str = BIZ, project_id: str = "proj_1") -> Decimal:
    await session.rollback()
    total = await RESERVATIONS.pending_total(session, biz, project_id)
    await session.rollback()
    return total


async def _supersede(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: str = "intruder",
    new_hold: bool = False,
) -> dict[str, object]:
    """Commit a newer generation/owner, optionally with a newer-generation hold."""
    async with factory() as other:
        async with other.begin():
            await other.execute(
                update(Task)
                .where(Task.business_id == BIZ, Task.id == "t_1")
                .values(
                    execution_generation=Task.execution_generation + 1,
                    lease_owner=owner,
                    updated_at=Task.updated_at,
                )
            )
            if new_hold:
                new_generation = (
                    await other.execute(
                        select(Task.execution_generation).where(
                            Task.business_id == BIZ, Task.id == "t_1"
                        )
                    )
                ).scalar_one()
                created = await RESERVATIONS.reserve(
                    other,
                    BIZ,
                    project_id="proj_1",
                    task_id="t_1",
                    attempt=1,
                    attempt_key=f"{BIZ}:t_1:a1",
                    amount=AMOUNT,
                    execution_generation=new_generation,
                )
                assert created is not None
        return await _task_values(other)


def _stale_stop(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    *,
    new_hold: bool = False,
    captured: dict[str, object] | None = None,
) -> None:
    """Supersede ownership right after the heartbeat stops, before persist."""
    original = LeaseHeartbeat.stop

    async def stop_then_supersede(self: LeaseHeartbeat) -> None:
        await original(self)
        if not self.ownership_lost:
            snapshot = await _supersede(factory, new_hold=new_hold)
            if captured is not None:
                captured.update(snapshot)

    monkeypatch.setattr(LeaseHeartbeat, "stop", stop_then_supersede)


# ---------------------------------------------------------------------------
# Lifecycle: heartbeat loss and stale persistence
# ---------------------------------------------------------------------------


async def test_heartbeat_loss_marks_only_old_hold_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await _world(session)
    await make_task(session, status="not_started", summary="original")
    await session.commit()
    factory = session_factory(test_engine)

    class TakeoverProvider(MockProvider):
        def __init__(self) -> None:
            super().__init__(texts=['{"summary": "must not persist"}'])
            self.release = asyncio.Event()

        async def complete(self, request):  # type: ignore[no-untyped-def]
            self.calls.append(request)
            await _supersede(factory)
            await asyncio.wait_for(self.release.wait(), timeout=5.0)
            return await super().complete(request)

    provider = TakeoverProvider()
    settings = _settings(
        execution_heartbeat_interval_s=0.01,
        execution_lease_ttl_s=60.0,
    )

    result = await execute_task(session, provider, _req(settings))

    assert result.outcome == "unknown" and result.error_code == "ownership_lost"
    assert result.ownership_lost is True
    assert len(provider.calls) == 1
    task = await _task_values(session)
    assert task["status"] == "working"
    assert task["attempts"] == 0
    assert task["execution_generation"] == 2
    assert task["lease_owner"] == "intruder"
    assert task["summary"] == "original"
    assert task["cost_accrued"] == Decimal("0")
    assert [e for e in await ActivityRepository().list(session, BIZ) if e.kind == "result"] == []
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous" and holds[0].reason == "ownership_lost"
    assert await _pending(session) == AMOUNT


async def test_fence_failure_after_provider_response_is_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _world(session)
    await make_task(session, status="not_started", summary="original")
    await session.commit()
    captured: dict[str, object] = {}
    _stale_stop(monkeypatch, session_factory(test_engine), captured=captured)
    provider = MockProvider(texts=['{"summary": "must not persist"}'])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown" and result.error_code == "ownership_lost"
    assert await _task_values(session) == captured
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous" and holds[0].reason == "ownership_lost"


async def test_pre_send_failure_releases_only_old_hold(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    await session.commit()
    captured: dict[str, object] = {}
    _stale_stop(monkeypatch, session_factory(test_engine), captured=captured)
    provider = MockProvider(errors=[ProviderError("connect refused before send")])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown" and result.error_code == "ownership_lost"
    assert await _task_values(session) == captured
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "released"
    assert holds[0].reason == "ownership_lost_pre_send"
    assert await _pending(session) == Decimal("0")


async def test_http_408_after_ownership_loss_keeps_only_old_hold_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 408 is ambiguous provider evidence even after the lease is superseded."""
    await _world(session)
    await make_task(session, status="not_started")
    await session.commit()
    _stale_stop(monkeypatch, session_factory(test_engine), new_hold=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(408, json={"error": {"message": "request timeout"}})

    provider = OpenAICompatProvider(
        api_key="test-only",
        default_model="mock",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown" and result.error_code == "ownership_lost"
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    holds = {hold.attempt_key: hold for hold in await _holds(session)}
    old = holds[f"{BIZ}:t_1:a0"]
    new = holds[f"{BIZ}:t_1:a1"]
    assert old.execution_generation == 1 and old.status == "ambiguous"
    # Held by Slice 2 loss compensation, never released as a pre-send failure.
    assert old.reason == "ownership_lost"
    assert new.execution_generation == 2 and new.status == "active"
    task = await _task_values(session)
    assert task["status"] == "working"
    assert task["attempts"] == 0
    assert task["execution_generation"] == 2 and task["lease_owner"] == "intruder"
    assert task["cost_accrued"] == Decimal("0")


async def test_cancellation_after_ownership_loss_is_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    entered = asyncio.Event()

    class BlockingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            entered.set()
            await asyncio.sleep(30.0)
            return await super().complete(request)

    run = asyncio.ensure_future(execute_task(session, BlockingProvider(), _req()))
    await asyncio.wait_for(entered.wait(), 5.0)
    # Supersede the task but leave the old hold `active`: cancellation itself is
    # not proof that the provider never received the request.
    await _supersede(factory)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    task = await _task_values(session)
    assert task["status"] == "working"
    assert task["execution_generation"] == 2
    assert task["lease_owner"] == "intruder"
    assert await _usage(session) == []
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous" and holds[0].reason == "ownership_lost"


async def test_heartbeat_db_failure_settles_old_hold(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    await session.commit()

    async def failing_renew(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("heartbeat store down")

    monkeypatch.setattr(heartbeat_module._tasks, "renew_lease", failing_renew)

    instances: list[LeaseHeartbeat] = []
    original_start = LeaseHeartbeat.start

    def recording_start(self: LeaseHeartbeat) -> None:
        instances.append(self)
        original_start(self)

    monkeypatch.setattr(LeaseHeartbeat, "start", recording_start)

    class WaitingProvider(MockProvider):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            await asyncio.wait_for(instances[0].wait_lost(), timeout=5.0)
            return await super().complete(request)

    result = await execute_task(
        session,
        WaitingProvider(texts=['{"summary": "must not persist"}']),
        _req(_settings(execution_heartbeat_interval_s=0.01, execution_lease_ttl_s=60.0)),
    )

    assert result.outcome == "unknown" and result.ownership_lost is True
    assert (await _task_values(session))["status"] == "working"
    assert await _usage(session) == []
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous" and holds[0].reason == "ownership_lost"


# ---------------------------------------------------------------------------
# Newer generation, NULL generation, missing/foreign/terminal rows
# ---------------------------------------------------------------------------


async def test_stale_compensation_never_touches_new_generation_hold(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    await session.commit()
    _stale_stop(monkeypatch, session_factory(test_engine), new_hold=True)
    provider = MockProvider(texts=['{"summary": "must not persist"}'])

    result = await execute_task(session, provider, _req())

    assert result.outcome == "unknown" and result.error_code == "ownership_lost"
    holds = {hold.attempt_key: hold for hold in await _holds(session)}
    old = holds[f"{BIZ}:t_1:a0"]
    new = holds[f"{BIZ}:t_1:a1"]
    assert old.execution_generation == 1 and old.status == "ambiguous"
    assert old.reason == "ownership_lost"
    assert new.execution_generation == 2 and new.status == "active"
    assert await _usage(session) == []
    task = await _task_values(session)
    assert task["execution_generation"] == 2 and task["lease_owner"] == "intruder"


async def test_null_generation_never_matches_integer_generation(
    session: AsyncSession,
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    legacy = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=AMOUNT,
        execution_generation=None,
    )
    assert legacy is not None and legacy.execution_generation is None
    legacy_id = legacy.id
    await session.commit()

    claim = _claim_of(ownership)
    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id=legacy_id, settle="ambiguous"
        )
        is False
    )
    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id=legacy_id, settle="release"
        )
        is False
    )

    # A legacy claim (no generation) must never fall back to an unscoped update.
    legacy_claim = _claim_of(
        TaskOwnership(
            business_id=BIZ,
            task_id="t_1",
            execution_generation=None,  # type: ignore[arg-type]
            lease_owner="worker-a",
            lease_expires_at=None,
            lease_deadline_at=None,
        )
    )
    assert (
        await _settle_stale_reservation(
            session, BIZ, legacy_claim, reservation_id=legacy_id, settle="ambiguous"
        )
        is False
    )

    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "active" and holds[0].reason == ""


@pytest.mark.parametrize("status", ["consumed", "released", "written_off"])
async def test_terminal_holds_are_never_changed(session: AsyncSession, status: str) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, ownership)
    hold_id = hold.id
    won = await RESERVATIONS.set_status(
        session,
        BIZ,
        hold_id,
        from_statuses=("active",),
        to_status=status,
        task_id="t_1",
        attempt_key=f"{BIZ}:t_1:a0",
        execution_generation=ownership.execution_generation,
    )
    assert won is True
    await session.commit()

    claim = _claim_of(ownership)
    for settle in ("ambiguous", "release"):
        assert (
            await _settle_stale_reservation(
                session, BIZ, claim, reservation_id=hold_id, settle=settle
            )
            is False
        )

    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == status
    assert await _usage(session) == []


async def test_repeated_compensation_is_noop_without_ledger(
    session: AsyncSession,
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, ownership)
    hold_id = hold.id
    await session.commit()
    claim = _claim_of(ownership)

    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id=hold_id, settle="ambiguous"
        )
        is True
    )
    for _ in range(3):
        assert (
            await _settle_stale_reservation(
                session, BIZ, claim, reservation_id=hold_id, settle="ambiguous"
            )
            is False
        )

    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "ambiguous"
    assert await _usage(session) == []
    assert await _pending(session) == AMOUNT


async def test_missing_reservation_and_business_scope_do_not_fall_back(
    session: AsyncSession,
) -> None:
    await _world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    await _reserve(session, ownership)

    other_project = f"proj_{BIZ_OTHER}"
    other_agent = f"ag_{BIZ_OTHER}"
    await _world(
        session,
        biz=BIZ_OTHER,
        project_id=other_project,
        agent_id=other_agent,
        budget=False,
    )
    await make_task(
        session,
        task_id="t_other",
        biz_id=BIZ_OTHER,
        project_id=other_project,
        owner_id=other_agent,
        status="not_started",
    )
    other_ownership = await _claim(session, task_id="t_other", biz=BIZ_OTHER, owner="other-worker")
    other = await RESERVATIONS.reserve(
        session,
        BIZ_OTHER,
        project_id=other_project,
        task_id="t_other",
        attempt=0,
        attempt_key=f"{BIZ_OTHER}:t_other:a0",
        amount=AMOUNT,
        execution_generation=other_ownership.execution_generation,
    )
    assert other is not None
    other_id = other.id
    await session.commit()

    claim = _claim_of(ownership)
    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id="res_missing", settle="ambiguous"
        )
        is False
    )
    # The other business's reservation id is not a fallback target either.
    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id=other_id, settle="ambiguous"
        )
        is False
    )

    own_holds = await _holds(session)
    own_statuses = [hold.status for hold in own_holds]
    other_holds = await _holds(session, biz=BIZ_OTHER)
    other_statuses = [hold.status for hold in other_holds]
    assert own_statuses == ["active"]
    assert other_statuses == ["active"]
    assert await _usage(session) == []
    assert await _usage(session, biz=BIZ_OTHER) == []


# ---------------------------------------------------------------------------
# Recovery vs compensation ordering and race
# ---------------------------------------------------------------------------


async def _prepare_takeover(
    session: AsyncSession,
) -> tuple[TaskOwnership, str, TaskOwnership]:
    await _world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, ownership)
    hold_id = hold.id
    await session.commit()
    await session.execute(
        text(
            "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
            "WHERE business_id = :b AND id = 't_1'"
        ),
        {"b": BIZ},
    )
    await session.commit()
    new = await REPO.takeover_expired(
        session,
        BIZ,
        "t_1",
        expected_execution_generation=ownership.execution_generation,
        expected_lease_owner=ownership.lease_owner,
        lease_owner="recovery:new",
        lease_ttl_seconds=300.0,
        lease_wall_seconds=2400.0,
    )
    assert new is not None
    await session.commit()
    return ownership, hold_id, new


async def test_recovery_first_makes_stale_compensation_a_noop(
    session: AsyncSession,
) -> None:
    ownership, hold_id, new = await _prepare_takeover(session)
    async with session.begin():
        reconciled = await reconcile_task_reservations(
            session, BIZ, "t_1", before_generation=new.execution_generation
        )
    assert reconciled == 1

    claim = _claim_of(ownership)
    for _ in range(2):
        assert (
            await _settle_stale_reservation(
                session, BIZ, claim, reservation_id=hold_id, settle="ambiguous"
            )
            is False
        )

    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous" and holds[0].reason == "recovery_stale"
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    assert (await _task_values(session))["execution_generation"] == 2


async def test_compensation_first_keeps_recovery_idempotent_and_budget_pending(
    session: AsyncSession,
) -> None:
    ownership, hold_id, new = await _prepare_takeover(session)
    claim = _claim_of(ownership)
    assert (
        await _settle_stale_reservation(
            session, BIZ, claim, reservation_id=hold_id, settle="ambiguous"
        )
        is True
    )
    assert await _pending(session) == AMOUNT

    async with session.begin():
        reconciled = await reconcile_task_reservations(
            session, BIZ, "t_1", before_generation=new.execution_generation
        )
    assert reconciled == 0
    assert await _pending(session) == AMOUNT
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    assert (await _task_values(session))["execution_generation"] == 2


async def test_compensation_and_recovery_race_exactly_one_transition(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    ownership, hold_id, new = await _prepare_takeover(session)
    factory = session_factory(test_engine)
    barrier = asyncio.Barrier(2)
    claim = _claim_of(ownership)

    async def compensate() -> bool:
        async with factory() as own_session:
            await barrier.wait()
            return await _settle_stale_reservation(
                own_session,
                BIZ,
                claim,
                reservation_id=hold_id,
                settle="ambiguous",
            )

    async def recover() -> int:
        async with factory() as recovery_session:
            await barrier.wait()
            async with recovery_session.begin():
                return await reconcile_task_reservations(
                    recovery_session,
                    BIZ,
                    "t_1",
                    before_generation=new.execution_generation,
                )

    compensated, recovered = await asyncio.gather(compensate(), recover())

    # Exactly one writer observed the row as `active`; the other is a safe
    # no-op. Neither path holds the task row while touching the reservation.
    assert int(compensated) + recovered == 1
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous"
    assert holds[0].reason in ("ownership_lost", "recovery_stale")
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
