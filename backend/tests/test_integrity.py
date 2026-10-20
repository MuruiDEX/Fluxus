"""Integrity: dangling references, scope isolation, CHECKs, append-only log."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import DanglingReferenceError
from app.persistence.models.task import Task
from app.persistence.repositories.base import clamp_progress
from app.persistence.repositories.concrete import ActivityRepository
from tests.factories import (
    BIZ,
    BIZ_OTHER,
    make_agent,
    make_business,
    make_project,
    make_task,
    make_world,
    projects,
    tasks,
)

activity = ActivityRepository()


async def test_task_rejects_unknown_project_agent_gate(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_x", project_id="proj_missing")
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_x", owner_id="ag_missing")
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_x", approval_gate_id="ap_missing")


async def test_dependency_edges_validated_and_self_ref_rejected(session: AsyncSession) -> None:
    await make_world(session)
    a = await make_task(session, task_id="t_a")
    b = await make_task(session, task_id="t_b", depends_on=["t_a"])
    assert b.id == "t_b"
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_c", depends_on=["t_ghost"])
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_self", depends_on=["t_self"])
    assert a.id == "t_a"


async def test_cross_business_references_rejected(session: AsyncSession) -> None:
    # NOTE: ids are global primary keys (uuid-style in production), so the
    # "other business" world uses distinct ids — same-shape ids would collide
    # at the PK level before scope checks even run.
    await make_world(session, BIZ)
    await make_business(session, BIZ_OTHER)
    await make_agent(session, "ag_o", BIZ_OTHER)
    await make_project(session, "proj_o", BIZ_OTHER)
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_x", project_id="proj_missing")
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_x", owner_id="ag_missing")
    # Same-shape but foreign rows: BIZ task cannot point at BIZ_OTHER rows.
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_y", biz_id=BIZ, project_id="proj_o")
    with pytest.raises(DanglingReferenceError):
        await make_task(session, task_id="t_z", biz_id=BIZ, owner_id="ag_o")


async def test_scope_isolation_reads(session: AsyncSession) -> None:
    from tests.factories import make_agent, make_business

    await make_world(session, BIZ)
    await make_business(session, BIZ_OTHER)
    await make_agent(session, "ag_o", BIZ_OTHER)
    await make_project(session, "proj_o", BIZ_OTHER)
    assert await projects.get(session, BIZ, "proj_1") is not None
    assert await projects.get(session, BIZ, "nope") is None
    # Same-shape foreign rows are invisible here: scoped get returns None,
    # and deleting "proj_o" from BIZ scope touches nothing.
    assert await projects.get(session, BIZ, "proj_o") is None
    assert await projects.delete(session, BIZ, "proj_o") is False
    assert await projects.get(session, BIZ_OTHER, "proj_o") is not None
    assert await tasks.count(session, BIZ) == 0


async def test_team_ids_validated(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(DanglingReferenceError):
        await make_project(session, "proj_t", team_ids=["ag_ghost"])
    ok = await make_project(session, "proj_t", team_ids=["ag_1"])
    assert ok.team_ids == ["ag_1"]


async def test_db_check_constraints_fire(session: AsyncSession) -> None:
    """CHECKs are the last line of defence even if repo validation is bypassed."""
    await make_world(session)
    session.add(
        Task(
            id="t_raw",
            business_id=BIZ,
            project_id="proj_1",
            title="raw",
            owner_agent_id="ag_1",
            status="exploding",
            progress=0,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_activity_append_only(session: AsyncSession) -> None:
    await make_world(session)
    assert not hasattr(activity, "update") and not hasattr(activity, "delete")
    ev = await activity.append(session, BIZ, {"agent_name": "Atlas", "text": "hi", "kind": "work"})
    assert ev.id
    rows = await activity.list(session, BIZ)
    assert [r.id for r in rows] == [ev.id]
    assert clamp_progress(150) == 100 and clamp_progress(-5) == 0
