"""Alembic migrations on a dedicated clean schema (muse_mig).

Never touches dev (muse) or test (muse_test) data. Verifies: upgrade head
from empty, downgrade one step, upgrade again — the revision chain is sound.
"""

import os

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.persistence.engine import create_engine_from_url

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    os.environ["APP_DATABASE_URL"] = url.replace("postgresql+asyncpg", "postgresql")
    # env.py reads APP_DATABASE_URL and adapts the driver internally.
    return cfg


async def _table_names(url: str) -> set[str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    return names


@pytest.mark.asyncio()
async def test_upgrade_downgrade_cycle(mig_db_url: str) -> None:
    # Start from a guaranteed-empty schema (two statements: asyncpg
    # forbids multi-command prepared statements).
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    # Point Alembic at the isolated migration database (plain URL is fine;
    # env.py normalizes it to the async driver).
    os.environ["APP_DATABASE_URL"] = mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "head")
    names = await _table_names(mig_db_url)
    for expected in (
        "businesses",
        "agents",
        "projects",
        "tasks",
        "task_dependencies",
        "approvals",
        "brain_entries",
        "marketplace_listings",
        "activity_events",
        "users",
        "memberships",
        "auth_sessions",
    ):
        assert expected in names, f"missing table after upgrade: {expected}"

    command.downgrade(cfg, "-1")
    names_after = await _table_names(mig_db_url)
    assert "businesses" in names_after  # only 0002 reverted
    assert "users" not in names_after
    assert "memberships" not in names_after
    assert "auth_sessions" not in names_after

    command.downgrade(cfg, "base")
    assert "businesses" not in await _table_names(mig_db_url)

    command.upgrade(cfg, "head")
    assert "businesses" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_stage5_data_survives_0002_upgrade(mig_db_url: str) -> None:
    """Pre-existing business data must be untouched by the auth migration."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    os.environ["APP_DATABASE_URL"] = mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "54c46892a254")
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_old', 'Old Biz', '', '', '', '', 0,"
                " '[]', '{}', '[]', '')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT name FROM businesses WHERE id = 'biz_old'"))
        ).scalar_one()
    await engine.dispose()
    assert row == "Old Biz"
