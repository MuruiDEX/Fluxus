"""Expired-lease recovery takeover (Stage 18 S2.2.4.4).

The existing recovery entry point is the explicit
`POST .../tasks/{task_id}/recover` endpoint; there is no background recovery
loop in this codebase. This module supplies the eligibility decision and the
takeover sequence used by that endpoint:

1. load the task in scope (read only);
2. classify its ownership fields;
3. lease-bearing rows: atomically take over only when the lease is complete,
   expired by DATABASE time, and the absolute wall has not passed;
4. immediately fence the freshly acquired generation back to the existing
   claimable state (`not_started` when no attempt was recorded yet, otherwise
   `failed` so the attempt budget keeps applying);
5. legacy rows with no lease keep using the pre-existing `updated_at` clock.

Takeover itself never touches reservations, ledger rows or spend. Reservation
reconciliation is the caller's recovery policy: S2.2.4.5 extends it so a lease
takeover marks only NULL/older-generation active holds `ambiguous`, never the
new generation's holds, and never releases or ledger-writes anything.

Recovery deliberately does NOT run provider work. The existing lifecycle is
conservative: a crashed attempt may already have been billed, so recovery only
makes the task safely claimable again. A later normal claim/execute starts the
next attempt under its own fresh generation and lease.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.persistence.repositories.concrete import TaskOwnership, TaskRepository

log = get_logger(__name__)

_tasks = TaskRepository()

LEASE_NONE = "none"
LEASE_INCOMPLETE = "incomplete"
LEASE_COMPLETE = "complete"

RECOVERED = "recovered"
NOT_STALE = "not_stale"
NOT_FOUND = "not_found"

MODE_LEASE_TAKEOVER = "lease_takeover"
MODE_LEGACY_STALE = "legacy_stale"


@dataclass(frozen=True)
class RecoveryOutcome:
    """Result of one recovery attempt. Refusals are write-free."""

    outcome: str
    status: str | None = None
    generation: int | None = None
    mode: str | None = None
    reason: str | None = None


def lease_state(task: object) -> str:
    """Classify the three lease columns without repairing anything.

    `complete` means all three are non-NULL (the schema's legal owned state).
    `incomplete` is illegal under `ck_tasks_lease_consistency` and therefore
    only reachable through schema corruption/manual bypass: fail closed.
    `none` is the legacy/pre-lease idle state.
    """
    fields = (
        getattr(task, "lease_owner", None),
        getattr(task, "lease_expires_at", None),
        getattr(task, "lease_deadline_at", None),
    )
    present = [value is not None for value in fields]
    if all(present):
        return LEASE_COMPLETE
    if any(present):
        return LEASE_INCOMPLETE
    return LEASE_NONE


def _reset_target(attempts: int) -> str:
    """Existing recovery policy: no attempt recorded -> not_started, else failed."""
    return "not_started" if attempts == 0 else "failed"


async def recover_task(
    session: AsyncSession,
    business_id: str,
    project_id: str,
    task_id: str,
    *,
    recovery_owner: str,
    lease_ttl_seconds: float,
    lease_wall_seconds: float,
    stale_seconds: float,
) -> RecoveryOutcome:
    """Run one recovery decision inside the caller's transaction.

    The caller owns the transaction and must append the audit event and apply
    the pre-existing reservation reconciliation only when the outcome is
    `recovered`. Refusals write nothing at all.
    """
    task = await _tasks.get(session, business_id, task_id)
    if task is None or task.project_id != project_id:
        return RecoveryOutcome(outcome=NOT_FOUND)

    if task.status != "working":
        return RecoveryOutcome(outcome=NOT_STALE, status=task.status)

    state = lease_state(task)
    if state == LEASE_INCOMPLETE:
        log.warning(
            "recovery refused: inconsistent lease fields",
            extra={"context": {"task_id": task_id}},
        )
        return RecoveryOutcome(
            outcome=NOT_STALE,
            status=task.status,
            reason="inconsistent_lease",
        )

    if state == LEASE_NONE:
        # Legacy pre-lease rows keep their existing database-time clock.
        previous_generation = task.execution_generation
        reset_to = await _tasks.recover_stale(
            session, business_id, task_id, stale_seconds=stale_seconds
        )
        if reset_to is None:
            return RecoveryOutcome(outcome=NOT_STALE, status="working", reason="not_legacy_stale")
        return RecoveryOutcome(
            outcome=RECOVERED,
            status=reset_to,
            generation=previous_generation,
            mode=MODE_LEGACY_STALE,
        )

    # Lease-bearing rows: DATABASE time is authoritative for both the expiry
    # and the absolute deadline. Local clocks never decide eligibility.
    db_now: datetime = (await session.execute(select(func.now()))).scalar_one()
    if task.lease_deadline_at is not None and task.lease_deadline_at <= db_now:
        # No terminal timeout policy exists in the current lifecycle; taking
        # over a run past its wall would silently grant unlimited retries.
        # Fail closed and report instead of inventing policy.
        log.warning(
            "recovery refused: absolute lease deadline passed",
            extra={"context": {"task_id": task_id}},
        )
        return RecoveryOutcome(
            outcome=NOT_STALE,
            status=task.status,
            reason="deadline_exceeded",
        )
    if task.lease_expires_at is not None and task.lease_expires_at > db_now:
        return RecoveryOutcome(outcome=NOT_STALE, status=task.status, reason="lease_active")

    assert task.lease_owner is not None  # guaranteed by LEASE_COMPLETE
    ownership: TaskOwnership | None = await _tasks.takeover_expired(
        session,
        business_id,
        task_id,
        expected_execution_generation=task.execution_generation,
        expected_lease_owner=task.lease_owner,
        lease_owner=recovery_owner,
        lease_ttl_seconds=lease_ttl_seconds,
        lease_wall_seconds=lease_wall_seconds,
    )
    if ownership is None:
        # Another worker won, the tuple moved, or the lease/deadline changed
        # between classification and the CAS. The CAS is authoritative.
        return RecoveryOutcome(outcome=NOT_STALE, status="working", reason="takeover_raced")

    # Use the newly persisted immutable tuple to prove the takeover before
    # returning the row to the existing claimable lifecycle.
    reset_to = _reset_target(task.attempts)
    fenced = await _tasks.fence_owned(
        session,
        business_id,
        task_id,
        execution_generation=ownership.execution_generation,
        lease_owner=ownership.lease_owner,
        values={"status": reset_to},
    )
    if not fenced:  # pragma: no cover - the takeover holds the row lock
        raise RuntimeError("takeover succeeded but the recovery fence lost ownership")

    return RecoveryOutcome(
        outcome=RECOVERED,
        status=reset_to,
        generation=ownership.execution_generation,
        mode=MODE_LEASE_TAKEOVER,
    )
