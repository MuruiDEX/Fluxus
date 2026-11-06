"""S2.2.4.1 execution ownership foundation (PostgreSQL).

Covers only what this slice introduces: the task ownership columns/CHECKs, the
reservation generation column, and the minimal generation-aware repository
primitives. Heartbeats, recovery takeover and executor fencing belong to later
slices and are deliberately absent.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.persistence.engine import session_factory
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import TaskOwnership, TaskRepository
from app.persistence.repositories.usage import BudgetReservationRepository
from tests.factories import BIZ, make_task, make_world
from tests.factories import tasks as tasks_repo

REPO = TaskRepository()


def _lease(**over: object) -> dict[str, object]:
    now = datetime.now(UTC)
    data: dict[str, object] = {
        "lease_owner": "worker-1",
        "lease_expires_at": now + timedelta(seconds=60),
        "lease_deadline_at": now + timedelta(seconds=120),
    }
    data.update(over)
    return data


# ---------------------------------------------------------------------------
# Model / CHECK constraints
# ---------------------------------------------------------------------------


async def test_lease_all_null_allowed(session: AsyncSession) -> None:
    await make_world(session)
    task = await make_task(session, status="not_started")

    assert task.lease_owner is None
    assert task.lease_expires_at is None
    assert task.lease_deadline_at is None


async def test_lease_all_non_null_allowed(session: AsyncSession) -> None:
    await make_world(session)
    task = await make_task(session, **_lease())

    assert task.lease_owner == "worker-1"
    assert task.lease_expires_at is not None
    assert task.lease_deadline_at is not None


async def test_expired_but_consistent_lease_allowed(session: AsyncSession) -> None:
    await make_world(session)
    now = datetime.now(UTC)
    task = await make_task(
        session,
        **_lease(
            lease_expires_at=now - timedelta(seconds=60),
            lease_deadline_at=now + timedelta(seconds=60),
        ),
    )

    assert task.lease_expires_at < task.lease_deadline_at


async def test_lease_owner_only_rejected(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(IntegrityError):
        await make_task(session, lease_owner="worker-1")


async def test_lease_expiry_only_rejected(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(IntegrityError):
        await make_task(session, lease_expires_at=datetime.now(UTC) + timedelta(seconds=60))


async def test_lease_owner_and_expiry_without_deadline_rejected(
    session: AsyncSession,
) -> None:
    await make_world(session)
    with pytest.raises(IntegrityError):
        await make_task(
            session,
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )


async def test_lease_expiry_after_deadline_rejected(session: AsyncSession) -> None:
    await make_world(session)
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        await make_task(
            session,
            lease_owner="worker-1",
            lease_expires_at=now + timedelta(seconds=120),
            lease_deadline_at=now + timedelta(seconds=60),
        )


async def test_negative_execution_generation_rejected(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(IntegrityError):
        await make_task(session, execution_generation=-1)


async def test_null_reservation_generation_allowed(session: AsyncSession) -> None:
    await make_world(session)
    reservation = BudgetReservation(
        id="res_null",
        business_id=BIZ,
        project_id="proj_1",
        attempt_key="attempt:null",
        amount=Decimal("1.00"),
        idempotency_key="reserve:null:1",
    )
    session.add(reservation)
    await session.flush()

    assert reservation.execution_generation is None


# ---------------------------------------------------------------------------
# Repository primitives
# ---------------------------------------------------------------------------


async def test_claim_owned_grants_full_lease_and_generation(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    ownership = await REPO.claim_owned(
        session,
        BIZ,
        "t_1",
        from_statuses=("not_started",),
        to_status="working",
        lease_owner="worker-1",
        lease_ttl_seconds=60,
        lease_wall_seconds=120,
    )

    assert ownership is not None
    assert ownership.business_id == BIZ
    assert ownership.execution_generation == 1
    assert ownership.lease_owner == "worker-1"
    assert ownership.lease_expires_at is not None
    assert ownership.lease_deadline_at is not None
    assert ownership.lease_expires_at <= ownership.lease_deadline_at

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "working"
    assert task.execution_generation == 1
    assert task.lease_owner == "worker-1"
    assert task.lease_expires_at is not None
    assert task.lease_deadline_at is not None


async def test_claim_owned_is_tenant_scoped(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    assert (
        await REPO.claim_owned(
            session,
            "biz_other",
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-1",
            lease_ttl_seconds=60,
            lease_wall_seconds=120,
        )
        is None
    )

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.execution_generation == 0
    assert task.lease_owner is None
    assert task.status == "not_started"


async def test_generation_is_scoped_per_task(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, "t_a", status="not_started")
    await make_task(session, "t_b", status="not_started")

    a1 = await REPO.claim_owned(
        session,
        BIZ,
        "t_a",
        from_statuses=("not_started",),
        to_status="working",
        lease_owner="worker-a",
        lease_ttl_seconds=60,
        lease_wall_seconds=120,
    )
    b1 = await REPO.claim_owned(
        session,
        BIZ,
        "t_b",
        from_statuses=("not_started",),
        to_status="working",
        lease_owner="worker-b",
        lease_ttl_seconds=60,
        lease_wall_seconds=120,
    )
    a2 = await REPO.claim_owned(
        session,
        BIZ,
        "t_a",
        from_statuses=("working",),
        to_status="waiting_dependency",
        lease_owner="worker-a2",
        lease_ttl_seconds=30,
        lease_wall_seconds=90,
    )

    assert a1 is not None and a2 is not None and b1 is not None
    assert (a1.execution_generation, b1.execution_generation, a2.execution_generation) == (
        1,
        1,
        2,
    )

    task_b = await tasks_repo.get(session, BIZ, "t_b")
    assert task_b is not None
    await session.refresh(task_b)
    assert task_b.execution_generation == 1


async def test_claim_owned_rejects_ttl_beyond_wall(session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await REPO.claim_owned(
            session,
            BIZ,
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-1",
            lease_ttl_seconds=120,
            lease_wall_seconds=60,
        )


async def test_claim_owned_rejects_same_status_transition(session: AsyncSession) -> None:
    """`working -> working` would displace a live lease; claim_owned forbids it."""
    await make_world(session)
    await make_task(session, status="working", **_lease(lease_owner="owner-live"))

    with pytest.raises(ValueError):
        await REPO.claim_owned(
            session,
            BIZ,
            "t_1",
            from_statuses=("working",),
            to_status="working",
            lease_owner="owner-thief",
            lease_ttl_seconds=60,
            lease_wall_seconds=120,
        )

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "working"
    assert task.execution_generation == 0
    assert task.lease_owner == "owner-live"


@pytest.mark.parametrize(
    ("from_statuses", "to_status"),
    [
        ((), "working"),
        (("",), "working"),
        (("not_started",), ""),
        (("bogus",), "working"),
        (("not_started",), "bogus"),
        (("not_started",), "not_started"),
    ],
)
async def test_claim_owned_rejects_invalid_status_sets(
    session: AsyncSession,
    from_statuses: tuple[str, ...],
    to_status: str,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    with pytest.raises(ValueError):
        await REPO.claim_owned(
            session,
            BIZ,
            "t_1",
            from_statuses=from_statuses,
            to_status=to_status,
            lease_owner="worker-1",
            lease_ttl_seconds=60,
            lease_wall_seconds=120,
        )

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "not_started"
    assert task.execution_generation == 0
    assert task.lease_owner is None


async def test_failed_claim_does_not_disturb_live_lease(session: AsyncSession) -> None:
    """A stale predicate cannot overwrite an existing owner or its generation."""
    await make_world(session)
    await make_task(session, status="working", **_lease(lease_owner="owner-live"))

    ownership = await REPO.claim_owned(
        session,
        BIZ,
        "t_1",
        from_statuses=("not_started",),
        to_status="working",
        lease_owner="owner-late",
        lease_ttl_seconds=60,
        lease_wall_seconds=120,
    )

    assert ownership is None
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "working"
    assert task.execution_generation == 0
    assert task.lease_owner == "owner-live"


async def test_claim_owned_returns_persisted_owner(session: AsyncSession) -> None:
    """The returned owner comes from RETURNING, not merely the input argument."""
    await make_world(session)
    await make_task(session, status="not_started")

    ownership = await REPO.claim_owned(
        session,
        BIZ,
        "t_1",
        from_statuses=("not_started",),
        to_status="working",
        lease_owner="owner-persisted",
        lease_ttl_seconds=60,
        lease_wall_seconds=120,
    )

    assert ownership is not None
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.lease_owner == "owner-persisted"
    assert ownership.lease_owner == task.lease_owner


@pytest.mark.parametrize(
    ("ttl", "wall"),
    [
        (math.nan, 120.0),
        (math.inf, 120.0),
        (-math.inf, 120.0),
        (60.0, math.nan),
        (60.0, math.inf),
        (60.0, -math.inf),
    ],
)
async def test_claim_owned_rejects_non_finite_durations(
    session: AsyncSession,
    ttl: float,
    wall: float,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    with pytest.raises(ValueError):
        await REPO.claim_owned(
            session,
            BIZ,
            "t_1",
            from_statuses=("not_started",),
            to_status="working",
            lease_owner="worker-1",
            lease_ttl_seconds=ttl,
            lease_wall_seconds=wall,
        )

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "not_started"
    assert task.execution_generation == 0


async def test_concurrent_claims_have_exactly_one_winner(
    session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    """Two independent PostgreSQL sessions race one `not_started -> working`."""
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()

    factory = session_factory(test_engine)
    barrier = asyncio.Barrier(2)

    async def claim(owner: str) -> TaskOwnership | None:
        async with factory() as claim_session:
            await barrier.wait()
            ownership = await REPO.claim_owned(
                claim_session,
                BIZ,
                "t_1",
                from_statuses=("not_started",),
                to_status="working",
                lease_owner=owner,
                lease_ttl_seconds=60,
                lease_wall_seconds=120,
            )
            await claim_session.commit()
            return ownership

    first, second = await asyncio.wait_for(
        asyncio.gather(claim("worker-1"), claim("worker-2")),
        timeout=15,
    )

    winners = [ownership for ownership in (first, second) if ownership is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.execution_generation == 1
    assert winner.lease_owner in {"worker-1", "worker-2"}

    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "working"
    assert task.execution_generation == 1
    assert task.lease_owner == winner.lease_owner


async def test_legacy_claim_without_lease_remains_valid(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")

    claimed = await REPO.claim_task(
        session, BIZ, "t_1", from_statuses=("not_started",), to_status="working"
    )

    assert claimed is True
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None
    await session.refresh(task)
    assert task.status == "working"
    assert task.execution_generation == 0
    assert task.lease_owner is None
    assert task.lease_expires_at is None
    assert task.lease_deadline_at is None


async def test_reservation_generation_roundtrip_and_scoping(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()

    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt_key="attempt:1",
        amount=Decimal("1.00"),
        execution_generation=3,
    )

    assert hold is not None
    assert hold.execution_generation == 3
    found = await repo.get_for_task(session, BIZ, "t_1", execution_generation=3)
    assert found is not None and found.id == hold.id
    assert await repo.get_for_task(session, BIZ, "t_1", execution_generation=4) is None
    assert await repo.get_for_task(session, "biz_other", "t_1", execution_generation=3) is None


async def test_get_for_task_orders_across_attempt_keys_deterministically(
    session: AsyncSession,
) -> None:
    """`run_seq` ties across attempt keys; created_at DESC + id DESC must decide."""
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()

    newest = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    older = newest - timedelta(minutes=5)
    for reservation_id, attempt_key, created_at, run_seq in (
        ("res_a", "attempt:a", newest, 1),
        ("res_b", "attempt:b", newest, 1),
        ("res_c", "attempt:c", older, 9),
    ):
        session.add(
            BudgetReservation(
                id=reservation_id,
                business_id=BIZ,
                project_id="proj_1",
                task_id="t_1",
                attempt_key=attempt_key,
                run_seq=run_seq,
                amount=Decimal("1.00"),
                execution_generation=4,
                idempotency_key=f"reserve:{attempt_key}:{run_seq}",
                created_at=created_at,
            )
        )
    await session.flush()

    first = await repo.get_for_task(session, BIZ, "t_1", execution_generation=4)
    second = await repo.get_for_task(session, BIZ, "t_1", execution_generation=4)

    assert first is not None and second is not None
    # Same created_at: id DESC is the deterministic tie-breaker.
    assert first.id == "res_b"
    # An older attempt's higher run_seq must not win.
    assert first.id != "res_c"
    assert second.id == first.id


async def test_null_legacy_reservation_generation_remains_supported(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()

    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt_key="attempt:legacy",
        amount=Decimal("1.00"),
    )

    assert hold is not None
    assert hold.execution_generation is None
    # NULL is not equal to 0: a generation filter must not match legacy rows.
    assert await repo.get_for_task(session, BIZ, "t_1", execution_generation=0) is None
    unfiltered = await repo.get_for_task(session, BIZ, "t_1")
    assert unfiltered is not None and unfiltered.id == hold.id


async def test_wrong_business_cannot_mutate_reservation(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = BudgetReservationRepository()

    hold = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt_key="attempt:1",
        amount=Decimal("1.00"),
        execution_generation=1,
    )
    assert hold is not None

    applied = await repo.set_status(
        session,
        "biz_other",
        hold.id,
        from_statuses=("active",),
        to_status="ambiguous",
    )

    assert applied is False
    unchanged = await repo.get_for_task(session, BIZ, "t_1")
    assert unchanged is not None and unchanged.status == "active"
