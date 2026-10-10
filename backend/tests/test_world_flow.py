"""End-to-end repository flow: full world through public repo APIs in one transaction.

Locks the supported write pattern: repositories sequence their writes
(create-then-flush per aggregate, dependencies validated before write).
A single flush of FK-related rows across tables is NOT used anywhere in
this codebase and is covered only by this negative documentation — the
positive path below is what Stage-4 orchestration must use.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import ActivityRepository
from app.persistence.repositories.spend import recalculate_project_spend
from tests.factories import (
    BIZ,
    approvals,
    brain,
    make_agent,
    make_approval,
    make_business,
    make_project,
    make_task,
    projects,
    tasks,
)

activity = ActivityRepository()


async def test_full_world_single_transaction(session: AsyncSession) -> None:
    await make_business(session, BIZ, name="Northstar")
    await make_agent(session, "ag_lead", name="Atlas", role="Team Lead", status="working")
    await make_agent(session, "ag_mkt", name="Mira", role="Analyst", status="working")
    await make_project(session, "proj_1", title="Launch", team_ids=["ag_lead", "ag_mkt"])
    await make_task(
        session,
        task_id="t_a",
        owner_id="ag_mkt",
        status="completed",
        progress=100,
        cost_accrued=Decimal("1.80"),
    )
    await make_task(
        session,
        task_id="t_b",
        owner_id="ag_lead",
        status="working",
        progress=10,
        cost_accrued=Decimal("0.40"),
        depends_on=["t_a"],
    )
    ap = await make_approval(session, approval_id="ap_1", requested_by_agent_id="ag_lead")
    await approvals.update(session, BIZ, ap.id, {"gates_task_id": "t_b"})
    await brain.create(
        session, BIZ, {"id": "b_1", "category": "results", "title": "R", "body": "..."}
    )
    await activity.append(
        session,
        BIZ,
        {"agent_name": "Atlas", "text": "done", "kind": "result", "project_id": "proj_1"},
    )
    total = await recalculate_project_spend(session, BIZ, "proj_1")

    assert total == Decimal("2.20")
    assert (await projects.get(session, BIZ, "proj_1")).ai_spent == Decimal("2.20")
    assert (await tasks.get(session, BIZ, "t_b")) is not None
    assert (await approvals.get(session, BIZ, "ap_1")).gates_task_id == "t_b"
    assert len(await activity.list(session, BIZ, project_id="proj_1")) == 1


async def test_gate_roundtrip_task_approval(session: AsyncSession) -> None:
    """Task waits on approval; approval targets the task — both directions persist."""
    from tests.factories import make_world

    await make_world(session)
    await make_task(session, task_id="t_g", approval_gate_id=None)
    ap = await make_approval(session, approval_id="ap_g", gates_task_id="t_g")
    await tasks.update(session, BIZ, "t_g", {"approval_gate_id": ap.id})
    reread = await tasks.get(session, BIZ, "t_g")
    assert reread is not None and reread.approval_gate_id == "ap_g"
