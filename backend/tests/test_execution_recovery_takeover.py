"""S2.2.4.4: expired-lease recovery takeover and generation bump.

Real PostgreSQL. The takeover primitive is a conditional UPDATE whose
predicates, row locks and database-time comparisons are exercised directly;
the recovery endpoint is then tested for eligibility, fencing of the old
worker, restart persistence and financial neutrality.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution import executor as executor_module
from app.execution.executor import ExecutionRequest, execute_task
from app.execution.recovery import (
    LEASE_COMPLETE,
    LEASE_INCOMPLETE,
    LEASE_NONE,
    lease_state,
)
from app.llm.types import LlmResponse, LlmUsage, ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.project import Project
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import (
    ActivityRepository,
    TaskOwnership,
    TaskRepository,
)
from app.persistence.repositories.usage import BudgetReservationRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_task, make_world
from tests.factories import tasks as tasks_repo

REPO = TaskRepository()
RESERVATIONS = BudgetReservationRepository()


def _recover_url(task_id: str = "t_1", biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/projects/proj_1/tasks/{task_id}/recover"


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


async def _expire(session: AsyncSession, task_id: str = "t_1") -> None:
    """Expire the lease in DATABASE time, keeping the absolute wall alive."""
    await session.execute(
        text(
            "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
            "WHERE business_id = :b AND id = :t"
        ),
        {"b": BIZ, "t": task_id},
    )
    await session.commit()


async def _pass_deadline(session: AsyncSession, task_id: str = "t_1") -> None:
    await session.execute(
        text(
            "UPDATE tasks SET lease_expires_at = now() - interval '3 seconds', "
            "lease_deadline_at = now() - interval '2 seconds' "
            "WHERE business_id = :b AND id = :t"
        ),
        {"b": BIZ, "t": task_id},
    )
    await session.commit()


async def _fresh(session: AsyncSession, task_id: str = "t_1"):
    task = await tasks_repo.get(session, BIZ, task_id)
    assert task is not None
    await session.refresh(task)
    return task


async def _reservations(session: AsyncSession, task_id: str = "t_1") -> list[BudgetReservation]:
    await session.rollback()
    rows = (
        await session.execute(
            select(BudgetReservation).where(
                BudgetReservation.business_id == BIZ,
                BudgetReservation.task_id == task_id,
            )
        )
    ).scalars()
    return list(rows)


async def _spend(session: AsyncSession) -> object:
    return (
        await session.execute(
            select(Project.ai_spent).where(Project.business_id == BIZ, Project.id == "proj_1")
        )
    ).scalar_one()


async def _takeover(
    session: AsyncSession,
    *,
    expected: TaskOwnership,
    owner: str = "recovery:b",
    ttl: float = 300.0,
    wall: float = 2400.0,
    task_id: str = "t_1",
    biz: str = BIZ,
) -> TaskOwnership | None:
    return await REPO.takeover_expired(
        session,
        biz,
        task_id,
        expected_execution_generation=expected.execution_generation,
        expected_lease_owner=expected.lease_owner,
        lease_owner=owner,
        lease_ttl_seconds=ttl,
        lease_wall_seconds=wall,
    )


# ---------------------------------------------------------------------------
# Eligibility classification
# ---------------------------------------------------------------------------


def test_lease_state_classification_fails_closed_on_partial_fields() -> None:
    complete = SimpleNamespace(
        lease_owner="w", lease_expires_at=object(), lease_deadline_at=object()
    )
    assert lease_state(complete) == LEASE_COMPLETE
    assert (
        lease_state(
            SimpleNamespace(lease_owner=None, lease_expires_at=None, lease_deadline_at=None)
        )
        == LEASE_NONE
    )
    for task in (
        SimpleNamespace(lease_owner="w", lease_expires_at=None, lease_deadline_at=None),
        SimpleNamespace(lease_owner=None, lease_expires_at=object(), lease_deadline_at=None),
        SimpleNamespace(lease_owner=None, lease_expires_at=None, lease_deadline_at=object()),
        SimpleNamespace(lease_owner="w", lease_expires_at=object(), lease_deadline_at=None),
    ):
        assert lease_state(task) == LEASE_INCOMPLETE


# ---------------------------------------------------------------------------
# Atomic takeover primitive
# ---------------------------------------------------------------------------


async def test_takeover_increments_generation_once_and_returns_persisted_tuple(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session, owner="worker-a")
    await _expire(session)

    new = await _takeover(session, expected=stale, owner="recovery:b")
    await session.commit()

    assert new is not None
    assert new.execution_generation == stale.execution_generation + 1 == 2
    assert new.lease_owner == "recovery:b" != stale.lease_owner
    task = await _fresh(session)
    assert task.execution_generation == 2
    assert task.lease_owner == "recovery:b"
    assert task.lease_expires_at == new.lease_expires_at
    assert task.lease_deadline_at == new.lease_deadline_at
    assert task.status == "working"
    assert task.lease_expires_at <= task.lease_deadline_at

    # Old token is fenced out of both renewal and persistence.
    old_renew = await REPO.renew_lease(
        session,
        BIZ,
        "t_1",
        execution_generation=stale.execution_generation,
        lease_owner=stale.lease_owner,
        ttl_seconds=300,
    )
    assert old_renew.renewed is False
    assert (
        await REPO.fence_owned(
            session,
            BIZ,
            "t_1",
            execution_generation=stale.execution_generation,
            lease_owner=stale.lease_owner,
            values={"status": "completed"},
        )
        is False
    )

    # New token can renew and fence.
    new_renew = await REPO.renew_lease(
        session,
        BIZ,
        "t_1",
        execution_generation=new.execution_generation,
        lease_owner=new.lease_owner,
        ttl_seconds=300,
    )
    assert new_renew.renewed is True
    assert (
        await REPO.fence_owned(
            session,
            BIZ,
            "t_1",
            execution_generation=new.execution_generation,
            lease_owner=new.lease_owner,
            values={"status": "completed", "summary": "recovered"},
        )
        is True
    )
    await session.commit()
    task = await _fresh(session)
    assert task.status == "completed" and task.summary == "recovered"


@pytest.mark.parametrize("case", ["business", "task", "generation", "owner"])
async def test_takeover_rejects_wrong_tuple(session: AsyncSession, case: str) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await _expire(session)

    kwargs: dict[str, object] = {
        "biz": BIZ,
        "task_id": "t_1",
        "expected": stale,
        "owner": "recovery:b",
    }
    if case == "business":
        kwargs["biz"] = BIZ_OTHER
    elif case == "task":
        kwargs["task_id"] = "t_missing"
    elif case == "generation":
        kwargs["expected"] = TaskOwnership(
            business_id=stale.business_id,
            task_id=stale.task_id,
            execution_generation=stale.execution_generation + 1,
            lease_owner=stale.lease_owner,
            lease_expires_at=stale.lease_expires_at,
            lease_deadline_at=stale.lease_deadline_at,
        )
    else:
        kwargs["expected"] = TaskOwnership(
            business_id=stale.business_id,
            task_id=stale.task_id,
            execution_generation=stale.execution_generation,
            lease_owner="worker-other",
            lease_expires_at=stale.lease_expires_at,
            lease_deadline_at=stale.lease_deadline_at,
        )

    assert await _takeover(session, **kwargs) is None  # type: ignore[arg-type]
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == 1
    assert task.lease_owner == "worker-a"


async def test_takeover_rejects_live_lease_and_non_working_status(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await session.commit()

    # Live lease: never displace a healthy owner.
    assert await _takeover(session, expected=stale) is None

    await session.execute(
        text("UPDATE tasks SET status = 'completed' WHERE business_id = :b AND id = 't_1'"),
        {"b": BIZ},
    )
    await session.commit()
    assert await _takeover(session, expected=stale) is None
    task = await _fresh(session)
    assert task.status == "completed" and task.execution_generation == 1


async def test_takeover_rejects_passed_absolute_deadline(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await _pass_deadline(session)

    assert await _takeover(session, expected=stale) is None
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == 1
    assert task.lease_owner == "worker-a"


async def test_repeated_takeover_requires_a_new_expiration_cycle(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await _expire(session)
    first = await _takeover(session, expected=stale, owner="recovery:b")
    await session.commit()
    assert first is not None

    # The new lease is live: a second takeover using either token must fail
    # and must not increment the generation again.
    assert await _takeover(session, expected=stale, owner="recovery:c") is None
    assert await _takeover(session, expected=first, owner="recovery:c") is None
    task = await _fresh(session)
    assert task.execution_generation == 2
    assert task.lease_owner == "recovery:b"


async def test_takeover_validates_timing_and_owner_inputs(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await _expire(session)
    base = {
        "expected_execution_generation": stale.execution_generation,
        "expected_lease_owner": stale.lease_owner,
        "lease_owner": "recovery:b",
        "lease_ttl_seconds": 300.0,
        "lease_wall_seconds": 2400.0,
    }
    with pytest.raises(ValueError, match="new lease_owner"):
        await REPO.takeover_expired(
            session, BIZ, "t_1", **{**base, "lease_owner": stale.lease_owner}
        )
    for ttl, wall in ((0.0, 10.0), (10.0, 0.0), (float("inf"), 10.0), (20.0, 10.0)):
        with pytest.raises(ValueError):
            await REPO.takeover_expired(
                session,
                BIZ,
                "t_1",
                **{**base, "lease_ttl_seconds": ttl, "lease_wall_seconds": wall},
            )
    with pytest.raises(ValueError, match="non-negative"):
        await REPO.takeover_expired(
            session, BIZ, "t_1", **{**base, "expected_execution_generation": -1}
        )


# ---------------------------------------------------------------------------
# Old-worker fencing (reuses S2.2.4.3 primitives)
# ---------------------------------------------------------------------------


async def test_old_worker_cannot_compensate_after_takeover(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session, owner="worker-a")
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=1,
    )
    assert hold is not None
    hold_id = hold.id
    await RESERVATIONS.set_status(
        session, BIZ, hold_id, from_statuses=("active",), to_status="ambiguous", reason="test"
    )
    await session.commit()
    await _expire(session)

    assert await _takeover(session, expected=stale, owner="recovery:b") is not None
    await session.commit()
    claim = executor_module._Claim(
        task_id="t_1",
        pre_status="not_started",
        pre_attempts=0,
        project_id="proj_1",
        attempt_key=f"{BIZ}:t_1:a0",
        ownership=stale,
    )
    await session.rollback()
    assert (
        await executor_module._compensate_claim(
            session, BIZ, claim, reservation_id=hold_id, settle="ambiguous"
        )
        is False
    )

    task = await _fresh(session)
    assert task.status == "working" and task.execution_generation == 2
    reservations = await _reservations(session)
    assert len(reservations) == 1
    assert reservations[0].status == "ambiguous"
    assert reservations[0].reason == "test"


# ---------------------------------------------------------------------------
# Concurrency with independent PostgreSQL sessions
# ---------------------------------------------------------------------------


async def test_concurrent_recovery_workers_have_exactly_one_winner(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await _expire(session)
    factory = session_factory(test_engine)
    started = asyncio.Event()

    async def contender() -> TaskOwnership | None:
        async with factory() as other:
            async with other.begin():
                started.set()
                return await _takeover(other, expected=stale, owner="recovery:loser")

    async with session.begin():
        winner = await _takeover(session, expected=stale, owner="recovery:winner")
        loser_task = asyncio.ensure_future(contender())
        await asyncio.wait_for(started.wait(), 5.0)

    assert winner is not None
    assert winner.lease_owner == "recovery:winner"
    assert await asyncio.wait_for(loser_task, 10.0) is None

    task = await _fresh(session)
    assert task.execution_generation == 2
    assert task.lease_owner == "recovery:winner"


async def test_claim_and_recovery_cannot_both_transition(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    # Reverse ordering: a normal claim wins first (live lease, generation 1).
    claimed = await _claim(session, owner="worker-a")
    await session.commit()
    renewed = await REPO.renew_lease(
        session,
        BIZ,
        "t_1",
        execution_generation=claimed.execution_generation,
        lease_owner=claimed.lease_owner,
        ttl_seconds=300,
    )
    assert renewed.renewed is True
    stale_tuple = TaskOwnership(
        business_id=claimed.business_id,
        task_id=claimed.task_id,
        execution_generation=0,
        lease_owner="legacy-owner",
        lease_expires_at=None,
        lease_deadline_at=None,
    )
    assert await _takeover(session, expected=stale_tuple) is None

    # Recovery ordering: once expired, the claim must lose (status is working)
    # and the takeover must win exactly once.
    await _expire(session)
    assert (
        await REPO.claim_owned(
            session,
            BIZ,
            "t_1",
            from_statuses=("not_started", "failed"),
            to_status="working",
            lease_owner="worker-b",
            lease_ttl_seconds=300,
            lease_wall_seconds=2400,
        )
        is None
    )
    won = await _takeover(session, expected=claimed, owner="recovery:b")
    await session.commit()
    assert won is not None and won.execution_generation == 2
    task = await _fresh(session)
    assert task.lease_owner == "recovery:b" and task.execution_generation == 2
    # The losing (previous) token can no longer renew.
    assert (
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=claimed.execution_generation,
            lease_owner=claimed.lease_owner,
            ttl_seconds=300,
        )
    ).renewed is False


# ---------------------------------------------------------------------------
# Claim-versus-recovery across independent PostgreSQL sessions
# ---------------------------------------------------------------------------


async def test_claim_and_recovery_stale_snapshots_across_sessions(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Two independent sessions; stale observations can never overwrite a win.

    Ordering 1: a claim commits first; a recovery worker still holding the
    pre-claim (no-lease) snapshot must not displace it.
    Ordering 2: an expired lease is taken over first; a racing normal claim
    cannot transition the still-`working` row.
    """
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)

    # --- Ordering 1: claim wins, stale recovery loses -----------------------
    async with factory() as claimer:
        async with claimer.begin():
            claimed = await REPO.claim_owned(
                claimer,
                BIZ,
                "t_1",
                from_statuses=("not_started",),
                to_status="working",
                lease_owner="worker-a",
                lease_ttl_seconds=300,
                lease_wall_seconds=2400,
            )
        assert claimed is not None and claimed.execution_generation == 1
        async with claimer.begin():
            renewed = await REPO.renew_lease(
                claimer,
                BIZ,
                "t_1",
                execution_generation=claimed.execution_generation,
                lease_owner=claimed.lease_owner,
                ttl_seconds=300,
            )
        assert renewed.renewed is True

    async with factory() as recovery:
        async with recovery.begin():
            assert (
                await REPO.takeover_expired(
                    recovery,
                    BIZ,
                    "t_1",
                    expected_execution_generation=0,
                    expected_lease_owner="legacy-owner",
                    lease_owner="recovery:stale",
                    lease_ttl_seconds=300,
                    lease_wall_seconds=2400,
                )
                is None
            )

    # --- Ordering 2: recovery wins, racing claim loses ----------------------
    async with factory() as recovery:
        async with recovery.begin():
            await recovery.execute(
                text(
                    "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
                    "WHERE business_id = :b AND id = 't_1'"
                ),
                {"b": BIZ},
            )
            taken = await REPO.takeover_expired(
                recovery,
                BIZ,
                "t_1",
                expected_execution_generation=claimed.execution_generation,
                expected_lease_owner=claimed.lease_owner,
                lease_owner="recovery:winner",
                lease_ttl_seconds=300,
                lease_wall_seconds=2400,
            )
        assert taken is not None and taken.execution_generation == 2

    async with factory() as claimer:
        async with claimer.begin():
            loser = await REPO.claim_owned(
                claimer,
                BIZ,
                "t_1",
                from_statuses=("not_started", "failed"),
                to_status="working",
                lease_owner="worker-b",
                lease_ttl_seconds=300,
                lease_wall_seconds=2400,
            )
        assert loser is None

    # The generation-1 token can no longer renew or persist; the generation-2
    # winner can do both.
    async with factory() as check:
        async with check.begin():
            assert (
                await REPO.renew_lease(
                    check,
                    BIZ,
                    "t_1",
                    execution_generation=claimed.execution_generation,
                    lease_owner=claimed.lease_owner,
                    ttl_seconds=300,
                )
            ).renewed is False
            assert (
                await REPO.fence_owned(
                    check,
                    BIZ,
                    "t_1",
                    execution_generation=claimed.execution_generation,
                    lease_owner=claimed.lease_owner,
                    values={"status": "completed"},
                )
                is False
            )
        async with check.begin():
            assert (
                await REPO.renew_lease(
                    check,
                    BIZ,
                    "t_1",
                    execution_generation=taken.execution_generation,
                    lease_owner=taken.lease_owner,
                    ttl_seconds=300,
                )
            ).renewed is True
        async with check.begin():
            assert (
                await REPO.fence_owned(
                    check,
                    BIZ,
                    "t_1",
                    execution_generation=taken.execution_generation,
                    lease_owner=taken.lease_owner,
                    values={"status": "completed"},
                )
                is True
            )

    task = await _fresh(session)
    assert task.execution_generation == 2
    assert task.lease_owner == "recovery:winner"
    assert task.status == "completed"


# ---------------------------------------------------------------------------
# Recovery endpoint integration
# ---------------------------------------------------------------------------


async def test_endpoint_refuses_live_lease_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claimed = await _claim(session)
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=2,
    )
    assert hold is not None
    await session.commit()
    before_events = len(await ActivityRepository().list(session, BIZ))
    headers = await _authed(session, api_client)

    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 409, res.text
    assert res.json()["outcome"] == "not_stale"
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == claimed.execution_generation
    assert task.lease_owner == "worker-a"
    assert len(await _reservations(session)) == 1
    assert (await _reservations(session))[0].status == "active"
    assert len(await ActivityRepository().list(session, BIZ)) == before_events


async def test_endpoint_takes_over_expired_lease_and_bumps_generation(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", cost_accrued="4.50")
    stale = await _claim(session, owner="worker-a")
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=3,
    )
    assert hold is not None
    await session.commit()
    from app.persistence.repositories.spend import recalculate_project_spend

    await recalculate_project_spend(session, BIZ, "proj_1")
    await session.commit()
    spend_before = await _spend(session)
    await _expire(session)
    headers = await _authed(session, api_client)

    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "recovered" and body["status"] == "not_started"
    task = await _fresh(session)
    assert task.status == "not_started"
    assert task.execution_generation == stale.execution_generation + 1 == 2
    assert task.lease_owner is not None and task.lease_owner.startswith("recovery:")
    assert task.lease_owner != stale.lease_owner
    # Old token is dead; the task can be claimed again normally.
    assert (
        await REPO.renew_lease(
            session,
            BIZ,
            "t_1",
            execution_generation=stale.execution_generation,
            lease_owner=stale.lease_owner,
            ttl_seconds=300,
        )
    ).renewed is False
    # Takeover itself changed no money; pre-existing recovery policy holds the
    # possibly-billed reservation.
    assert await _spend(session) == spend_before
    assert (await _fresh(session)).cost_accrued == Decimal("4.50")
    hold = (await _reservations(session))[0]
    assert hold.status == "ambiguous" and hold.reason == "recovery_stale"
    events = await ActivityRepository().list(session, BIZ, project_id="proj_1")
    assert any("recovered from expired lease" in e.text for e in events)

    # A second scan/recovery must not bump again.
    again = await api_client.post(_recover_url(), headers=headers)
    assert again.status_code == 409, again.text
    assert (await _fresh(session)).execution_generation == 2


async def test_endpoint_keeps_attempt_history_on_takeover(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    stale = await _claim(session)
    await session.execute(
        text("UPDATE tasks SET attempts = 2 WHERE business_id = :b AND id = 't_1'"),
        {"b": BIZ},
    )
    await session.commit()
    await _expire(session)
    headers = await _authed(session, api_client)

    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "failed"
    task = await _fresh(session)
    assert task.status == "failed" and task.attempts == 2
    assert task.execution_generation == stale.execution_generation + 1


async def test_endpoint_refuses_passed_deadline_without_writes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    claimed = await _claim(session)
    await _pass_deadline(session)
    before_events = len(await ActivityRepository().list(session, BIZ))
    headers = await _authed(session, api_client)

    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 409, res.text
    assert res.json()["outcome"] == "not_stale"
    task = await _fresh(session)
    assert task.status == "working"
    assert task.execution_generation == claimed.execution_generation
    assert task.lease_owner == "worker-a"
    assert len(await ActivityRepository().list(session, BIZ)) == before_events


async def test_endpoint_legacy_no_lease_still_uses_staleness_clock(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await session.commit()
    await session.execute(
        text("UPDATE tasks SET updated_at = now() - interval '7200 seconds' WHERE id = 't_1'")
    )
    await session.commit()
    headers = await _authed(session, api_client)

    res = await api_client.post(_recover_url(), headers=headers)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "not_started"
    task = await _fresh(session)
    assert task.execution_generation == 0  # no ownership token existed
    assert task.lease_owner is None


# ---------------------------------------------------------------------------
# Real takeover during an in-flight provider call
# ---------------------------------------------------------------------------


class _GatedProvider:
    """Provider call that blocks until the test releases it."""

    name = "mock"
    _default_model = "mock"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[object] = []
        self._error = error

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        self.entered.set()
        await self.release.wait()
        if self._error is not None:
            raise self._error
        return LlmResponse(
            text='{"summary": "stale"}',
            data={"summary": "stale"},
            usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True),
            model="mock",
        )


async def _takeover_in_flight(session: AsyncSession, factory) -> TaskOwnership:
    """Expire the live lease and take over while the old worker is paused."""
    async with factory() as other:
        async with other.begin():
            row = await tasks_repo.get(other, BIZ, "t_1")
            assert row is not None and row.lease_owner is not None
            old = TaskOwnership(
                business_id=BIZ,
                task_id="t_1",
                execution_generation=row.execution_generation,
                lease_owner=row.lease_owner,
                lease_expires_at=row.lease_expires_at,
                lease_deadline_at=row.lease_deadline_at,
            )
        async with other.begin():
            await other.execute(
                text(
                    "UPDATE tasks SET lease_expires_at = now() - interval '1 second' "
                    "WHERE business_id = :b AND id = 't_1'"
                ),
                {"b": BIZ},
            )
            new = await REPO.takeover_expired(
                other,
                BIZ,
                "t_1",
                expected_execution_generation=old.execution_generation,
                expected_lease_owner=old.lease_owner,
                lease_owner="recovery:inflight",
                lease_ttl_seconds=300,
                lease_wall_seconds=2400,
            )
        assert new is not None and new.execution_generation == old.execution_generation + 1
        return new


def _long_heartbeat_settings() -> Settings:
    # First heartbeat tick is far beyond the test, so no renewal can race the
    # deliberate takeover. TTL stays within the wall (config invariant).
    return Settings(
        execution_lease_ttl_s=2400.0,
        execution_heartbeat_interval_s=1800.0,
        execution_lease_wall_s=2400.0,
    )


async def test_stale_worker_cannot_persist_success_after_real_takeover(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", summary="original")
    await session.commit()
    factory = session_factory(test_engine)
    provider = _GatedProvider()
    run = asyncio.ensure_future(
        execute_task(
            session,
            provider,
            ExecutionRequest(
                business_id=BIZ,
                project_id="proj_1",
                task_id="t_1",
                timeout_s=10.0,
                settings=_long_heartbeat_settings(),
                session_factory=factory,
            ),
        )
    )
    await asyncio.wait_for(provider.entered.wait(), 5.0)
    new = await _takeover_in_flight(session, factory)
    provider.release.set()

    result = await asyncio.wait_for(run, 10.0)

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost" and result.ownership_lost is True
    task = await _fresh(session)
    assert task.status == "working"
    assert task.summary == "original"
    assert task.attempts == 0
    assert task.execution_generation == new.execution_generation == 2
    assert task.lease_owner == "recovery:inflight"
    events = await ActivityRepository().list(session, BIZ, project_id="proj_1")
    assert [e for e in events if e.kind == "result"] == []
    hold = (await _reservations(session))[0]
    assert hold.status == "ambiguous" and hold.reason == "ownership_lost"


async def test_stale_worker_cannot_persist_failure_after_real_takeover(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    provider = _GatedProvider(error=ProviderError("boom"))
    run = asyncio.ensure_future(
        execute_task(
            session,
            provider,
            ExecutionRequest(
                business_id=BIZ,
                project_id="proj_1",
                task_id="t_1",
                timeout_s=10.0,
                settings=_long_heartbeat_settings(),
                session_factory=factory,
            ),
        )
    )
    await asyncio.wait_for(provider.entered.wait(), 5.0)
    new = await _takeover_in_flight(session, factory)
    provider.release.set()

    result = await asyncio.wait_for(run, 10.0)

    assert result.outcome == "unknown"
    assert result.error_code == "ownership_lost" and result.ownership_lost is True
    task = await _fresh(session)
    assert task.status == "working"
    assert task.attempts == 0
    assert task.execution_generation == new.execution_generation == 2
    assert task.lease_owner == "recovery:inflight"
    assert [e for e in await ActivityRepository().list(session, BIZ) if e.kind == "system"] == []
    # The provider error proves a pre-send failure, so only the old execution's
    # own hold may be released; the task remains untouched.
    hold = (await _reservations(session))[0]
    assert hold.status == "released" and hold.reason == "ownership_lost_pre_send"


# ---------------------------------------------------------------------------
# Financial neutrality of takeover alone
# ---------------------------------------------------------------------------


async def test_takeover_alone_creates_no_ledger_spend_or_reservation_change(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started", cost_accrued="4.50")
    stale = await _claim(session)
    hold = await RESERVATIONS.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=3,
    )
    assert hold is not None
    await session.commit()
    from app.persistence.repositories.spend import recalculate_project_spend

    await recalculate_project_spend(session, BIZ, "proj_1")
    await session.commit()
    spend_before = await _spend(session)
    await _expire(session)
    usage_before = (
        await session.execute(select(func.count()).select_from(UsageRecord))
    ).scalar_one()

    assert await _takeover(session, expected=stale) is not None
    await session.commit()

    assert await _spend(session) == spend_before
    assert (await _fresh(session)).cost_accrued == Decimal("4.50")
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "active"
    usage_after = (
        await session.execute(select(func.count()).select_from(UsageRecord))
    ).scalar_one()
    assert usage_after == usage_before == 0
