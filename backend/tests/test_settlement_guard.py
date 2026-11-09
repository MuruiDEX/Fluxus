"""S2.2.4.6 Slice 5: strict settlement-result guard.

Real PostgreSQL throughout. These tests exercise the strict
`settle_success`/`settle_estimated_failure` APIs, the legacy tuple wrappers and
the executor's fail-closed integration:

- pre-write rejections perform no write; post-append CAS losses raise with
  `wrote=True` so the caller rolls back;
- exact replays validate the persisted ledger linkage without re-pricing and
  without appending or CAS-ing again;
- the executor never completes a task whose settlement was skipped or rejected.
"""

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.execution import executor as executor_module
from app.execution.executor import ExecutionRequest, ExecutionResult, execute_task
from app.llm.mock import MockProvider
from app.llm.types import BadResponseError, LlmResponse, LlmUsage
from app.persistence.models.activity_event import ActivityEvent
from app.persistence.models.project import Project
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import TaskRepository
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from app.usage import pricing
from app.usage import settlement as settlement_module
from app.usage.settlement import (
    SettlementRejected,
    SettlementResult,
    record_estimated_failure,
    record_success,
    settle_estimated_failure,
    settle_success,
)
from app.usage.tariffs import Tariff
from tests.factories import BIZ, make_task, make_world

TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)
ZERO_TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("0"),
    output_per_1k=Decimal("0"),
)
MAX_OUT = 16

TASKS = TaskRepository()
RESERVATIONS = BudgetReservationRepository()
USAGE = UsageRepository()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {"llm_max_tokens": MAX_OUT, "usage_unpriced_reserve_amount": "0.25"}
    data.update(over)
    return Settings(**data)


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        timeout_s=10.0,
        settings=_settings(),
    )


def _response(
    *,
    reported: bool = True,
    prompt: int = 10,
    completion: int = 5,
    cost: Decimal | None = None,
) -> LlmResponse:
    return LlmResponse(
        text="ok",
        usage=LlmUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            reported=reported,
            cost_estimate=cost,
        ),
        model="mock",
        attempts=1,
    )


async def _claim(session: AsyncSession, *, owner: str = "worker-a"):
    await session.commit()
    ownership = await TASKS.claim_owned(
        session,
        BIZ,
        "t_1",
        from_statuses=("not_started", "failed"),
        to_status="working",
        lease_owner=owner,
        lease_ttl_seconds=300.0,
        lease_wall_seconds=2400.0,
    )
    assert ownership is not None
    await session.commit()
    return ownership


@dataclass(frozen=True)
class _HoldSnapshot:
    """Plain reservation identity, immune to test-helper rollbacks.

    Helpers call `session.rollback()` to observe committed state; with
    `expire_on_commit=False` an ORM instance survives commits but not a
    rollback, and every later attribute read would attempt lazy IO outside the
    greenlet. Capture the identity once, right after the reserve commit.
    """

    id: str
    idempotency_key: str
    attempt: int
    task_id: str | None
    project_id: str | None
    attempt_key: str
    currency: str


async def _reserve(
    session: AsyncSession,
    *,
    generation: int | None,
    attempt_key: str | None = None,
    task_id: str | None = "t_1",
    project_id: str = "proj_1",
    amount: Decimal = Decimal("1.00"),
    currency: str = "USD",
) -> _HoldSnapshot:
    key = attempt_key or f"{BIZ}:t_1:a0"
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id=project_id,
        task_id=task_id,
        attempt=0,
        attempt_key=key,
        amount=amount,
        currency=currency,
        execution_generation=generation,
    )
    assert hold is not None
    snapshot = _HoldSnapshot(
        id=hold.id,
        idempotency_key=hold.idempotency_key,
        attempt=hold.attempt,
        task_id=hold.task_id,
        project_id=hold.project_id,
        attempt_key=hold.attempt_key,
        currency=hold.currency,
    )
    await session.commit()
    return snapshot


def _success_call(
    *,
    reservation_id: str | None,
    reservation_key: str | None,
    generation: int | None,
    attempt: int = 0,
    task_id: str | None = "t_1",
    project_id: str = "proj_1",
    attempt_key: str | None = None,
    provider: str = "mock",
    model: str = "mock",
    response: LlmResponse | None = None,
    input_bound_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, object]:
    return {
        "reservation_id": reservation_id,
        "reservation_key": reservation_key,
        "task_id": task_id,
        "project_id": project_id,
        "agent_id": "ag_1",
        "call_kind": "task_execution",
        "attempt": attempt,
        "attempt_key": attempt_key or f"{BIZ}:t_1:a{attempt}",
        "execution_generation": generation,
        "provider": provider,
        "model": model,
        "response": response or _response(),
        "input_bound_tokens": input_bound_tokens,
        "max_output_tokens": max_output_tokens,
    }


def _hold_call(hold: _HoldSnapshot, *, generation: int | None, **over: object) -> dict:
    kwargs = _success_call(
        reservation_id=hold.id,
        reservation_key=hold.idempotency_key,
        generation=generation,
        attempt=hold.attempt,
        task_id=hold.task_id,
        project_id=hold.project_id or "proj_1",
        attempt_key=hold.attempt_key,
    )
    kwargs.update(over)
    return kwargs


def _failure_call(
    hold: _HoldSnapshot,
    *,
    generation: int | None,
    input_bound_tokens: int = 1000,
    max_output_tokens: int = 100,
    **over: object,
) -> dict:
    kwargs = _hold_call(hold, generation=generation)
    kwargs.update(
        {
            "input_bound_tokens": input_bound_tokens,
            "max_output_tokens": max_output_tokens,
        }
    )
    kwargs.update(over)
    kwargs.pop("response", None)
    return kwargs


async def _hold_row(session: AsyncSession, hold_id: str) -> BudgetReservation:
    await session.rollback()
    res = await session.execute(
        select(BudgetReservation)
        .where(BudgetReservation.id == hold_id)
        .execution_options(populate_existing=True)
    )
    return res.scalar_one()


async def _usage_rows(session: AsyncSession, *, task_id: str | None = "t_1") -> list[UsageRecord]:
    await session.rollback()
    session.expire_all()
    stmt = select(UsageRecord).where(UsageRecord.business_id == BIZ)
    if task_id is not None:
        stmt = stmt.where(UsageRecord.task_id == task_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def _holds(session: AsyncSession) -> list[BudgetReservation]:
    await session.rollback()
    res = await session.execute(
        select(BudgetReservation)
        .where(BudgetReservation.business_id == BIZ)
        .execution_options(populate_existing=True)
    )
    return list(res.scalars().all())


async def _result_events(session: AsyncSession) -> list[ActivityEvent]:
    await session.rollback()
    res = await session.execute(
        select(ActivityEvent).where(
            ActivityEvent.business_id == BIZ,
            ActivityEvent.kind == "result",
        )
    )
    return list(res.scalars().all())


async def _task_status(session: AsyncSession) -> str:
    await session.rollback()
    session.expire_all()
    task = await TASKS.get(session, BIZ, "t_1")
    assert task is not None
    return task.status


async def _project_spend(session: AsyncSession) -> Decimal:
    await session.rollback()
    return (
        await session.execute(
            select(Project.ai_spent).where(Project.business_id == BIZ, Project.id == "proj_1")
        )
    ).scalar_one()


async def _run(
    session: AsyncSession, provider: MockProvider, request: ExecutionRequest
) -> ExecutionResult:
    await session.commit()
    return await execute_task(session, provider, request)


async def _settled_confirmed(
    session: AsyncSession,
) -> tuple[_HoldSnapshot, int, str]:
    """Create a confirmed, consumed settlement and return (hold, gen, record_id)."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    async with session.begin():
        result = await settle_success(
            session, BIZ, **_hold_call(hold, generation=ownership.execution_generation)
        )
        assert result.outcome == "applied_confirmed"
        assert result.record is not None
        record_id = result.record.id
    return hold, ownership.execution_generation, record_id


# ---------------------------------------------------------------------------
# Strict success outcomes
# ---------------------------------------------------------------------------


async def test_success_confirmed_applies_consumed(session: AsyncSession) -> None:
    hold, generation, record_id = await _settled_confirmed(session)

    row = await _hold_row(session, hold.id)
    assert row.status == "consumed"
    assert row.usage_record_id == record_id
    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].id == record_id
    assert usage[0].usage_status == "confirmed"
    assert usage[0].cost_source == "tariff"
    assert usage[0].idempotency_key == f"usage:{hold.idempotency_key}"
    assert generation == 1


async def test_success_unpriced_keeps_hold_ambiguous(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_success(
            session,
            BIZ,
            **_hold_call(
                hold,
                generation=ownership.execution_generation,
                response=_response(reported=False, prompt=0, completion=0),
            ),
        )

    assert result.outcome == "applied_ambiguous"
    assert result.reservation_settled is True
    assert result.reservation_status == "ambiguous"
    assert result.usage_status == "unknown"
    assert result.reason == "post_send_unpriced"

    row = await _hold_row(session, hold.id)
    assert row.status == "ambiguous"
    assert row.reason == "post_send_unpriced"
    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].usage_status == "unknown"
    assert usage[0].cost_amount is None
    assert usage[0].cost_source == "unknown"


async def test_success_reported_but_unpriceable_consumes_unknown(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_success(
            session,
            BIZ,
            **_hold_call(hold, generation=ownership.execution_generation),
        )

    assert result.outcome == "applied_unknown"
    assert result.reservation_settled is True
    assert result.reservation_status == "consumed"
    assert result.usage_status == "unknown"

    row = await _hold_row(session, hold.id)
    assert row.status == "consumed"
    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].usage_status == "unknown"
    assert usage[0].cost_amount is None


async def test_success_conservative_positive_estimate_consumes(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_success(
            session,
            BIZ,
            **_hold_call(
                hold,
                generation=ownership.execution_generation,
                response=_response(reported=False, prompt=0, completion=0),
                input_bound_tokens=1000,
                max_output_tokens=100,
            ),
        )

    assert result.outcome == "applied_estimated"
    assert result.reservation_settled is True
    assert result.reservation_status == "consumed"
    assert result.usage_status == "estimated"
    assert result.reason == "conservative_estimate"

    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].usage_status == "estimated"
    assert usage[0].cost_amount is not None and usage[0].cost_amount > 0


async def test_zero_cost_conservative_estimate_keeps_hold(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(ZERO_TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_success(
            session,
            BIZ,
            **_hold_call(
                hold,
                generation=ownership.execution_generation,
                response=_response(reported=False, prompt=0, completion=0),
                input_bound_tokens=100,
                max_output_tokens=10,
            ),
        )

    assert result.outcome == "applied_ambiguous"
    assert result.reservation_settled is True
    assert result.reservation_status == "ambiguous"
    assert result.usage_status == "estimated"
    assert result.reason == "zero_cost_bound"

    row = await _hold_row(session, hold.id)
    assert row.status == "ambiguous"
    # Stored reason stays the historical post-send literal.
    assert row.reason == "post_send_unpriced"
    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].usage_status == "estimated"
    assert usage[0].cost_amount == Decimal("0.00")


# ---------------------------------------------------------------------------
# Strict estimated-failure outcomes
# ---------------------------------------------------------------------------


async def test_estimated_failure_priceable_consumes(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_estimated_failure(
            session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
        )

    assert result.outcome == "applied_estimated"
    assert result.reservation_settled is True
    assert result.reservation_status == "consumed"
    assert result.usage_status == "estimated"
    row = await _hold_row(session, hold.id)
    assert row.status == "consumed"
    usage = await _usage_rows(session)
    assert len(usage) == 1 and usage[0].usage_status == "estimated"


async def test_estimated_failure_unpriceable_keeps_hold(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_estimated_failure(
            session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
        )

    assert result.outcome == "applied_ambiguous"
    assert result.record is None
    assert result.reservation_settled is True
    assert result.reservation_status == "ambiguous"
    row = await _hold_row(session, hold.id)
    assert row.status == "ambiguous"
    assert row.reason == "post_send_unpriced"
    assert await _usage_rows(session) == []


# ---------------------------------------------------------------------------
# Missing / terminal reservations
# ---------------------------------------------------------------------------


async def test_missing_reservation_is_skipped(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)

    async with session.begin():
        result = await settle_success(
            session,
            BIZ,
            **_success_call(
                reservation_id="res_missing",
                reservation_key="reserve:missing:1",
                generation=ownership.execution_generation,
            ),
        )

    assert result.outcome == "skipped"
    assert result.reason == "reservation_missing"
    assert result.reservation_status == "missing"
    assert result.reservation_settled is False
    assert await _usage_rows(session) == []


@pytest.mark.parametrize("terminal", ["ambiguous", "released", "written_off", "reconciled"])
async def test_terminal_reservation_is_skipped(session: AsyncSession, terminal: str) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    async with session.begin():
        assert await RESERVATIONS.set_status(
            session,
            BIZ,
            hold.id,
            from_statuses=("active",),
            to_status=terminal,
        )

    async with session.begin():
        result = await settle_success(
            session, BIZ, **_hold_call(hold, generation=ownership.execution_generation)
        )

    assert result.outcome == "skipped"
    assert result.reason == "reservation_terminal"
    assert result.reservation_status == terminal
    assert result.reservation_settled is False
    assert await _usage_rows(session) == []


# ---------------------------------------------------------------------------
# Pre-write rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "proj_other"),
        ("task_id", "t_other"),
        ("attempt_key", "biz_test:t_1:a9"),
        ("attempt", 9),
        ("execution_generation", 999),
        ("reservation_key", "reserve:other:1"),
    ],
)
async def test_scope_mismatch_rejected_before_write(
    session: AsyncSession, field: str, value: object
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    kwargs = _hold_call(hold, generation=ownership.execution_generation)
    kwargs[field] = value

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **kwargs)

    assert exc.value.wrote is False
    assert exc.value.code == "scope_mismatch"
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


async def test_currency_mismatch_rejected_before_write(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation, currency="EUR")

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(
                session, BIZ, **_hold_call(hold, generation=ownership.execution_generation)
            )

    assert exc.value.wrote is False
    assert exc.value.code == "currency_mismatch"
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


# ---------------------------------------------------------------------------
# Replay validation (no re-pricing, no append, no CAS)
# ---------------------------------------------------------------------------


async def test_replay_after_tariff_change_does_not_reprice(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold, generation, record_id = await _settled_confirmed(session)

    # A historical replay must not depend on the current catalog.
    pricing.registry.clear()
    calls: list[str] = []

    async def forbidden_async(*args: object, **kwargs: object) -> object:
        calls.append("write")
        raise AssertionError("replay performed a write")

    def forbidden_price(*args: object, **kwargs: object) -> object:
        calls.append("price")
        raise AssertionError("replay consulted pricing")

    monkeypatch.setattr(settlement_module._usage, "append", forbidden_async)
    monkeypatch.setattr(settlement_module._reservations, "set_status", forbidden_async)
    monkeypatch.setattr(settlement_module, "price_response", forbidden_price)
    monkeypatch.setattr(settlement_module.pricing, "upper_bound_cost", forbidden_price)

    async with session.begin():
        replay = await settle_success(
            session,
            BIZ,
            **_hold_call(
                hold,
                generation=generation,
                response=_response(prompt=999, completion=999),
            ),
        )
        replay_record_id = replay.record.id if replay.record is not None else None

    assert calls == []
    assert replay.outcome == "replayed"
    assert replay.reason == "exact_replay"
    assert replay.reservation_settled is False
    assert replay.usage_status == "confirmed"
    assert replay_record_id == record_id
    assert len(await _usage_rows(session)) == 1
    assert (await _hold_row(session, hold.id)).status == "consumed"


async def test_replay_linkage_missing_rejected(session: AsyncSession) -> None:
    hold, generation, _record_id = await _settled_confirmed(session)
    async with session.begin():
        await session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == hold.id)
            .values(usage_record_id=None)
        )
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **_hold_call(hold, generation=generation))

    assert exc.value.code == "replay_linkage_missing"
    assert exc.value.wrote is False
    assert len(await _usage_rows(session)) == 1


async def test_replay_linkage_mismatch_rejected(session: AsyncSession) -> None:
    hold, generation, _record_id = await _settled_confirmed(session)
    async with session.begin():
        await session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == hold.id)
            .values(usage_record_id="ur_corrupt")
        )
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **_hold_call(hold, generation=generation))

    assert exc.value.code == "replay_linkage_mismatch"
    assert exc.value.wrote is False
    assert len(await _usage_rows(session)) == 1


async def test_replay_missing_ledger_rejected(session: AsyncSession) -> None:
    hold, generation, record_id = await _settled_confirmed(session)
    async with session.begin():
        await session.execute(delete(UsageRecord).where(UsageRecord.id == record_id))
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **_hold_call(hold, generation=generation))

    assert exc.value.code == "replay_ledger_missing"
    assert exc.value.wrote is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("model", "other"),
    ],
)
async def test_replay_identity_conflict_rejected(
    session: AsyncSession, field: str, value: str
) -> None:
    hold, generation, record_id = await _settled_confirmed(session)
    async with session.begin():
        await session.execute(
            update(UsageRecord).where(UsageRecord.id == record_id).values({field: value})
        )
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **_hold_call(hold, generation=generation))

    assert exc.value.code == "replay_identity_conflict"
    assert exc.value.wrote is False


@pytest.mark.parametrize(
    "values",
    [
        {"currency": "EUR"},
        {"cost_source": "unknown"},
        {"usage_status": "unknown"},
        {"usage_status": "estimated", "cost_source": "provider"},
    ],
)
async def test_replay_financial_conflict_rejected(session: AsyncSession, values: dict) -> None:
    hold, generation, record_id = await _settled_confirmed(session)
    async with session.begin():
        await session.execute(
            update(UsageRecord).where(UsageRecord.id == record_id).values(**values)
        )
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(session, BIZ, **_hold_call(hold, generation=generation))

    assert exc.value.code == "replay_identity_conflict"
    assert exc.value.wrote is False


async def test_estimated_failure_replay_validates_without_reprice(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async with session.begin():
        first = await settle_estimated_failure(
            session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
        )
        first_id = first.record.id if first.record is not None else None
    assert first.outcome == "applied_estimated"
    assert first_id is not None

    pricing.registry.clear()
    calls: list[str] = []

    async def forbidden_async(*args: object, **kwargs: object) -> object:
        calls.append("write")
        raise AssertionError("failure replay performed a write")

    def forbidden_price(*args: object, **kwargs: object) -> object:
        calls.append("price")
        raise AssertionError("failure replay consulted pricing")

    monkeypatch.setattr(settlement_module._usage, "append", forbidden_async)
    monkeypatch.setattr(settlement_module._reservations, "set_status", forbidden_async)
    monkeypatch.setattr(settlement_module.pricing, "upper_bound_cost", forbidden_price)

    async with session.begin():
        replay = await settle_estimated_failure(
            session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
        )
        replay_id = replay.record.id if replay.record is not None else None

    assert calls == []
    assert replay.outcome == "replayed"
    assert replay.reason == "exact_replay"
    assert replay.reservation_settled is False
    assert replay.usage_status == "estimated"
    assert replay_id == first_id
    assert len(await _usage_rows(session)) == 1
    assert (await _hold_row(session, hold.id)).status == "consumed"


async def test_failure_replay_rejects_non_estimated_status(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    async with session.begin():
        result = await settle_estimated_failure(
            session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
        )
        assert result.outcome == "applied_estimated"
        record_id = result.record.id

    # Turn the persisted estimated row into a structurally valid confirmed row:
    # a failure replay must still reject it by allowed status.
    async with session.begin():
        await session.execute(
            update(UsageRecord)
            .where(UsageRecord.id == record_id)
            .values(usage_status="confirmed", cost_source="provider")
        )
    session.expire_all()

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_estimated_failure(
                session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
            )

    assert exc.value.code == "replay_identity_conflict"
    assert exc.value.wrote is False


# ---------------------------------------------------------------------------
# CAS loss after append
# ---------------------------------------------------------------------------


async def test_cas_loss_after_append_raises_and_rolls_back(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async def lose_cas(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(settlement_module._reservations, "set_status", lose_cas)

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_success(
                session, BIZ, **_hold_call(hold, generation=ownership.execution_generation)
            )

    assert exc.value.code == "cas_lost"
    assert exc.value.wrote is True
    # The caller's transaction rolled back the ledger append.
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


async def test_estimated_failure_cas_loss_after_append_raises_and_rolls_back(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async def lose_cas(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(settlement_module._reservations, "set_status", lose_cas)

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await settle_estimated_failure(
                session, BIZ, **_failure_call(hold, generation=ownership.execution_generation)
            )

    assert exc.value.code == "cas_lost"
    assert exc.value.wrote is True
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


async def test_wrapper_propagates_post_append_rejection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)

    async def lose_cas(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(settlement_module._reservations, "set_status", lose_cas)

    with pytest.raises(SettlementRejected) as exc:
        async with session.begin():
            await record_success(
                session, BIZ, **_hold_call(hold, generation=ownership.execution_generation)
            )

    assert exc.value.wrote is True
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


async def test_wrapper_pre_write_rejection_keeps_legacy_tuple(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    kwargs = _hold_call(hold, generation=ownership.execution_generation, project_id="proj_other")

    record, won = await record_success(session, BIZ, **kwargs)
    await session.rollback()

    assert record is None and won is False
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"

    failure_kwargs = _failure_call(hold, generation=ownership.execution_generation)
    failure_kwargs["task_id"] = "t_other"
    est_record, est_won, status = await record_estimated_failure(session, BIZ, **failure_kwargs)
    await session.rollback()

    assert est_record is None and est_won is False and status == "skipped"
    assert await _usage_rows(session) == []
    assert (await _hold_row(session, hold.id)).status == "active"


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


async def test_executor_skipped_success_settlement_rolls_back(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])

    async def skipped(*args: object, **kwargs: object) -> SettlementResult:
        return SettlementResult(
            outcome="skipped",
            record=None,
            reservation_id=None,
            reservation_status="missing",
            reservation_settled=False,
            usage_status=None,
            reason="reservation_missing",
        )

    monkeypatch.setattr(executor_module, "settle_success", skipped)

    with pytest.raises(SettlementRejected) as exc:
        await _run(session, provider, _request())

    assert exc.value.code == "settlement_skipped"
    assert exc.value.wrote is True
    assert await _task_status(session) == "not_started"
    assert await _result_events(session) == []
    assert await _usage_rows(session) == []
    assert await _project_spend(session) == Decimal("0")
    # Compensation settles the provider-answered hold as ambiguous.
    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "ambiguous"


async def test_executor_rejected_success_settlement_rolls_back(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])

    async def rejected(*args: object, **kwargs: object) -> SettlementResult:
        raise SettlementRejected("scope_mismatch", "injected rejection", wrote=True)

    monkeypatch.setattr(executor_module, "settle_success", rejected)

    with pytest.raises(SettlementRejected) as exc:
        await _run(session, provider, _request())

    assert exc.value.code == "scope_mismatch"
    assert await _task_status(session) == "not_started"
    assert await _result_events(session) == []
    assert await _usage_rows(session) == []
    assert await _project_spend(session) == Decimal("0")


async def test_executor_estimated_failure_skipped_keeps_task_failed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(errors=[BadResponseError("malformed body")])

    async def skipped(*args: object, **kwargs: object) -> SettlementResult:
        return SettlementResult(
            outcome="skipped",
            record=None,
            reservation_id=None,
            reservation_status="missing",
            reservation_settled=False,
            usage_status=None,
            reason="reservation_missing",
        )

    monkeypatch.setattr(executor_module, "settle_estimated_failure", skipped)

    result = await _run(session, provider, _request())

    assert result.outcome == "failed"
    assert result.error_code == "bad_response"
    task = await TASKS.get(session, BIZ, "t_1")
    assert task is not None and task.status == "failed" and task.attempts == 1
    assert await _usage_rows(session) == []


async def test_executor_estimated_failure_rejected_rolls_back_and_compensates(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(errors=[BadResponseError("malformed body")])

    async def rejected(*args: object, **kwargs: object) -> SettlementResult:
        raise SettlementRejected("scope_mismatch", "injected rejection", wrote=True)

    monkeypatch.setattr(executor_module, "settle_estimated_failure", rejected)

    with pytest.raises(SettlementRejected) as exc:
        await _run(session, provider, _request())

    assert exc.value.code == "scope_mismatch"
    # The failure transaction rolled back, then compensation reverted the claim.
    assert await _task_status(session) == "not_started"
    assert await _usage_rows(session) == []
    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "ambiguous"


# ---------------------------------------------------------------------------
# Team Lead legacy replay
# ---------------------------------------------------------------------------


async def test_teamlead_replay_adds_no_duplicate_row(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    hold = await _reserve(
        session,
        generation=None,
        attempt_key="tl:b1:brief_questions:g0",
        task_id=None,
        amount=Decimal("0.50"),
    )
    kwargs = {
        "reservation_id": hold.id,
        "reservation_key": hold.idempotency_key,
        "task_id": None,
        "project_id": "proj_1",
        "call_kind": "brief_questions",
        "attempt": 0,
        "attempt_key": hold.attempt_key,
        "provider": "mock",
        "model": "mock",
    }

    first, first_won = await record_success(session, BIZ, response=_response(), **kwargs)
    assert first is not None and first_won is True
    first_id = first.id
    await session.commit()

    # A replay must not need the historical tariff.
    pricing.registry.clear()
    second, second_won = await record_success(
        session, BIZ, response=_response(prompt=999, completion=999), **kwargs
    )
    assert second is not None and second.id == first_id
    assert second_won is False
    await session.commit()

    async with session.begin():
        replay = await settle_success(
            session, BIZ, response=_response(prompt=999, completion=999), **kwargs
        )
    assert replay.outcome == "replayed"
    assert replay.reservation_settled is False

    usage = await _usage_rows(session, task_id=None)
    assert len(usage) == 1 and usage[0].id == first_id
    assert (await _hold_row(session, hold.id)).status == "consumed"


# ---------------------------------------------------------------------------
# Legacy adapter sanity (no behavior drift for common callers)
# ---------------------------------------------------------------------------


async def test_legacy_tuple_matches_strict_result(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    ownership = await _claim(session)
    hold = await _reserve(session, generation=ownership.execution_generation)
    kwargs = _hold_call(hold, generation=ownership.execution_generation)

    async with session.begin():
        result = await settle_success(session, BIZ, **kwargs)
        result_id = result.record.id
    assert result.outcome == "applied_confirmed"

    async with session.begin():
        record, won = await record_success(session, BIZ, **kwargs)
        record_id = record.id if record is not None else None
    assert won is False  # replay, not a second settlement
    assert record_id == result_id


async def test_async_cas_helpers_still_exported() -> None:
    """Guard against accidental removal of the non-result settlement helpers."""
    from app.usage.settlement import mark_ambiguous, release_reservation

    assert asyncio.iscoroutinefunction(mark_ambiguous)
    assert asyncio.iscoroutinefunction(release_reservation)
