"""Stage 18 S2.2.4.6 slice 4: manual ambiguous-reservation reconciliation.

Real PostgreSQL throughout. Every transactional/concurrency test uses
independent sessions from the test engine; SQLite is never substituted for the
locking guarantees under test.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.passwords import hash_password
from app.execution.recovery import recover_task
from app.llm.types import LlmResponse, LlmUsage
from app.persistence.engine import session_factory
from app.persistence.models.activity_event import ActivityEvent
from app.persistence.models.project import Project
from app.persistence.models.task import Task
from app.persistence.models.usage import (
    BudgetReservation,
    UsageReconciliation,
    UsageRecord,
)
from app.persistence.models.user import User
from app.persistence.repositories.concrete import (
    MembershipRepository,
    TaskRepository,
    UserRepository,
)
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from app.usage.manual_reconciliation import (
    ChargeCommand,
    ConfirmedCharge,
    ReconciliationConflict,
    confirm_provider_charge,
)
from app.usage.reconciliation import reconcile_task_reservations
from app.usage.settlement import (
    mark_ambiguous,
    record_estimated_failure,
    record_success,
    release_reservation,
)
from tests.conftest import OPERATOR_EMAIL, login_as, seed_operator
from tests.factories import (
    BIZ,
    BIZ_OTHER,
    make_project,
    make_task,
    make_world,
)

REPO = TaskRepository()
RESERVATIONS = BudgetReservationRepository()
USAGE = UsageRepository()
MEMBERS = MembershipRepository()
USERS = UserRepository()

PASSWORD = "correct-horse-1"
MEMBER_EMAIL = "member@example.com"
OTHER_EMAIL = "other@example.com"


def _list_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/usage/ambiguous-reservations"


def _detail_url(reservation_id: str, biz: str = BIZ) -> str:
    return f"{_list_url(biz)}/{reservation_id}"


def _confirm_url(reservation_id: str, biz: str = BIZ) -> str:
    return f"{_detail_url(reservation_id, biz)}/confirm-charge"


def _settings(**over: object) -> Settings:
    data: dict[str, object] = {
        "execution_lease_ttl_s": 300.0,
        "execution_heartbeat_interval_s": 60.0,
        "execution_lease_wall_s": 2400.0,
        "llm_max_tokens": 16,
    }
    data.update(over)
    return Settings(**data)


async def _claim(
    session: AsyncSession,
    *,
    task_id: str = "t_1",
    biz: str = BIZ,
    project_id: str = "proj_1",
    owner: str = "worker-a",
):
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


async def _expire(session: AsyncSession, *, task_id: str = "t_1", biz: str = BIZ) -> None:
    await session.execute(
        text(
            "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
            "WHERE business_id = :b AND id = :t"
        ),
        {"b": biz, "t": task_id},
    )
    await session.commit()


async def _reserve(
    session: AsyncSession,
    *,
    attempt_key: str,
    generation: int | None,
    task_id: str | None = "t_1",
    project_id: str = "proj_1",
    biz: str = BIZ,
    amount: Decimal = Decimal("2.00"),
) -> BudgetReservation:
    hold = await RESERVATIONS.reserve(
        session,
        biz,
        project_id=project_id,
        task_id=task_id,
        attempt=0,
        attempt_key=attempt_key,
        amount=amount,
        execution_generation=generation,
    )
    assert hold is not None
    return hold


async def _ambiguous(
    session: AsyncSession,
    *,
    attempt_key: str,
    generation: int | None,
    task_id: str | None = "t_1",
    project_id: str = "proj_1",
    biz: str = BIZ,
    amount: Decimal = Decimal("2.00"),
) -> BudgetReservation:
    hold = await _reserve(
        session,
        attempt_key=attempt_key,
        generation=generation,
        task_id=task_id,
        project_id=project_id,
        biz=biz,
        amount=amount,
    )
    assert (
        await RESERVATIONS.set_status(
            session,
            biz,
            hold.id,
            from_statuses=("active",),
            to_status="ambiguous",
            reason="timeout",
        )
        is True
    )
    return hold


def _charge_body(
    hold: BudgetReservation,
    *,
    task_id: str = "t_1",
    generation: int | None = 1,
    amount: str = "2.00",
    **over: object,
) -> dict:
    data: dict = {
        "taskId": task_id,
        "attemptKey": hold.attempt_key,
        "executionGeneration": generation,
        "provider": "openai",
        "model": "gpt-4o",
        "amount": amount,
        "currency": "USD",
        "providerRequestId": "req_1",
        "promptTokens": 10,
        "completionTokens": 5,
        "totalTokens": 15,
        "evidence": "Provider invoice line 1",
        "attestation": True,
    }
    data.update(over)
    return data


def _command(
    hold: BudgetReservation,
    *,
    task_id: str = "t_1",
    generation: int | None = 1,
    amount: Decimal = Decimal("2.00"),
    **over: object,
) -> ChargeCommand:
    data: dict = {
        "task_id": task_id,
        "attempt_key": hold.attempt_key,
        "execution_generation": generation,
        "provider": "openai",
        "model": "gpt-4o",
        "amount": amount,
        "currency": "USD",
        "provider_request_id": "req_1",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "evidence": "Provider invoice line 1",
    }
    data.update(over)
    return ChargeCommand(**data)


async def _seed_user(
    session: AsyncSession,
    *,
    user_id: str,
    email: str,
    biz_id: str = BIZ,
    role: str = "member",
) -> None:
    session.add(
        User(
            id=user_id,
            email=email,
            password_hash=hash_password(PASSWORD),
            display_name=user_id,
        )
    )
    await session.flush()
    await MEMBERS.grant(session, user_id, biz_id, role)
    await session.commit()


async def _authed_owner(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


async def _reload(session: AsyncSession):
    await session.rollback()


async def _holds(session: AsyncSession, biz: str = BIZ) -> list[BudgetReservation]:
    await session.rollback()
    rows = (
        await session.execute(
            select(BudgetReservation)
            .where(BudgetReservation.business_id == biz)
            .order_by(BudgetReservation.id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _usage_rows(session: AsyncSession, biz: str = BIZ) -> list[UsageRecord]:
    await session.rollback()
    rows = (
        await session.execute(
            select(UsageRecord)
            .where(UsageRecord.business_id == biz)
            .order_by(UsageRecord.id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _reconciliations(session: AsyncSession, biz: str = BIZ) -> list[UsageReconciliation]:
    await session.rollback()
    rows = (
        await session.execute(
            select(UsageReconciliation)
            .where(UsageReconciliation.business_id == biz)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return list(rows)


async def _task(session: AsyncSession, task_id: str = "t_1", biz: str = BIZ) -> Task:
    await session.rollback()
    task = await REPO.get(session, biz, task_id)
    assert task is not None
    await session.refresh(task)
    return task


async def _project(session: AsyncSession, project_id: str = "proj_1", biz: str = BIZ) -> Project:
    await session.rollback()
    task = (
        await session.execute(
            select(Project)
            .where(Project.business_id == biz, Project.id == project_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return task


# ---------------------------------------------------------------------------
# Happy path + accounting
# ---------------------------------------------------------------------------


async def test_owner_can_confirm_actual_provider_charge(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session,
        attempt_key=f"{BIZ}:t_1:a0",
        generation=claim.execution_generation,
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation)
    await session.commit()
    headers = await _authed_owner(session, api_client)

    res = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "reconciled"
    assert body["status"] == "reconciled"
    assert body["costSource"] == "provider"
    assert Decimal(str(body["amount"])) == Decimal("2.00")
    assert Decimal(str(body["taskCost"])) == Decimal("2.00")
    assert Decimal(str(body["projectSpend"])) == Decimal("2.00")
    assert body["actorEmail"] == OPERATOR_EMAIL
    assert body["usageRecordId"]
    assert body["reconciliationId"]

    holds = await _holds(session)
    assert len(holds) == 1
    assert holds[0].status == "reconciled"
    assert holds[0].reason == "manual_provider_charge"
    assert holds[0].usage_record_id == body["usageRecordId"]

    usage = await _usage_rows(session)
    assert len(usage) == 1
    usage_record_id = usage[0].id
    assert usage_record_id == body["usageRecordId"]
    assert usage[0].cost_amount == Decimal("2.00")
    assert usage[0].cost_source == "provider"
    assert usage[0].usage_status == "confirmed"
    assert usage[0].call_kind == "task_execution"
    assert usage[0].prompt_tokens == 10
    assert usage[0].completion_tokens == 5
    assert usage[0].total_tokens == 15
    assert usage[0].provider_request_id == "req_1"

    recon = await _reconciliations(session)
    assert len(recon) == 1
    assert recon[0].reservation_id == hold_id
    assert recon[0].usage_record_id == usage_record_id
    assert recon[0].outcome == "charged"
    assert recon[0].cost_source == "provider"
    assert recon[0].amount == Decimal("2.00")
    assert recon[0].attested is True
    assert recon[0].actor_email == OPERATOR_EMAIL
    assert recon[0].actor_user_id == "u_operator"
    assert recon[0].execution_generation == claim.execution_generation
    assert recon[0].evidence == "Provider invoice line 1"

    task = await _task(session)
    assert task.cost_accrued == Decimal("2.00")
    project = await _project(session)
    assert project.ai_spent == Decimal("2.00")
    assert await RESERVATIONS.pending_total(session, BIZ, "proj_1") == Decimal("0")

    events = (
        await session.execute(select(ActivityEvent).where(ActivityEvent.business_id == BIZ))
    ).scalars()
    events = list(events)
    assert len(events) == 1
    assert events[0].kind == "system"
    assert events[0].cost == Decimal("2.00")
    assert "reconciled" in events[0].text


async def test_null_generation_hold_can_be_reconciled_with_explicit_null(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """The NULL branch is exact: 0 never matches, explicit null does."""
    await make_world(session)
    await make_task(session, status="working")
    legacy = await _ambiguous(session, attempt_key="legacy-null", generation=None)
    legacy_id = legacy.id
    zero_body = _charge_body(legacy, generation=0)
    null_body = _charge_body(legacy, generation=None)
    await session.commit()
    headers = await _authed_owner(session, api_client)

    zero = await api_client.post(
        _confirm_url(legacy_id),
        json=zero_body,
        headers=headers,
    )
    assert zero.status_code == 409, zero.text
    assert zero.json()["error"]["code"] == "reservation_scope_mismatch"
    assert (await _holds(session))[0].status == "ambiguous"

    ok = await api_client.post(
        _confirm_url(legacy_id),
        json=null_body,
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    usage = await _usage_rows(session)
    assert len(usage) == 1
    assert usage[0].agent_id is None  # stale/unknown generation never invents an agent


async def test_unknown_ledger_row_uses_linked_reconciliation_key(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """An `unknown` row is not a charge; the confirmed correction is linked."""
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session,
        attempt_key=f"{BIZ}:t_1:a0",
        generation=claim.execution_generation,
    )
    hold_id = hold.id
    hold_key = hold.idempotency_key
    attempt_key = hold.attempt_key
    body = _charge_body(hold, generation=claim.execution_generation)
    await USAGE.append(
        session,
        BIZ,
        {
            "idempotency_key": f"usage:{hold_key}",
            "attempt_key": attempt_key,
            "attempt": 0,
            "project_id": "proj_1",
            "task_id": "t_1",
            "provider": "openai",
            "model": "gpt-4o",
            "cost_amount": None,
            "cost_source": "unknown",
            "usage_status": "unknown",
            "occurred_at": datetime.now(UTC),
        },
    )
    await session.commit()
    headers = await _authed_owner(session, api_client)

    res = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    usage = await _usage_rows(session)
    assert len(usage) == 2
    keys = {row.idempotency_key for row in usage}
    assert f"usage:{hold_key}" in keys
    assert f"usage:reconcile:{hold_id}:charge" in keys
    confirmed = [row for row in usage if row.usage_status == "confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0].cost_amount == Decimal("2.00")
    task = await _task(session)
    assert task.cost_accrued == Decimal("2.00")


# ---------------------------------------------------------------------------
# Authorization / validation
# ---------------------------------------------------------------------------


async def test_ordinary_member_cannot_reconcile(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation)
    await _seed_user(session, user_id="u_member", email=MEMBER_EMAIL, role="member")
    csrf = await login_as(api_client, email=MEMBER_EMAIL, password=PASSWORD)

    res = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "forbidden"
    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []
    assert await _reconciliations(session) == []


async def test_unauthenticated_and_csrf_invalid_requests_rejected(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation)

    unauth = await api_client.post(_confirm_url(hold_id), json=body)
    assert unauth.status_code == 401

    await seed_operator(session)
    csrf = await login_as(api_client)
    assert csrf

    missing = await api_client.post(_confirm_url(hold_id), json=body)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "forbidden"

    wrong = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert wrong.status_code == 403
    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []


async def test_owner_only_write_but_member_can_read(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    await _ambiguous(session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation)
    await _seed_user(session, user_id="u_member", email=MEMBER_EMAIL, role="member")
    await login_as(api_client, email=MEMBER_EMAIL, password=PASSWORD)

    res = await api_client.get(_list_url())
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 1


async def test_cross_business_access_is_uniform_404_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation)
    await make_world(session, biz_id=BIZ_OTHER)
    await _seed_user(session, user_id="u_other", email=OTHER_EMAIL, biz_id=BIZ_OTHER, role="owner")
    csrf = await login_as(api_client, email=OTHER_EMAIL, password=PASSWORD)

    # Foreign business path: not a member -> uniform 404.
    foreign_path = await api_client.post(
        _confirm_url(hold_id, biz=BIZ),
        json=body,
        headers={"X-CSRF-Token": csrf},
    )
    assert foreign_path.status_code == 404, foreign_path.text

    # Own business path, foreign reservation id -> scoped 404.
    own_path = await api_client.post(
        _confirm_url(hold_id, biz=BIZ_OTHER),
        json=body,
        headers={"X-CSRF-Token": csrf},
    )
    assert own_path.status_code == 404, own_path.text

    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []
    assert await _reconciliations(session) == []


async def test_cross_project_and_scope_mismatches_write_nothing(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_project(session, project_id="proj_2")
    await make_task(session, project_id="proj_2", task_id="t_other", status="working")
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    cases = [
        _charge_body(hold, task_id="t_other", generation=claim.execution_generation),
        _charge_body(hold, attemptKey="some-other-attempt", generation=claim.execution_generation),
        _charge_body(hold, generation=claim.execution_generation + 1),
        _charge_body(hold, generation=claim.execution_generation - 1),
    ]
    headers = await _authed_owner(session, api_client)

    for body in cases:
        res = await api_client.post(_confirm_url(hold_id), json=body, headers=headers)
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "reservation_scope_mismatch"

    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []
    assert await _reconciliations(session) == []


async def test_validation_rejects_missing_attestation_generation_and_zero_amount(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    invalid_bodies = [
        _charge_body(hold, generation=claim.execution_generation, attestation=False),
        _charge_body(hold, generation=claim.execution_generation, amount="0"),
        {k: v for k, v in _charge_body(hold, generation=1).items() if k != "executionGeneration"},
        _charge_body(hold, generation=claim.execution_generation, extraField="nope"),
    ]
    headers = await _authed_owner(session, api_client)

    for body in invalid_bodies:
        res = await api_client.post(_confirm_url(hold_id), json=body, headers=headers)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "validation_error"

    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []


async def test_currency_mismatch_is_422_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation, currency="EUR")
    headers = await _authed_owner(session, api_client)
    res = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers=headers,
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"
    assert (await _holds(session))[0].status == "ambiguous"
    assert await _usage_rows(session) == []


# ---------------------------------------------------------------------------
# Idempotency / duplicate protection
# ---------------------------------------------------------------------------


async def test_duplicate_reconciliation_is_rejected_without_second_charge(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    body = _charge_body(hold, generation=claim.execution_generation)
    headers = await _authed_owner(session, api_client)

    first = await api_client.post(_confirm_url(hold_id), json=body, headers=headers)
    assert first.status_code == 200, first.text
    second = await api_client.post(_confirm_url(hold_id), json=body, headers=headers)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "already_reconciled"

    assert len(await _usage_rows(session)) == 1
    assert len(await _reconciliations(session)) == 1
    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "reconciled"
    assert (await _task(session)).cost_accrued == Decimal("2.00")
    assert (await _project(session)).ai_spent == Decimal("2.00")


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_confirm_requests_create_exactly_one_outcome(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    command = _command(hold, generation=claim.execution_generation)
    await seed_operator(session)

    factory = session_factory(test_engine)
    start = asyncio.Event()

    async def attempt() -> object:
        async with factory() as other:
            async with other.begin():
                await start.wait()
                return await confirm_provider_charge(
                    other,
                    BIZ,
                    hold_id,
                    command,
                    actor_user_id="u_operator",
                    actor_email=OPERATOR_EMAIL,
                )

    tasks = [asyncio.ensure_future(attempt()) for _ in range(2)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15.0)

    successes = [r for r in results if isinstance(r, ConfirmedCharge)]
    conflicts = [r for r in results if isinstance(r, ReconciliationConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].code == "already_reconciled"

    assert len(await _usage_rows(session)) == 1
    assert len(await _reconciliations(session)) == 1
    assert (await _task(session)).cost_accrued == Decimal("2.00")
    assert (await _project(session)).ai_spent == Decimal("2.00")


async def test_late_settlement_cannot_charge_an_already_reconciled_hold(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    reservation_key = hold.idempotency_key
    attempt_key = hold.attempt_key
    body = _charge_body(hold, generation=claim.execution_generation)
    headers = await _authed_owner(session, api_client)
    confirmed = await api_client.post(
        _confirm_url(hold_id),
        json=body,
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    record_id = confirmed.json()["usageRecordId"]

    response = LlmResponse(
        text="ok",
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True),
    )
    record, won = await record_success(
        session,
        BIZ,
        reservation_id=hold_id,
        reservation_key=reservation_key,
        task_id="t_1",
        project_id="proj_1",
        call_kind="task_execution",
        attempt=0,
        attempt_key=attempt_key,
        execution_generation=claim.execution_generation,
        provider="openai",
        model="gpt-4o",
        response=response,
    )
    await session.commit()
    assert record is None and won is False

    assert (
        await mark_ambiguous(
            session,
            BIZ,
            hold_id,
            "late",
            task_id="t_1",
            attempt_key=attempt_key,
            execution_generation=claim.execution_generation,
        )
        is False
    )
    assert (
        await release_reservation(
            session,
            BIZ,
            hold_id,
            "late",
            task_id="t_1",
            attempt_key=attempt_key,
            execution_generation=claim.execution_generation,
        )
        is False
    )
    est_record, est_won, status = await record_estimated_failure(
        session,
        BIZ,
        reservation_id=hold_id,
        reservation_key=reservation_key,
        task_id="t_1",
        project_id="proj_1",
        call_kind="task_execution",
        attempt=0,
        attempt_key=attempt_key,
        execution_generation=claim.execution_generation,
        provider="openai",
        model="gpt-4o",
        input_bound_tokens=10,
        max_output_tokens=5,
    )
    await session.commit()
    assert est_record is None and est_won is False and status == "skipped"

    usage = await _usage_rows(session)
    assert len(usage) == 1 and usage[0].id == record_id
    assert (await _task(session)).cost_accrued == Decimal("2.00")
    assert (await _project(session)).ai_spent == Decimal("2.00")


async def test_settlement_and_manual_confirmation_race_has_one_charge(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    reservation_key = hold.idempotency_key
    attempt_key = hold.attempt_key
    command = _command(hold, generation=claim.execution_generation)
    await seed_operator(session)

    factory = session_factory(test_engine)
    start = asyncio.Event()

    async def settle() -> object:
        async with factory() as other:
            async with other.begin():
                await start.wait()
                return await record_success(
                    other,
                    BIZ,
                    reservation_id=hold_id,
                    reservation_key=reservation_key,
                    task_id="t_1",
                    project_id="proj_1",
                    call_kind="task_execution",
                    attempt=0,
                    attempt_key=attempt_key,
                    execution_generation=claim.execution_generation,
                    provider="openai",
                    model="gpt-4o",
                    response=LlmResponse(
                        text="ok",
                        usage=LlmUsage(
                            prompt_tokens=10,
                            completion_tokens=5,
                            total_tokens=15,
                            reported=True,
                        ),
                    ),
                )

    async def reconcile() -> object:
        async with factory() as other:
            async with other.begin():
                await start.wait()
                return await confirm_provider_charge(
                    other,
                    BIZ,
                    hold_id,
                    command,
                    actor_user_id="u_operator",
                    actor_email=OPERATOR_EMAIL,
                )

    start.set()
    results = await asyncio.wait_for(
        asyncio.gather(
            asyncio.ensure_future(settle()),
            asyncio.ensure_future(reconcile()),
            return_exceptions=True,
        ),
        15.0,
    )
    assert any(isinstance(r, ConfirmedCharge) for r in results)
    assert len(await _usage_rows(session)) == 1
    assert len(await _reconciliations(session)) == 1
    assert (await _task(session)).cost_accrued == Decimal("2.00")
    assert (await _project(session)).ai_spent == Decimal("2.00")


async def test_manual_confirmation_wins_and_recovery_never_reopens_hold(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    command = _command(hold, generation=claim.execution_generation)
    await seed_operator(session)
    await _expire(session)

    factory = session_factory(test_engine)
    manual_committed = asyncio.Event()
    allow_commit = asyncio.Event()

    async def manual() -> ConfirmedCharge:
        async with factory() as other:
            async with other.begin():
                result = await confirm_provider_charge(
                    other,
                    BIZ,
                    hold_id,
                    command,
                    actor_user_id="u_operator",
                    actor_email=OPERATOR_EMAIL,
                )
                manual_committed.set()
                await allow_commit.wait()
                return result

    async def recovery() -> int:
        async with factory() as other:
            async with other.begin():
                outcome = await recover_task(
                    other,
                    BIZ,
                    "proj_1",
                    "t_1",
                    recovery_owner="recovery:test",
                    lease_ttl_seconds=300.0,
                    lease_wall_seconds=2400.0,
                    stale_seconds=1800.0,
                )
                assert outcome.outcome == "recovered"
                await reconcile_task_reservations(
                    other,
                    BIZ,
                    "t_1",
                    before_generation=outcome.generation,
                )
                return 1

    manual_task = asyncio.ensure_future(manual())
    await asyncio.wait_for(manual_committed.wait(), 15.0)
    recovery_task = asyncio.ensure_future(recovery())
    await asyncio.sleep(0.3)  # let recovery block on the task row lock
    allow_commit.set()

    result = await asyncio.wait_for(manual_task, 15.0)
    assert result.reservation.id == hold_id
    await asyncio.wait_for(recovery_task, 15.0)

    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "reconciled"
    assert len(await _usage_rows(session)) == 1
    assert len(await _reconciliations(session)) == 1
    task = await _task(session)
    assert task.execution_generation == claim.execution_generation + 1
    assert task.cost_accrued == Decimal("2.00")
    assert (await _project(session)).ai_spent == Decimal("2.00")


async def test_recovery_can_precede_manual_confirmation_of_old_generation(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _reserve(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    command = _command(hold, generation=claim.execution_generation)
    await seed_operator(session)
    await _expire(session)

    factory = session_factory(test_engine)
    recovery_locked = asyncio.Event()
    allow_commit = asyncio.Event()

    async def recovery() -> None:
        async with factory() as other:
            async with other.begin():
                outcome = await recover_task(
                    other,
                    BIZ,
                    "proj_1",
                    "t_1",
                    recovery_owner="recovery:test",
                    lease_ttl_seconds=300.0,
                    lease_wall_seconds=2400.0,
                    stale_seconds=1800.0,
                )
                assert outcome.outcome == "recovered"
                await reconcile_task_reservations(
                    other, BIZ, "t_1", before_generation=outcome.generation
                )
                recovery_locked.set()
                await allow_commit.wait()

    async def manual() -> ConfirmedCharge:
        async with factory() as other:
            async with other.begin():
                return await confirm_provider_charge(
                    other,
                    BIZ,
                    hold_id,
                    command,
                    actor_user_id="u_operator",
                    actor_email=OPERATOR_EMAIL,
                )

    recovery_task = asyncio.ensure_future(recovery())
    await asyncio.wait_for(recovery_locked.wait(), 15.0)
    manual_task = asyncio.ensure_future(manual())
    await asyncio.sleep(0.3)  # let manual block on the task row lock
    allow_commit.set()

    await asyncio.wait_for(recovery_task, 15.0)
    result = await asyncio.wait_for(manual_task, 15.0)
    assert result.reservation.id == hold_id

    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "reconciled"
    assert len(await _usage_rows(session)) == 1
    task = await _task(session)
    assert task.execution_generation == claim.execution_generation + 1
    assert task.cost_accrued == Decimal("2.00")


# ---------------------------------------------------------------------------
# Atomicity / out-of-scope data
# ---------------------------------------------------------------------------


async def test_failed_transaction_leaves_no_partial_reconciliation_state(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claim = await _claim(session)
    hold = await _ambiguous(
        session, attempt_key=f"{BIZ}:t_1:a0", generation=claim.execution_generation
    )
    hold_id = hold.id
    command = _command(hold, generation=claim.execution_generation)
    await seed_operator(session)

    with pytest.raises(RuntimeError, match="injected"):
        async with session.begin():
            await confirm_provider_charge(
                session,
                BIZ,
                hold_id,
                command,
                actor_user_id="u_operator",
                actor_email=OPERATOR_EMAIL,
            )
            raise RuntimeError("injected")

    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "ambiguous"
    assert holds[0].usage_record_id is None
    assert await _usage_rows(session) == []
    assert await _reconciliations(session) == []
    assert (await _task(session)).cost_accrued == Decimal("0")
    assert (await _project(session)).ai_spent == Decimal("0")


async def test_taskless_holds_are_out_of_scope_and_unaffected(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    taskless = await _ambiguous(
        session,
        attempt_key="tl:brief_finalize:g0",
        generation=None,
        task_id=None,
        amount=Decimal("1.25"),
    )
    taskless_id = taskless.id
    taskless_body = _charge_body(taskless, generation=None)
    headers = await _authed_owner(session, api_client)

    listed = await api_client.get(_list_url(), headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 0
    assert listed.json()["items"] == []

    detail = await api_client.get(_detail_url(taskless_id), headers=headers)
    assert detail.status_code == 404

    confirmed = await api_client.post(
        _confirm_url(taskless_id),
        json=taskless_body,
        headers=headers,
    )
    assert confirmed.status_code == 404

    holds = await _holds(session)
    assert len(holds) == 1 and holds[0].status == "ambiguous"
    assert await _usage_rows(session) == []
    assert await _reconciliations(session) == []

    # Existing business/project usage summary still counts taskless ambiguity.
    summary = await api_client.get(
        f"/api/v1/businesses/{BIZ}/usage?project_id=proj_1",
        headers=headers,
    )
    assert summary.status_code == 200, summary.text
    assert Decimal(str(summary.json()["summary"]["reservations"]["ambiguous"])) == Decimal("1.25")


async def test_read_endpoints_expose_only_authorized_business_data(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    own = await _ambiguous(session, attempt_key="own", generation=1)
    own_id = own.id

    await make_world(session, biz_id=BIZ_OTHER)
    await make_task(
        session,
        task_id="t_biz_other",
        biz_id=BIZ_OTHER,
        project_id="proj_biz_other",
        owner_id="ag_biz_other",
        status="working",
    )
    foreign = await _ambiguous(
        session,
        attempt_key="foreign",
        generation=1,
        biz=BIZ_OTHER,
        project_id="proj_biz_other",
        task_id="t_biz_other",
    )
    foreign_id = foreign.id
    await _seed_user(session, user_id="u_other", email=OTHER_EMAIL, biz_id=BIZ_OTHER, role="owner")
    await login_as(api_client, email=OTHER_EMAIL, password=PASSWORD)

    own_list = await api_client.get(_list_url(BIZ_OTHER))
    assert own_list.status_code == 200, own_list.text
    assert [item["id"] for item in own_list.json()["items"]] == [foreign_id]

    cross_detail = await api_client.get(_detail_url(own_id, biz=BIZ_OTHER))
    assert cross_detail.status_code == 404

    # BIZ is not a member business for this caller.
    cross_list = await api_client.get(_list_url(BIZ))
    assert cross_list.status_code == 404

    # Owner of BIZ sees only BIZ data (and the read helper is not weakened).
    await session.rollback()
    await seed_operator(session, biz_id=BIZ)
    csrf_owner = await login_as(api_client)
    assert csrf_owner
    own_only = await api_client.get(_list_url(BIZ))
    assert own_only.status_code == 200, own_only.text
    assert [item["id"] for item in own_only.json()["items"]] == [own_id]

    detail = await api_client.get(_detail_url(own_id))
    assert detail.status_code == 200, detail.text
    assert detail.json()["taskId"] == "t_1"
    assert detail.json()["usage"] == []


async def test_read_detail_includes_attempt_ledger_evidence(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    hold = await _ambiguous(session, attempt_key="evidence", generation=1)
    hold_id = hold.id
    await USAGE.append(
        session,
        BIZ,
        {
            "idempotency_key": "usage:evidence",
            "attempt_key": "evidence",
            "attempt": 0,
            "project_id": "proj_1",
            "task_id": "t_1",
            "provider": "openai",
            "model": "gpt-4o",
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
            "cost_amount": None,
            "cost_source": "unknown",
            "usage_status": "unknown",
            "occurred_at": datetime.now(UTC),
        },
    )
    await session.commit()
    headers = await _authed_owner(session, api_client)

    res = await api_client.get(_detail_url(hold_id), headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ambiguous"
    assert len(body["usage"]) == 1
    assert body["usage"][0]["provider"] == "openai"
    assert body["usage"][0]["model"] == "gpt-4o"
    assert body["usage"][0]["usageStatus"] == "unknown"
    assert body["usage"][0]["costAmount"] is None


async def test_list_filters_are_business_scoped_and_reject_foreign_scope(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_project(session, project_id="proj_2")
    await make_task(session, task_id="t_2", project_id="proj_2", status="working")
    await make_task(session, status="working")
    one = await _ambiguous(session, attempt_key="one", generation=1)
    two = await _ambiguous(
        session, attempt_key="two", generation=1, task_id="t_2", project_id="proj_2"
    )
    one_id = one.id
    two_id = two.id
    await session.commit()
    headers = await _authed_owner(session, api_client)

    by_project = await api_client.get(f"{_list_url()}?project_id=proj_2", headers=headers)
    assert by_project.status_code == 200, by_project.text
    assert [item["id"] for item in by_project.json()["items"]] == [two_id]

    by_task = await api_client.get(f"{_list_url()}?task_id=t_1", headers=headers)
    assert by_task.status_code == 200, by_task.text
    assert [item["id"] for item in by_task.json()["items"]] == [one_id]

    mismatch = await api_client.get(f"{_list_url()}?project_id=proj_2&task_id=t_1", headers=headers)
    assert mismatch.status_code == 404

    foreign_project = await api_client.get(f"{_list_url()}?project_id=missing", headers=headers)
    assert foreign_project.status_code == 404
