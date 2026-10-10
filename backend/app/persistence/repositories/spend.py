"""Project spend recalculation — the ONLY legal aiSpent mutation (contract §5.1).

Rules enforced:
- aiSpent is recomputed as SUM(cost_accrued) over the (business_id,
  project_id) scope — never incremented, so re-running on the same state
  yields the same value (idempotent, no double-count by construction).
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

CENT = Decimal("0.01")


def quantize_spend(value: Decimal | float | str | None) -> Decimal:
    amount = Decimal(str(value)) if value is not None else Decimal("0")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


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
    total = (await session.execute(total_stmt)).scalar_one()
    project.ai_spent = quantize_spend(total)
    await session.flush()
    return project.ai_spent
