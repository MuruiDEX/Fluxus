"""SQLite portability subset — honest compatibility evidence, nothing more.

PostgreSQL stays the primary and mandatory environment: nothing here weakens
a PG guarantee to make SQLite pass. Each test asserts ONLY semantics both
engines truly share (CHECK enforcement, FK with PRAGMA, rollback, JSON and
Numeric round-trips). Anything engine-specific (SELECT … FOR UPDATE, native
types, concurrent writers) is PostgreSQL-only and lives in the other files,
skipped here by construction — not by xfail.
"""

import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.persistence.base import Base
from app.persistence.engine import session_factory


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def sqlite_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite+aiosqlite:///{tmp.name}"
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    os.unlink(tmp.name)


async def fresh_session(sqlite_engine: AsyncEngine) -> AsyncSession:
    factory = session_factory(sqlite_engine)
    sess = factory()
    for table in reversed(Base.metadata.sorted_tables):
        await sess.execute(text(f"DELETE FROM {table.name}"))
    await sess.commit()
    return sess


async def test_crud_roundtrip(sqlite_engine: AsyncEngine) -> None:
    from tests.factories import make_world

    sess = await fresh_session(sqlite_engine)
    try:
        await make_world(sess)
        from tests.factories import projects

        assert await projects.get(sess, "biz_test", "proj_1") is not None
    finally:
        await sess.close()


async def test_check_constraints_enforced(sqlite_engine: AsyncEngine) -> None:
    from app.persistence.models.task import Task
    from tests.factories import make_world

    sess = await fresh_session(sqlite_engine)
    try:
        await make_world(sess)
        sess.add(
            Task(
                id="t_bad",
                business_id="biz_test",
                project_id="proj_1",
                title="x",
                owner_agent_id="ag_1",
                status="exploding",
                progress=0,
            )
        )
        with pytest.raises(IntegrityError):
            await sess.flush()
    finally:
        await sess.close()


async def test_fk_enforced_with_pragma(sqlite_engine: AsyncEngine) -> None:
    from app.persistence.models.task import Task
    from tests.factories import make_business

    sess = await fresh_session(sqlite_engine)
    try:
        await make_business(sess)
        sess.add(
            Task(
                id="t_fk",
                business_id="biz_test",
                project_id="proj_ghost",
                title="x",
                owner_agent_id="ag_1",
                status="working",
                progress=0,
            )
        )
        with pytest.raises(IntegrityError):
            await sess.flush()
    finally:
        await sess.close()


async def test_transaction_rollback(sqlite_engine: AsyncEngine) -> None:
    from tests.factories import BIZ, businesses, make_business

    sess = await fresh_session(sqlite_engine)
    try:
        await make_business(sess)
        with pytest.raises(IntegrityError):
            async with sess.begin_nested():
                from app.persistence.models.agent import Agent

                sess.add(Agent(id="ag_x", business_id=BIZ, name="x", role="y", status="nope"))
                await sess.flush()
        assert await businesses.count(sess, BIZ) == 1
    finally:
        await sess.close()


async def test_lease_consistency_check_enforced(sqlite_engine: AsyncEngine) -> None:
    """S2.2.4.1 lease CHECKs also hold under Base.metadata.create_all on SQLite."""
    from datetime import UTC, datetime, timedelta

    from app.persistence.models.task import Task
    from tests.factories import make_world

    sess = await fresh_session(sqlite_engine)
    try:
        await make_world(sess)
        now = datetime.now(UTC)

        def task(task_id: str, **over: object) -> Task:
            data: dict = {
                "id": task_id,
                "business_id": "biz_test",
                "project_id": "proj_1",
                "title": "x",
                "owner_agent_id": "ag_1",
                "status": "working",
                "progress": 0,
            }
            data.update(over)
            return Task(**data)

        # No lease, a consistent live lease, and a consistent expired lease.
        sess.add(task("t_null"))
        sess.add(
            task(
                "t_live",
                lease_owner="w",
                lease_expires_at=now + timedelta(seconds=60),
                lease_deadline_at=now + timedelta(seconds=120),
            )
        )
        sess.add(
            task(
                "t_expired",
                lease_owner="w",
                lease_expires_at=now - timedelta(seconds=60),
                lease_deadline_at=now + timedelta(seconds=60),
            )
        )
        await sess.flush()

        bad = (
            {"lease_owner": "w"},
            {"lease_expires_at": now + timedelta(seconds=60)},
            {
                "lease_owner": "w",
                "lease_expires_at": now + timedelta(seconds=60),
            },
            {
                "lease_owner": "w",
                "lease_expires_at": now + timedelta(seconds=120),
                "lease_deadline_at": now + timedelta(seconds=60),
            },
        )
        for index, over in enumerate(bad):
            with pytest.raises(IntegrityError):
                async with sess.begin_nested():
                    sess.add(task(f"t_bad_{index}", **over))
                    await sess.flush()

        with pytest.raises(IntegrityError):
            async with sess.begin_nested():
                sess.add(task("t_neg_gen", execution_generation=-1))
                await sess.flush()
    finally:
        await sess.close()
