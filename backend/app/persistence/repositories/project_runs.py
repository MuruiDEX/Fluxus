"""Project-run persistence and the shared execution admission boundary."""

from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.persistence.models.brief import Brief
from app.persistence.models.integration import IntegrationAction
from app.persistence.models.project_run import ProjectRun, RunState
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation, UsageRecord

log = get_logger(__name__)


async def lock_project_admission(session: AsyncSession, business_id: str, project_id: str) -> None:
    """Lock even when the run row does not yet exist.

    Order: admission lock, then membership/task rows as applicable, then the
    Slice 1 business and project budget locks. Settlement does not acquire the
    admission lock. Release it at commit before any provider call.
    """
    if session.get_bind().dialect.name == "postgresql":
        # Length-prefix the business ID because IDs can themselves contain colons.
        scope = f"fluxus:project-admission:{len(business_id)}:{business_id}:{project_id}".encode()
        key = int.from_bytes(sha256(scope).digest()[:8], "big", signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(key)))


async def get_project_run(
    session: AsyncSession, business_id: str, project_id: str
) -> ProjectRun | None:
    return await session.scalar(
        select(ProjectRun)
        .where(ProjectRun.business_id == business_id, ProjectRun.project_id == project_id)
        .execution_options(populate_existing=True)
    )


async def has_working_task(session: AsyncSession, business_id: str, project_id: str) -> bool:
    task = select(Task.id).where(
        Task.business_id == business_id, Task.project_id == project_id, Task.status == "working"
    )
    return bool(await session.scalar(select(task.exists())))


async def unresolved_task_hold(
    session: AsyncSession, business_id: str, project_id: str, *, task_id: str | None = None
) -> str | None:
    stmt = select(BudgetReservation.task_id).where(
        BudgetReservation.business_id == business_id,
        BudgetReservation.project_id == project_id,
        BudgetReservation.task_id.is_not(None),
        BudgetReservation.status.in_(("active", "ambiguous")),
    )
    if task_id is not None:
        stmt = stmt.where(BudgetReservation.task_id == task_id)
    return await session.scalar(stmt.order_by(BudgetReservation.task_id).limit(1))


async def retry_blocker(
    session: AsyncSession, run: ProjectRun, task: Task, max_attempts: int
) -> str | None:
    """Check while holding the task row lock, before (re)authorizing a retry."""
    # The executor's ownership fence requires status=working. Settlement and
    # recovery retain the historical lease tuple on the non-working row.
    if task.status not in ("failed", "not_started"):
        return "retry_ownership_unresolved"
    action = select(IntegrationAction.id).where(
        IntegrationAction.business_id == run.business_id,
        IntegrationAction.task_id == task.id,
    )
    if await session.scalar(select(action.exists())):
        return "retry_external_action"
    rows = (
        await session.execute(
            select(
                BudgetReservation.execution_generation,
                BudgetReservation.status,
                UsageRecord.usage_status,
            )
            .outerjoin(UsageRecord, BudgetReservation.usage_record_id == UsageRecord.id)
            .where(
                BudgetReservation.business_id == run.business_id,
                BudgetReservation.project_id == run.project_id,
                BudgetReservation.task_id == task.id,
            )
        )
    ).all()
    if max(task.attempts, len(rows)) >= max_attempts:
        return "retry_attempts_exhausted"
    expected = run.active_task_generation if run.active_task_id else task.execution_generation
    if run.active_task_id is None and (
        run.reason_code == "retry_required" or run.retry_task_id == task.id
    ):
        # Recovery can advance ownership without admitting another attempt.
        previous = [row[0] for row in rows if row[0] is not None]
        expected = max(previous, default=expected)
        if expected not in (task.execution_generation, task.execution_generation - 1):
            return "retry_evidence_missing"
    if not any(generation == expected for generation, _, _ in rows):
        return "retry_evidence_missing"
    for _, status, usage in rows:
        if status not in ("released", "reconciled", "consumed"):
            return "retry_reservation_unresolved"
        if status == "consumed" and usage != "confirmed":
            return "retry_reservation_unresolved"
    return None


async def wake_project_run(
    factory: async_sessionmaker[AsyncSession],
    business_id: str,
    project_id: str,
    *,
    observe_active: bool = False,
) -> None:
    """Wake from a committed decision, outside the approval/action lock order."""
    try:
        async with factory() as session, session.begin():
            await lock_project_admission(session, business_id, project_id)
            run = await get_project_run(session, business_id, project_id)
            if run is not None and (
                run.state in (RunState.RUNNING, RunState.WAITING_APPROVAL)
                or (observe_active and run.active_task_id is not None)
            ):
                run.next_check_at = await session.scalar(select(func.clock_timestamp()))
    except SQLAlchemyError:
        # The decision has already committed. Polling remains the fallback.
        log.warning("project run wakeup failed", extra={"context": {"project_id": project_id}})


async def manual_admission_blocker(
    session: AsyncSession, business_id: str, project_id: str
) -> str | None:
    """Caller holds the project admission lock through the task claim."""
    if await get_project_run(session, business_id, project_id) is not None:
        return "run_managed"
    finalized = select(Brief.id).where(
        Brief.business_id == business_id,
        Brief.project_id == project_id,
        Brief.status == "completed",
    )
    if await session.scalar(select(finalized.exists())):
        return "project_finalized"
    return None


async def plan_edit_blocker(session: AsyncSession, run: ProjectRun) -> bool:
    return (
        run.state != RunState.PAUSED
        or run.active_task_id is not None
        or await has_working_task(session, run.business_id, run.project_id)
    )


def finalization_blocked(run: ProjectRun | None) -> bool:
    return run is not None and (
        run.state in (RunState.RUNNING, RunState.WAITING_APPROVAL) or run.active_task_id is not None
    )
