"""S2.2.4.5: reservation generation reconciliation for task execution.

Real PostgreSQL throughout. Task-execution holds are stamped with the exact
claim generation, settlement is fenced by that generation (under the same
task → reservation lock order recovery uses), and lease takeover marks only
NULL/older-generation active holds ambiguous — never the new generation's, and
never releasing or ledger-writing anything.
"""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution.executor import ExecutionRequest, execute_task
from app.llm.mock import MockProvider
from app.llm.types import LlmResponse, LlmUsage
from app.persistence.engine import session_factory
from app.persistence.models.activity_event import ActivityEvent
from app.persistence.models.project import Project
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import TaskRepository
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage import pricing
from app.usage.reconciliation import reconcile_task_reservations
from app.usage.settlement import mark_ambiguous, record_success, release_reservation
from app.usage.tariffs import Tariff
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_project, make_task, make_world
from tests.factories import tasks as tasks_repo

REPO = TaskRepository()
RESERVATIONS = BudgetReservationRepository()

TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)


def _recover_url(task_id: str = "t_1", *, biz: str = BIZ, project_id: str = "proj_1") -> str:
    return f"/api/v1/businesses/{biz}/projects/{project_id}/tasks/{task_id}/recover"


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {
        "execution_lease_ttl_s": 300.0,
        "execution_heartbeat_interval_s": 60.0,
        "execution_lease_wall_s": 2400.0,
        "llm_max_tokens": 16,
    }
    data.update(over)
    return Settings(**data)


def _req() -> ExecutionRequest:
    return ExecutionRequest(
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        timeout_s=10.0,
        settings=_settings(),
    )


def _response() -> LlmResponse:
    return LlmResponse(
        text="ok",
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True),
    )


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


async def _claim(
    session: AsyncSession,
    *,
    task_id: str = "t_1",
    owner: str = "worker-a",
    ttl: float = 300.0,
    wall: float = 2400.0,
):
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


async def _expire(session: AsyncSession, task_id: str = "t_1") -> None:
    await session.execute(
        text(
            "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
            "WHERE business_id = :b AND id = :t"
        ),
        {"b": BIZ, "t": task_id},
    )
    await session.commit()


async def _reserve(
    session: AsyncSession,
    *,
    attempt_key: str,
    generation: int | None,
    task_id: str = "t_1",
    amount: Decimal | None = None,
    biz: str = BIZ,
) -> BudgetReservation:
    hold = await RESERVATIONS.reserve(
        session,
        biz,
        project_id="proj_1",
        task_id=task_id,
        attempt=0,
        attempt_key=attempt_key,
        amount=amount if amount is not None else Decimal("1.00"),
        execution_generation=generation,
    )
    assert hold is not None
    return hold


async def _holds(session: AsyncSession, task_id: str = "t_1") -> list[BudgetReservation]:
    await session.rollback()
    rows = (
        await session.execute(
            select(BudgetReservation)
            .where(
                BudgetReservation.business_id == BIZ,
                BudgetReservation.task_id == task_id,
            )
            .order_by(BudgetReservation.idempotency_key)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _fresh_task(session: AsyncSession, task_id: str = "t_1"):
    await session.rollback()
    task = await tasks_repo.get(session, BIZ, task_id)
    assert task is not None
    await session.refresh(task)
    return task


async def _usage(session: AsyncSession, task_id: str = "t_1") -> list[UsageRecord]:
    await session.rollback()
    rows = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.business_id == BIZ,
                UsageRecord.task_id == task_id,
            )
        )
    ).scalars()
    return list(rows)


async def _spend(session: AsyncSession, biz: str = BIZ, project_id: str = "proj_1") -> Decimal:
    await session.rollback()
    return (
        await session.execute(
            select(Project.ai_spent).where(Project.business_id == biz, Project.id == project_id)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Generation stamping
# ---------------------------------------------------------------------------


async def test_executor_stamps_claim_generation_on_reservation(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()

    result = await execute_task(session, MockProvider(texts=['{"summary": "done"}']), _req())

    assert result.outcome == "completed"
    task = await _fresh_task(session)
    assert task.execution_generation == 1
    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].execution_generation == 1
    assert holds[0].status == "consumed"
    assert len(await _usage(session)) == 1


async def test_generation_stamped_across_claim_recovery_and_next_claim(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    first = await _claim(session)
    attempt_key = f"{BIZ}:t_1:a0"
    await _reserve(session, attempt_key=attempt_key, generation=first.execution_generation)
    await session.commit()
    await _expire(session)

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)
    assert res.status_code == 200, res.text

    task = await _fresh_task(session)
    assert task.execution_generation == 2 and task.status == "not_started"
    hold1 = (await _holds(session))[0]
    assert hold1.execution_generation == 1 and hold1.status == "ambiguous"

    # The next normal claim starts generation 3 and stamps its own hold.
    await session.rollback()
    result = await execute_task(session, MockProvider(texts=['{"summary": "done"}']), _req())
    assert result.outcome == "completed"

    holds = await _holds(session)
    generations = sorted(hold.execution_generation for hold in holds)
    assert generations == [1, 3]
    by_generation = {hold.execution_generation: hold for hold in holds}
    assert by_generation[1].status == "ambiguous"  # reconciled, never reused
    assert by_generation[3].status == "consumed"
    assert len(await _usage(session)) == 1


# ---------------------------------------------------------------------------
# Generation-scoped CAS
# ---------------------------------------------------------------------------


async def test_set_status_generation_cas_requires_exact_scope(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")
    hold = await _reserve(session, attempt_key="cas:g2", generation=2)
    await session.commit()

    async def cas(*, task_id: str = "t_1", key: str = "cas:g2", generation: int = 2) -> bool:
        return await RESERVATIONS.set_status(
            session,
            BIZ,
            hold.id,
            from_statuses=("active",),
            to_status="consumed",
            task_id=task_id,
            attempt_key=key,
            execution_generation=generation,
        )

    assert await cas(generation=3) is False
    assert await cas(generation=1) is False
    assert await cas(task_id="t_other") is False
    assert await cas(key="cas:other") is False
    assert await cas() is True
    assert await cas() is False  # already consumed: no second transition

    with pytest.raises(ValueError, match="requires task_id and attempt_key"):
        await RESERVATIONS.set_status(
            session,
            BIZ,
            hold.id,
            from_statuses=("active",),
            to_status="consumed",
            execution_generation=2,
        )


async def test_null_generation_never_matches_zero(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")
    hold = await _reserve(session, attempt_key="legacy", generation=None)
    await session.commit()
    assert hold.execution_generation is None

    found = await RESERVATIONS.get_for_settlement(
        session,
        BIZ,
        hold.id,
        task_id="t_1",
        attempt_key="legacy",
        execution_generation=0,
    )
    assert found is None
    assert (
        await RESERVATIONS.set_status(
            session,
            BIZ,
            hold.id,
            from_statuses=("active",),
            to_status="consumed",
            task_id="t_1",
            attempt_key="legacy",
            execution_generation=0,
        )
        is False
    )

    # Legacy settlement (no generation) still works and matches the NULL row.
    assert (
        await RESERVATIONS.set_status(
            session,
            BIZ,
            hold.id,
            from_statuses=("active",),
            to_status="consumed",
            task_id="t_1",
            attempt_key="legacy",
        )
        is True
    )


# ---------------------------------------------------------------------------
# Recovery reconciliation by generation
# ---------------------------------------------------------------------------


async def test_recovery_marks_only_null_and_older_active_holds_ambiguous(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    assert claim.execution_generation == 1

    null_hold = await _reserve(session, attempt_key="k:null", generation=None)
    old_hold = await _reserve(session, attempt_key="k:g1", generation=1)
    current_hold = await _reserve(session, attempt_key="k:g2", generation=2)
    future_hold = await _reserve(session, attempt_key="k:g3", generation=3)
    consumed_hold = await _reserve(session, attempt_key="k:consumed", generation=1)
    released_hold = await _reserve(session, attempt_key="k:released", generation=1)
    await RESERVATIONS.set_status(
        session, BIZ, consumed_hold.id, from_statuses=("active",), to_status="consumed"
    )
    await RESERVATIONS.set_status(
        session, BIZ, released_hold.id, from_statuses=("active",), to_status="released"
    )
    await session.commit()
    await _expire(session)

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "recovered"

    task = await _fresh_task(session)
    assert task.execution_generation == 2

    statuses = {hold.id: (hold.status, hold.reason) for hold in await _holds(session)}
    assert statuses[null_hold.id] == ("ambiguous", "recovery_stale")
    assert statuses[old_hold.id] == ("ambiguous", "recovery_stale")
    assert statuses[current_hold.id] == ("active", "")
    assert statuses[future_hold.id] == ("active", "")
    assert statuses[consumed_hold.id] == ("consumed", "")
    assert statuses[released_hold.id] == ("released", "")

    # Ambiguity reconciliation neither charges nor releases anything.
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")
    pending = await RESERVATIONS.pending_total(session, BIZ, "proj_1")
    assert pending == Decimal("4.00")  # null + g1 ambiguous + g2 + g3 active


async def test_repeated_recovery_leaves_reconciled_holds_unchanged(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await _claim(session)
    await _reserve(session, attempt_key="k:g1", generation=1)
    await _reserve(session, attempt_key="k:g2", generation=2)
    await session.commit()
    await _expire(session)

    headers = await _authed(session, api_client)
    first = await api_client.post(_recover_url(), headers=headers)
    assert first.status_code == 200, first.text
    before = {hold.id: (hold.status, hold.reason) for hold in await _holds(session)}

    second = await api_client.post(_recover_url(), headers=headers)
    assert second.status_code == 409, second.text
    after = {hold.id: (hold.status, hold.reason) for hold in await _holds(session)}
    assert after == before

    # A second reconciliation pass is a no-op: the old generation is already
    # ambiguous and the new one is excluded by the filter.
    await session.rollback()
    held = await reconcile_task_reservations(session, BIZ, "t_1", before_generation=2)
    await session.commit()
    assert held == 0
    assert {hold.id: (hold.status, hold.reason) for hold in await _holds(session)} == before


async def test_legacy_recovery_without_generation_keeps_existing_scope(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Legacy (no-lease) recovery keeps marking every active hold ambiguous."""
    await make_world(session)
    await make_task(session, status="working")
    null_hold = await _reserve(session, attempt_key="k:null", generation=None)
    stamped_hold = await _reserve(session, attempt_key="k:g5", generation=5)
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '7200 seconds' WHERE id = 't_1'")
    )
    await session.commit()

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url(), headers=headers)
    assert res.status_code == 200, res.text

    statuses = {hold.id: (hold.status, hold.reason) for hold in await _holds(session)}
    assert statuses[null_hold.id] == ("ambiguous", "recovery_stale")
    assert statuses[stamped_hold.id] == ("ambiguous", "recovery_stale")
    assert await _usage(session) == []


# ---------------------------------------------------------------------------
# Settlement/recovery races across independent PostgreSQL sessions
# ---------------------------------------------------------------------------


async def test_settlement_wins_race_against_recovery(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    attempt_key = f"{BIZ}:t_1:a0"
    hold = await _reserve(session, attempt_key=attempt_key, generation=claim.execution_generation)
    hold_id, hold_key = hold.id, hold.idempotency_key
    await session.commit()

    factory = session_factory(test_engine)
    settled = asyncio.Event()
    allow_commit = asyncio.Event()

    async def settle() -> tuple[str | None, bool]:
        async with factory() as other:
            async with other.begin():
                record, won = await record_success(
                    other,
                    BIZ,
                    reservation_id=hold_id,
                    reservation_key=hold_key,
                    task_id="t_1",
                    project_id="proj_1",
                    call_kind="task_execution",
                    attempt=0,
                    attempt_key=attempt_key,
                    execution_generation=claim.execution_generation,
                    provider="mock",
                    model="mock",
                    response=_response(),
                )
                record_id = record.id if record is not None else None
                settled.set()
                await allow_commit.wait()
        return record_id, won

    async def reconcile() -> int:
        async with factory() as other:
            async with other.begin():
                return await reconcile_task_reservations(other, BIZ, "t_1", before_generation=2)

    settle_task = asyncio.ensure_future(settle())
    await asyncio.wait_for(settled.wait(), 10.0)
    reconcile_task = asyncio.ensure_future(reconcile())
    allow_commit.set()

    record_id, won = await asyncio.wait_for(settle_task, 10.0)
    held = await asyncio.wait_for(reconcile_task, 10.0)

    assert record_id is not None and won is True
    assert held == 0
    assert (await _holds(session))[0].status == "consumed"
    assert len(await _usage(session)) == 1


async def test_recovery_wins_race_blocks_stale_settlement_ledger(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    attempt_key = f"{BIZ}:t_1:a0"
    hold = await _reserve(session, attempt_key=attempt_key, generation=claim.execution_generation)
    hold_id, hold_key = hold.id, hold.idempotency_key
    await session.commit()

    factory = session_factory(test_engine)
    reconciled = asyncio.Event()
    allow_commit = asyncio.Event()

    async def reconcile() -> int:
        async with factory() as other:
            async with other.begin():
                counts = await reconcile_task_reservations(other, BIZ, "t_1", before_generation=2)
                reconciled.set()
                await allow_commit.wait()
        return counts

    async def stale_settlement() -> tuple[str | None, bool]:
        async with factory() as other:
            async with other.begin():
                record, won = await record_success(
                    other,
                    BIZ,
                    reservation_id=hold_id,
                    reservation_key=hold_key,
                    task_id="t_1",
                    project_id="proj_1",
                    call_kind="task_execution",
                    attempt=0,
                    attempt_key=attempt_key,
                    execution_generation=claim.execution_generation,
                    provider="mock",
                    model="mock",
                    response=_response(),
                )
                return (record.id if record is not None else None), won

    reconcile_task = asyncio.ensure_future(reconcile())
    await asyncio.wait_for(reconciled.wait(), 10.0)
    settle_task = asyncio.ensure_future(stale_settlement())
    allow_commit.set()

    held = await asyncio.wait_for(reconcile_task, 10.0)
    record_id, won = await asyncio.wait_for(settle_task, 10.0)

    assert held == 1
    assert record_id is None and won is False
    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")


# ---------------------------------------------------------------------------
# Old worker / old generation
# ---------------------------------------------------------------------------


async def test_old_generation_cannot_mutate_new_reservation(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    old = await _claim(session, owner="worker-a")
    attempt_key = f"{BIZ}:t_1:a0"
    old_hold = await _reserve(session, attempt_key=attempt_key, generation=old.execution_generation)
    await session.commit()
    await _expire(session)

    takeover = await REPO.takeover_expired(
        session,
        BIZ,
        "t_1",
        expected_execution_generation=old.execution_generation,
        expected_lease_owner=old.lease_owner,
        lease_owner="recovery:b",
        lease_ttl_seconds=300.0,
        lease_wall_seconds=2400.0,
    )
    assert takeover is not None
    assert (
        await REPO.fence_owned(
            session,
            BIZ,
            "t_1",
            execution_generation=takeover.execution_generation,
            lease_owner=takeover.lease_owner,
            values={"status": "not_started"},
        )
        is True
    )
    await session.commit()

    fresh = await _claim(session, owner="worker-c")
    assert fresh.execution_generation == 3
    new_hold = await _reserve(
        session, attempt_key=f"{BIZ}:t_1:a1", generation=fresh.execution_generation
    )
    new_hold_id = new_hold.id
    new_key = new_hold.attempt_key
    new_hold_key = new_hold.idempotency_key
    await session.commit()

    # The old token is fenced out of task persistence.
    assert (
        await REPO.fence_owned(
            session,
            BIZ,
            "t_1",
            execution_generation=old.execution_generation,
            lease_owner=old.lease_owner,
            values={"status": "completed"},
        )
        is False
    )
    # Tail of the old attempt: only its own hold matches the old generation.
    assert (
        await mark_ambiguous(
            session,
            BIZ,
            new_hold_id,
            "stale",
            task_id="t_1",
            attempt_key=new_key,
            execution_generation=old.execution_generation,
        )
        is False
    )
    assert (
        await release_reservation(
            session,
            BIZ,
            new_hold_id,
            "stale",
            task_id="t_1",
            attempt_key=new_key,
            execution_generation=old.execution_generation,
        )
        is False
    )
    record, won = await record_success(
        session,
        BIZ,
        reservation_id=new_hold_id,
        reservation_key=new_hold_key,
        task_id="t_1",
        project_id="proj_1",
        call_kind="task_execution",
        attempt=0,
        attempt_key=new_key,
        execution_generation=old.execution_generation,
        provider="mock",
        model="mock",
        response=_response(),
    )
    assert record is None and won is False
    await session.rollback()

    holds = {hold.id: hold.status for hold in await _holds(session)}
    assert holds[new_hold_id] == "active"
    assert holds[old_hold.id] == "active"
    assert await _usage(session) == []


async def test_business_isolation_blocks_foreign_reservation_mutation(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    hold = await _reserve(session, attempt_key="isolation", generation=1)
    await session.commit()

    assert (
        await RESERVATIONS.set_status(
            session,
            BIZ_OTHER,
            hold.id,
            from_statuses=("active",),
            to_status="consumed",
            task_id="t_1",
            attempt_key="isolation",
            execution_generation=1,
        )
        is False
    )
    assert (
        await RESERVATIONS.get_for_settlement(
            session,
            BIZ_OTHER,
            hold.id,
            task_id="t_1",
            attempt_key="isolation",
            execution_generation=1,
        )
        is None
    )
    held = await RESERVATIONS.mark_active_ambiguous(
        session,
        BIZ_OTHER,
        task_id="t_1",
        before_execution_generation=2,
    )
    assert held == 0
    await session.rollback()

    assert (await _holds(session))[0].status == "active"


# ---------------------------------------------------------------------------
# Rollback / ambiguity accounting / Team Lead legacy behavior
# ---------------------------------------------------------------------------


async def test_failed_settlement_transaction_leaves_no_partial_state(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    attempt_key = f"{BIZ}:t_1:a0"
    hold = await _reserve(session, attempt_key=attempt_key, generation=claim.execution_generation)
    hold_id, hold_key = hold.id, hold.idempotency_key
    await session.commit()

    async def boom() -> None:
        with pytest.raises(RuntimeError, match="injected"):
            async with session.begin():
                await REPO.fence_owned(
                    session,
                    BIZ,
                    "t_1",
                    execution_generation=claim.execution_generation,
                    lease_owner=claim.lease_owner,
                    values={"status": "completed", "progress": 100},
                )
                await record_success(
                    session,
                    BIZ,
                    reservation_id=hold_id,
                    reservation_key=hold_key,
                    task_id="t_1",
                    project_id="proj_1",
                    call_kind="task_execution",
                    attempt=0,
                    attempt_key=attempt_key,
                    execution_generation=claim.execution_generation,
                    provider="mock",
                    model="mock",
                    response=_response(),
                )
                raise RuntimeError("injected")

    await boom()

    task = await _fresh_task(session)
    assert task.status == "working" and task.execution_generation == 1
    assert task.cost_accrued == Decimal("0")
    assert (await _holds(session))[0].status == "active"
    assert await _usage(session) == []
    assert await _spend(session) == Decimal("0")


async def test_ambiguous_hold_stays_pending_and_is_not_auto_released(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    hold = await _reserve(session, attempt_key="pending", generation=1, amount=Decimal("2.00"))
    await RESERVATIONS.set_status(
        session, BIZ, hold.id, from_statuses=("active",), to_status="ambiguous", reason="timeout"
    )
    await session.commit()

    held = await reconcile_task_reservations(session, BIZ, "t_1", before_generation=2)
    await session.commit()

    assert held == 0
    row = (await _holds(session))[0]
    assert row.status == "ambiguous"
    assert await RESERVATIONS.pending_total(session, BIZ, "proj_1") == Decimal("2.00")


async def test_teamlead_legacy_settlement_keeps_null_generation(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id=None,
        attempt_key="tl:b1:brief_finalize:g0",
        amount=Decimal("0.50"),
        mode="best_effort",
    )
    assert hold is not None
    await session.commit()

    record, won = await record_success(
        session,
        BIZ,
        reservation_id=hold.id,
        reservation_key=hold.idempotency_key,
        task_id=None,
        project_id="proj_1",
        call_kind="brief_finalize",
        attempt=0,
        attempt_key="tl:b1:brief_finalize:g0",
        provider="mock",
        model="mock",
        response=_response(),
    )
    await session.commit()

    assert won is True
    await session.rollback()
    hold_row = (
        await session.execute(select(BudgetReservation).where(BudgetReservation.id == hold.id))
    ).scalar_one()
    assert hold_row.execution_generation is None
    assert hold_row.status == "consumed"
    usage = (
        (await session.execute(select(UsageRecord).where(UsageRecord.business_id == BIZ)))
        .scalars()
        .all()
    )
    assert len(usage) == 1 and usage[0].task_id is None


# ---------------------------------------------------------------------------
# Authorization regression
# ---------------------------------------------------------------------------


async def test_recovery_cross_project_task_returns_404_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_project(session, project_id="proj_2")
    await make_task(session, task_id="t_other", project_id="proj_2", status="not_started")
    other_claim = await _claim(session, task_id="t_other", owner="worker-other")
    outside = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_2",
        task_id="t_other",
        attempt=0,
        attempt_key=f"{BIZ}:t_other:a0",
        amount=Decimal("1.00"),
        execution_generation=other_claim.execution_generation,
    )
    assert outside is not None
    outside_id = outside.id
    await session.commit()
    await _expire(session, task_id="t_other")

    headers = await _authed(session, api_client)
    res = await api_client.post(_recover_url("t_other", project_id="proj_1"), headers=headers)

    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "not_found"

    task = await _fresh_task(session, task_id="t_other")
    assert task.status == "working"
    assert task.execution_generation == other_claim.execution_generation
    hold = (
        await session.execute(select(BudgetReservation).where(BudgetReservation.id == outside_id))
    ).scalar_one()
    assert hold.status == "active"
    assert hold.execution_generation == other_claim.execution_generation
    events = (
        (await session.execute(select(ActivityEvent).where(ActivityEvent.business_id == BIZ)))
        .scalars()
        .all()
    )
    assert events == []
    usage = (
        (await session.execute(select(UsageRecord).where(UsageRecord.business_id == BIZ)))
        .scalars()
        .all()
    )
    assert usage == []
