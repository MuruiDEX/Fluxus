"""Project-run persistence and the shared execution admission boundary."""

from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.brief import Brief
from app.persistence.models.project_run import ProjectRun, RunState
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation


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
