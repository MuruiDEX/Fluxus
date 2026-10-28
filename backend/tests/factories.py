"""Shared model factories for persistence tests (test data only)."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import (
    AgentRepository,
    ApprovalRepository,
    BrainRepository,
    BusinessRepository,
    MarketplaceRepository,
    ProjectRepository,
    TaskRepository,
)

BIZ = "biz_test"
BIZ_OTHER = "biz_other"

agents = AgentRepository()
projects = ProjectRepository()
tasks = TaskRepository()
approvals = ApprovalRepository()
brain = BrainRepository()
businesses = BusinessRepository()
marketplace = MarketplaceRepository()


async def make_business(session: AsyncSession, biz_id: str = BIZ, **over: object) -> object:
    data: dict = {"id": biz_id, "name": "Test Business"}
    data.update(over)
    return await businesses.create(session, biz_id, data)


async def make_agent(
    session: AsyncSession, agent_id: str = "ag_1", biz_id: str = BIZ, **over: object
):
    data: dict = {"id": agent_id, "name": "A", "role": "Tester", "status": "available"}
    data.update(over)
    return await agents.create(session, biz_id, data)


async def make_project(
    session: AsyncSession, project_id: str = "proj_1", biz_id: str = BIZ, **over: object
):
    data: dict = {"id": project_id, "title": "P", "status": "in_progress"}
    data.update(over)
    return await projects.create(session, biz_id, data)


async def make_task(
    session: AsyncSession,
    task_id: str = "t_1",
    biz_id: str = BIZ,
    project_id: str = "proj_1",
    owner_id: str = "ag_1",
    **over: object,
):
    data: dict = {
        "id": task_id,
        "project_id": project_id,
        "title": "T",
        "owner_agent_id": owner_id,
        "status": "working",
    }
    data.update(over)
    return await tasks.create(session, biz_id, data)


async def make_approval(
    session: AsyncSession, approval_id: str = "ap_1", biz_id: str = BIZ, **over: object
):
    data: dict = {
        "id": approval_id,
        "project_id": "proj_1",
        "title": "Approve?",
        "requested_by_agent_id": "ag_1",
        "status": "pending",
    }
    data.update(over)
    return await approvals.create(session, biz_id, data)


async def make_world(
    session: AsyncSession,
    biz_id: str = BIZ,
    *,
    agent_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Minimal coherent graph: business + agent + project.

    The default IDs are canonical for the primary test business. IDs are
    global primary keys, so a second world must not reuse them.
    """
    agent_id = agent_id or ("ag_1" if biz_id == BIZ else f"ag_{biz_id}")
    project_id = project_id or ("proj_1" if biz_id == BIZ else f"proj_{biz_id}")
    await make_business(session, biz_id)
    await make_agent(session, agent_id, biz_id)
    await make_project(session, project_id, biz_id)


async def spend_of_project(session: AsyncSession, biz_id: str, project_id: str) -> Decimal:

    proj = await projects.get(session, biz_id, project_id)
    assert proj is not None
    return proj.ai_spent
