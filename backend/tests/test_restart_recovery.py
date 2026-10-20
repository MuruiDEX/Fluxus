"""Backend restart drill (Stage 13): engine dispose + recreate mid-lifecycle.

Simulates a process restart by disposing the SQLAlchemy engine and building
a fresh one over the same database — all persisted state must survive
intact: completed tasks never re-run, crashed working rows refuse re-runs
until recovered, attempt budgets and gates persist, spend never double
counts, and other tenants are untouched. Uses only production code paths
(repositories + executor + spend helper), each engine on the shared session
event loop, disposed before return.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.execution.executor import ExecutionRequest, execute_task
from app.llm.mock import MockProvider
from app.persistence.engine import create_engine_from_url, session_factory
from app.persistence.repositories.concrete import ApprovalRepository, TaskRepository
from tests.conftest import TABLES
from tests.factories import BIZ_OTHER, make_agent, make_business, make_project
from tests.factories import tasks as tasks_repo

BIZ = "biz_restart"
VALID = '{"summary": "Researched 12 rivals.", "findings": ["a", "b"]}'
BROKEN = "not json at all {{{"


async def _fresh_factory(test_db_url: str):
    """Own engine over the shared test DB, tables truncated, caller disposes."""
    engine = create_engine_from_url(test_db_url)
    factory = session_factory(engine)
    async with factory() as session:
        async with session.begin():
            for table in TABLES:
                await session.execute(text(f"TRUNCATE {table} CASCADE"))
    return engine, factory


async def _seed_world(factory) -> None:
    async with factory() as session:
        async with session.begin():
            await make_business(session, BIZ)
            await make_agent(session, "ag_1", BIZ)
            await make_project(session, "proj_1", BIZ)


async def _spend_of(factory, biz: str, project_id: str) -> Decimal:
    from app.persistence.repositories.concrete import ProjectRepository

    async with factory() as session:
        proj = await ProjectRepository().get(session, biz, project_id)
        assert proj is not None
        return proj.ai_spent


def _request(task_id: str, project_id: str = "proj_1") -> ExecutionRequest:
    return ExecutionRequest(business_id=BIZ, project_id=project_id, task_id=task_id, timeout_s=10.0)


async def _make_task(factory, task_id: str, status: str = "not_started", **over) -> None:
    from tests.factories import make_task

    async with factory() as session:
        async with session.begin():
            await make_task(session, task_id, BIZ, "proj_1", "ag_1", status=status, **over)


async def test_completed_never_reruns_after_restart(test_db_url: str) -> None:
    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        await _make_task(factory, "t_done")
        async with factory() as session:
            first = await execute_task(session, MockProvider(texts=[VALID]), _request("t_done"))
        assert first.outcome == "completed"
        spend_before = await _spend_of(factory, BIZ, "proj_1")
        async with factory() as session:
            row = await tasks_repo.get(session, BIZ, "t_done")
            assert row is not None
            attempts_before = row.attempts
        await engine.dispose()

        engine2 = create_engine_from_url(test_db_url)
        try:
            factory2 = session_factory(engine2)
            async with factory2() as session:
                second = await execute_task(
                    session, MockProvider(texts=[VALID]), _request("t_done")
                )
            assert second.outcome == "already_completed"
            assert await _spend_of(factory2, BIZ, "proj_1") == spend_before
            async with factory2() as session:
                row = await tasks_repo.get(session, BIZ, "t_done")
                assert row is not None and row.attempts == attempts_before
        finally:
            await engine2.dispose()
    finally:
        await engine.dispose()


async def test_crashed_working_refuses_then_recovers_after_restart(
    test_db_url: str,
) -> None:
    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        await _make_task(factory, "t_crash")
        async with factory() as session:
            async with session.begin():
                claimed = await TaskRepository().claim_task(
                    session, BIZ, "t_crash", from_statuses=("not_started",), to_status="working"
                )
                assert claimed is True
        await engine.dispose()

        engine2 = create_engine_from_url(test_db_url)
        try:
            factory2 = session_factory(engine2)
            fresher = MockProvider(texts=[VALID])
            async with factory2() as session:
                refused = await execute_task(session, fresher, _request("t_crash"))
            # The crashed run still owns the row: no second execution starts.
            assert refused.outcome == "already_running"
            assert fresher.calls == []
            # Recovery only after provable staleness — then exactly once.
            async with factory2() as session:
                async with session.begin():
                    await tasks_repo.update(
                        session, BIZ, "t_crash", {"updated_at": datetime(2000, 1, 1, tzinfo=UTC)}
                    )
            async with factory2() as session:
                async with session.begin():
                    reset_to = await TaskRepository().recover_stale(
                        session, BIZ, "t_crash", stale_seconds=1.0
                    )
            assert reset_to == "not_started"
            assert await _spend_of(factory2, BIZ, "proj_1") == Decimal("0")
        finally:
            await engine2.dispose()
    finally:
        await engine.dispose()


async def test_failed_budget_persists_across_restart(test_db_url: str) -> None:
    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        await _make_task(factory, "t_flaky")
        for expected_attempts in (1, 2):
            async with factory() as session:
                result = await execute_task(
                    session, MockProvider(texts=[BROKEN]), _request("t_flaky")
                )
            assert result.outcome == "failed", result
            async with factory() as session:
                row = await tasks_repo.get(session, BIZ, "t_flaky")
                assert row is not None and row.attempts == expected_attempts
            # Restart between failures: the budget lives in the row, not RAM.
            await engine.dispose()
            engine = create_engine_from_url(test_db_url)
            factory = session_factory(engine)
        async with factory() as session:
            third = await execute_task(session, MockProvider(texts=[BROKEN]), _request("t_flaky"))
        assert third.outcome == "failed"
        async with factory() as session:
            fourth = await execute_task(session, MockProvider(texts=[VALID]), _request("t_flaky"))
        assert fourth.outcome == "refused" and fourth.error_code == "conflict"
        assert await _spend_of(factory, BIZ, "proj_1") == Decimal("0")
    finally:
        await engine.dispose()


async def test_gate_survives_restart(test_db_url: str) -> None:
    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        await _make_task(factory, "t_gated")
        from tests.factories import make_approval

        async with factory() as session:
            async with session.begin():
                await make_approval(session, "ap_gate", BIZ)
                await tasks_repo.update(session, BIZ, "t_gated", {"approval_gate_id": "ap_gate"})
        gate_probe = MockProvider(texts=[VALID])
        async with factory() as session:
            refused = await execute_task(session, gate_probe, _request("t_gated"))
        assert refused.outcome == "refused" and refused.error_code == "approval_required"
        assert gate_probe.calls == []
        await engine.dispose()

        engine2 = create_engine_from_url(test_db_url)
        try:
            factory2 = session_factory(engine2)
            async with factory2() as session:
                async with session.begin():
                    decided = await ApprovalRepository().decide(
                        session, BIZ, "ap_gate", to_status="approved"
                    )
                    assert decided is True
            async with factory2() as session:
                completed = await execute_task(
                    session, MockProvider(texts=[VALID]), _request("t_gated")
                )
            assert completed.outcome == "completed"
        finally:
            await engine2.dispose()
    finally:
        await engine.dispose()


async def test_other_tenant_untouched_by_restart(test_db_url: str) -> None:
    from sqlalchemy import func, select

    from app.persistence.models.task import Task

    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        async with factory() as session:
            async with session.begin():
                await make_business(session, BIZ_OTHER)
                await make_agent(session, "ag_x", BIZ_OTHER)
                await make_project(session, "proj_x", BIZ_OTHER)
                from tests.factories import make_task

                await make_task(session, "t_x", BIZ_OTHER, "proj_x", "ag_x")
        await engine.dispose()

        engine2 = create_engine_from_url(test_db_url)
        try:
            factory2 = session_factory(engine2)
            async with factory2() as session:
                count = (
                    await session.execute(
                        select(func.count()).select_from(Task).where(Task.business_id == BIZ_OTHER)
                    )
                ).scalar_one()
                assert count == 1
        finally:
            await engine2.dispose()
    finally:
        await engine.dispose()


async def test_restart_leaves_no_open_transaction(test_db_url: str) -> None:
    """Sessions borrowed around a restart stay usable with no pending writes.

    (A read-only autobegin transaction after a SELECT is normal SQLAlchemy
    behavior — the assertion targets uncommitted state, not its presence.)
    """
    engine, factory = await _fresh_factory(test_db_url)
    try:
        await _seed_world(factory)
        await engine.dispose()
        engine2 = create_engine_from_url(test_db_url)
        try:
            factory2 = session_factory(engine2)
            async with factory2() as session:
                assert len(session.new) == 0 and len(session.dirty) == 0
                row = await tasks_repo.get(session, BIZ, "missing")
                assert row is None
                assert len(session.new) == 0 and len(session.dirty) == 0
                await session.rollback()
                assert len(session.new) == 0 and len(session.dirty) == 0
        finally:
            await engine2.dispose()
    finally:
        await engine.dispose()
