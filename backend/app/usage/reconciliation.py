"""Reservation reconciliation (Stage 16).

Recovery resets a stale `working` task, but a crashed run's budget hold must not
simply disappear: the provider may already have billed it. Recovery therefore:

1. converts any `active` hold for the task to `ambiguous` (fail-closed, always);
2. optionally releases `ambiguous` holds older than the operator-configured age
   (`usage_ambiguous_release_minutes`; 0 == never, the default).

Both steps run in the caller's transaction, next to the stale-row reset and its
audit event, so recovery stays atomic.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.usage import BudgetReservationRepository

_reservations = BudgetReservationRepository()


async def reconcile_task_reservations(
    session: AsyncSession,
    business_id: str,
    task_id: str,
    *,
    ambiguous_release_minutes: int = 0,
) -> tuple[int, int]:
    """Return `(held_ambiguous, aged_released)` counts for the task."""
    held = await _reservations.mark_task_active_ambiguous(
        session, business_id, task_id, reason="recovery_stale"
    )
    released = await _reservations.release_aged_ambiguous(
        session,
        business_id,
        older_than_minutes=ambiguous_release_minutes,
        task_id=task_id,
    )
    return held, released


async def reconcile_project_reservations(
    session: AsyncSession,
    business_id: str,
    project_id: str,
    *,
    ambiguous_release_minutes: int = 0,
) -> tuple[int, int]:
    """Return `(held_ambiguous, aged_released)` counts for the project.

    Team Lead calls are project-scoped without a task (Stage 16.1), so a crashed
    finalization leaves an `active` hold that task recovery never sees. This
    converts those holds to `ambiguous` (fail-closed) and optionally ages out
    older `ambiguous` holds — the same discipline as task reconciliation. A
    retried call supersedes its own attempt_key hold via `reserve`.
    """
    held = await _reservations.mark_active_ambiguous(
        session, business_id, reason="recovery_stale", project_id=project_id
    )
    released = await _reservations.release_aged_ambiguous(
        session,
        business_id,
        older_than_minutes=ambiguous_release_minutes,
        project_id=project_id,
    )
    return held, released
