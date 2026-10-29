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
    # env.py reads APP_DATABASE_URL and adapts the driver internally.
    return cfg


async def _table_names(url: str) -> set[str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    return names


async def _column_names(url: str, table: str) -> set[str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns(table)})
    await engine.dispose()
    return cols


async def _index_names(url: str, table: str) -> set[str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        idx = await conn.run_sync(lambda c: {i["name"] for i in inspect(c).get_indexes(table)})
    await engine.dispose()
    return idx


@pytest.mark.asyncio()
async def test_upgrade_downgrade_cycle(mig_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Start from a guaranteed-empty schema (two statements: asyncpg
    # forbids multi-command prepared statements).
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    # Point Alembic at the isolated migration database (plain URL is fine;
    # env.py normalizes it to the async driver). monkeypatch restores the
    # variable afterwards — leaking it would repoint any later suite that
    # reads APP_DATABASE_URL from the environment.
    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
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
        "briefs",
        "marketplace_listings",
        "activity_events",
        "users",
        "memberships",
        "auth_sessions",
        "pack_installations",
        "pack_installation_items",
    ):
        assert expected in names, f"missing table after upgrade: {expected}"

    command.downgrade(cfg, "c9d4e2f7a5b3")  # revert 0007 + 0006
    names_after = await _table_names(mig_db_url)
    assert "businesses" in names_after
    assert "briefs" in names_after
    assert "users" in names_after
    assert "memberships" in names_after
    assert "auth_sessions" in names_after
    assert "pack_installations" not in names_after
    assert "pack_installation_items" not in names_after
    assert "source_slug" in await _column_names(mig_db_url, "agents")

    command.upgrade(cfg, "head")
    assert "final_report" in await _column_names(mig_db_url, "briefs")
    assert "source_slug" in await _column_names(mig_db_url, "agents")
    assert "pack_installations" in await _table_names(mig_db_url)

    command.downgrade(cfg, "b7e2d4f6a8c1")  # revert 0005 as well
    assert "source_slug" not in await _column_names(mig_db_url, "agents")
    assert "final_report" in await _column_names(mig_db_url, "briefs")
    assert "pack_installations" not in await _table_names(mig_db_url)

    command.downgrade(cfg, "base")
    assert "businesses" not in await _table_names(mig_db_url)

    command.upgrade(cfg, "head")
    assert "businesses" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_0005_agent_source_column_and_index(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0005 is additive: source_slug + partial unique index, clean downgrade."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "head")
    assert "source_slug" in await _column_names(mig_db_url, "agents")
    assert "uq_agents_business_source" in await _index_names(mig_db_url, "agents")

    command.downgrade(cfg, "b7e2d4f6a8c1")
    assert "source_slug" not in await _column_names(mig_db_url, "agents")

    command.upgrade(cfg, "head")
    assert "source_slug" in await _column_names(mig_db_url, "agents")


@pytest.mark.asyncio()
async def test_0006_pack_tables_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0006 is additive: receipts + items before/after, clean downgrade."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "head")
    assert {
        "id",
        "business_id",
        "pack_slug",
        "version",
        "created_at",
        "updated_at",
    } <= await _column_names(mig_db_url, "pack_installations")
    assert {
        "id",
        "business_id",
        "installation_id",
        "kind",
        "ref",
        "entity_id",
    } <= await _column_names(mig_db_url, "pack_installation_items")
    assert "ix_pack_installations_business_id" in await _index_names(
        mig_db_url, "pack_installations"
    )
    assert "ix_pack_installation_items_installation_id" in await _index_names(
        mig_db_url, "pack_installation_items"
    )

    command.downgrade(cfg, "c9d4e2f7a5b3")  # revert 0007 + 0006
    names = await _table_names(mig_db_url)
    assert "pack_installations" not in names
    assert "pack_installation_items" not in names
    assert "source_slug" in await _column_names(mig_db_url, "agents")

    command.upgrade(cfg, "head")
    assert "pack_installations" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_0007_usage_ledger_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0007 is additive: two tables + a defaulted project column, clean downgrade."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "head")
    names = await _table_names(mig_db_url)
    assert "usage_records" in names
    assert "budget_reservations" in names
    assert "allow_unpriced_ai" in await _column_names(mig_db_url, "projects")
    assert {
        "idempotency_key",
        "cost_amount",
        "usage_status",
        "cost_source",
        "tariff_version",
        "provider_request_id",
        "call_kind",
    } <= await _column_names(mig_db_url, "usage_records")
    assert {
        "idempotency_key",
        "attempt_key",
        "run_seq",
        "mode",
        "usage_record_id",
    } <= await _column_names(mig_db_url, "budget_reservations")
    assert "ix_usage_records_business_project" in await _index_names(mig_db_url, "usage_records")
    assert "ix_budget_reservations_business_project" in await _index_names(
        mig_db_url, "budget_reservations"
    )

    # Down one step: only 0008 (call_kind) is removed; the ledger remains.
    command.downgrade(cfg, "f1a2b3c4d5e6")
    assert "call_kind" not in await _column_names(mig_db_url, "usage_records")
    assert "usage_records" in await _table_names(mig_db_url)

    command.downgrade(cfg, "d8e6b4a2f0c7")
    names = await _table_names(mig_db_url)
    assert "usage_records" not in names
    assert "budget_reservations" not in names
    assert "allow_unpriced_ai" not in await _column_names(mig_db_url, "projects")

    command.upgrade(cfg, "head")
    assert "usage_records" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_0007_backfills_allow_unpriced_ai_false(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project created before 0007 must read allow_unpriced_ai = false."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "d8e6b4a2f0c7")  # 0006, before usage ledger
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_old', 'Old', '', '', '', '', 0, '[]', '{}', '[]', '')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, business_id, title, goal, status,"
                " progress, budget, ai_budget, ai_spent, deadline, team_ids,"
                " milestones)"
                " VALUES ('proj_old', 'biz_old', 'P', '', 'planning', 0, 0, 0, 0,"
                " '', '[]', '[]')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        value = (
            await conn.execute(text("SELECT allow_unpriced_ai FROM projects WHERE id = 'proj_old'"))
        ).scalar_one()
    await engine.dispose()
    assert value is False


@pytest.mark.asyncio()
async def test_0008_backfills_call_kind_task_execution(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows written before 0008 are backfilled to `task_execution`."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "f1a2b3c4d5e6")  # 0007, before call_kind
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_old', 'Old', '', '', '', '', 0, '[]', '{}', '[]', '')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records (id, business_id, attempt_key, provider,"
                " model, idempotency_key, occurred_at)"
                " VALUES ('u_old', 'biz_old', 'k', 'mock', 'mock', 'usage:old', now())"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        value = (
            await conn.execute(text("SELECT call_kind FROM usage_records WHERE id = 'u_old'"))
        ).scalar_one()
    await engine.dispose()
    assert value == "task_execution"


@pytest.mark.asyncio()
async def test_stage5_data_survives_0002_upgrade(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing business data must be untouched by the auth migration."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
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


@pytest.mark.asyncio()
async def test_0009_business_budget_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0009 is additive and backfills existing businesses to no limit/fail-closed."""
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    monkeypatch.setenv(
        "APP_DATABASE_URL", mig_db_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    cfg = _alembic_config(mig_db_url)

    command.upgrade(cfg, "b4e8f1a2c3d4")  # 0008, before business budget
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_old', 'Old', '', '', '', '', 0, '[]', '{}', '[]', '')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")
    assert {"ai_budget", "allow_unpriced_ai"} <= await _column_names(mig_db_url, "businesses")
    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT ai_budget, allow_unpriced_ai FROM businesses WHERE id = 'biz_old'")
            )
        ).one()
    await engine.dispose()
    assert row == (0, False)

    command.downgrade(cfg, "b4e8f1a2c3d4")
    assert "ai_budget" not in await _column_names(mig_db_url, "businesses")
    assert "allow_unpriced_ai" not in await _column_names(mig_db_url, "businesses")

    command.upgrade(cfg, "head")
    assert "ai_budget" in await _column_names(mig_db_url, "businesses")
