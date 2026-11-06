"""Reservation reconciliation (Stage 16, Stage 18 S2.2.4.6).

Recovery resets a stale `working` task, but a crashed run's budget hold must not
simply disappear: the provider may already have billed it. Recovery therefore
converts any `active` hold for the task to `ambiguous` (fail-closed, always).

Ambiguous holds represent potentially billable unknown outcomes. They are never
released because of age or any timer: they keep counting against the budget
until an explicitly authorized reconciliation resolves them. The conversion runs
in the caller's transaction, next to the stale-row reset and its audit event, so
recovery stays atomic.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.usage import BudgetReservationRepository

_reservations = BudgetReservationRepository()


async def reconcile_task_reservations(
    session: AsyncSession,
    business_id: str,
    task_id: str,
    *,
    before_generation: int | None = None,
) -> int:
    """Return the number of `active` holds converted to `ambiguous`.

    `before_generation` is the lease-takeover generation (Stage 18 S2.2.4.5).
    When supplied, only holds whose generation is NULL (legacy/unknown) or
    strictly older are reconciled; the new/future generation's holds are left
    untouched. Legacy recovery without a new generation omits it and keeps the
    pre-existing scope behavior.

    Already-`ambiguous` holds are never released, regardless of age.
    """
    return await _reservations.mark_task_active_ambiguous(
        session,
        business_id,
        task_id,
        reason="recovery_stale",
        before_execution_generation=before_generation,
    )


async def reconcile_project_reservations(
    session: AsyncSession,
    business_id: str,
    project_id: str,
) -> int:
    """Return the number of `active` holds converted to `ambiguous`.

    Team Lead calls are project-scoped without a task (Stage 16.1), so a crashed
    finalization leaves an `active` hold that task recovery never sees. This
    converts those holds to `ambiguous` (fail-closed) with the same discipline
    as task reconciliation. A retried call supersedes its own attempt_key hold
    via `reserve`. Already-`ambiguous` holds are never released by age.
    """
    return await _reservations.mark_active_ambiguous(
        session, business_id, reason="recovery_stale", project_id=project_id
    )
