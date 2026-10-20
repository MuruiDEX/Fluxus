"""Project spend recalculation — the ONLY legal aiSpent mutation (contract §5.1).

Rules enforced:
- aiSpent is recomputed as SUM(Task.cost_accrued) PLUS confirmed task-less
  project-scoped usage over the (business_id, project_id) scope — i.e. all
  confirmed project-scoped usage exactly once. Never incremented, so re-running
  on the same state yields the same value (idempotent, no double-count by
  construction; the task-linked and task-less terms are disjoint).
- The project row is locked (SELECT … FOR UPDATE) inside the caller's
  transaction, so concurrent writers serialize on the project, not on tasks.
- Decimal / ROUND_HALF_UP / cent quantum — matches the frontend engine.
- Stage-4 orchestration MUST call this after writing task costs and MUST NOT
  reimplement the aggregation.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.project import Project
from app.persistence.models.task import Task
from app.persistence.models.usage import UsageRecord

CENT = Decimal("0.01")


def quantize_spend(value: Decimal | float | str | None) -> Decimal:
    amount = Decimal(str(value)) if value is not None else Decimal("0")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


async def recalculate_task_cost(
    session: AsyncSession, business_id: str, task_id: str
) -> Decimal:
    """Recompute `Task.cost_accrued` = SUM(confirmed ledger cost) for the task.

    Ledger-derived by construction (never incremented), so replaying the same
    settled state yields the same value — no double count. `estimated` and
    `unknown` rows are deliberately excluded: they are tracked separately and
    never presented as confirmed cost.
    """
    from app.persistence.enums import UsageStatus

    total_stmt = select(func.coalesce(func.sum(UsageRecord.cost_amount), 0)).where(
        UsageRecord.business_id == business_id,
        UsageRecord.task_id == task_id,
        UsageRecord.usage_status == UsageStatus.CONFIRMED.value,
    )
    total = (await session.execute(total_stmt)).scalar_one()
    task_stmt = select(Task).where(Task.business_id == business_id, Task.id == task_id)
    task = (await session.execute(task_stmt)).scalar_one_or_none()
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    task.cost_accrued = quantize_spend(total)
    await session.flush()
    return task.cost_accrued


async def recalculate_project_spend(
    session: AsyncSession, business_id: str, project_id: str
) -> Decimal:
    # Lock the project row first: concurrent recalculations serialize here.
    project_stmt = (
        select(Project)
        .where(Project.business_id == business_id, Project.id == project_id)
        .with_for_update()
    )
    project = (await session.execute(project_stmt)).scalar_one_or_none()
    if project is None:
        raise ValueError(f"unknown project: {project_id}")

    total_stmt = select(func.coalesce(func.sum(Task.cost_accrued), 0)).where(
        Task.business_id == business_id, Task.project_id == project_id
    )
    task_linked = (await session.execute(total_stmt)).scalar_one()
    # Stage 16.1: confirmed project-scoped usage WITHOUT a task (e.g. Team Lead
    # finalization) is not represented by any Task row. It is disjoint from
    # task-linked cost by construction (`task_id IS NULL`), so it can be added
    # without double-counting: aiSpent = confirmed project-scoped usage exactly
    # once. Estimated/unknown rows are excluded — never presented as spend.
    taskless_stmt = select(func.coalesce(func.sum(UsageRecord.cost_amount), 0)).where(
        UsageRecord.business_id == business_id,
        UsageRecord.project_id == project_id,
        UsageRecord.task_id.is_(None),
        UsageRecord.usage_status == "confirmed",
    )
    taskless = (await session.execute(taskless_stmt)).scalar_one()
    project.ai_spent = quantize_spend(Decimal(str(task_linked)) + Decimal(str(taskless)))
    await session.flush()
    return project.ai_spent
