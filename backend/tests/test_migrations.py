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


async def _check_constraint_names(url: str, table: str) -> set[str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(
            lambda c: {cc["name"] for cc in inspect(c).get_check_constraints(table)}
        )
    await engine.dispose()
    return names


async def _column_details(url: str, table: str) -> dict[str, dict]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda c: inspect(c).get_columns(table))
    await engine.dispose()
    return {col["name"]: col for col in columns}


async def _index_details(url: str, table: str) -> dict[str, dict]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        indexes = await conn.run_sync(lambda c: inspect(c).get_indexes(table))
    await engine.dispose()
    return {index["name"]: index for index in indexes}


async def _check_constraint_texts(url: str, table: str) -> dict[str, str]:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        constraints = await conn.run_sync(lambda c: inspect(c).get_check_constraints(table))
    await engine.dispose()
    return {cc["name"]: cc["sqltext"] for cc in constraints}


async def _index_predicate(url: str, index_name: str) -> str | None:
    engine = create_engine_from_url(url)
    async with engine.connect() as conn:
        predicate = (
            await conn.execute(
                text(
                    "SELECT pg_get_expr(i.indpred, i.indrelid)"
                    " FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                    " WHERE c.relname = :name"
                ),
                {"name": index_name},
            )
        ).scalar_one_or_none()
    await engine.dispose()
    return predicate


def _normalize_sql(sql: str) -> str:
    return "".join(sql.split()).replace("(", "").replace(")", "").replace("::text", "")


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


@pytest.mark.asyncio()
async def test_0010_execution_ownership_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0010 is additive: ownership columns/constraints/indexes, existing rows kept."""
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

    command.upgrade(cfg, "a9c1e3f5b7d9")  # 0009, before execution ownership
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
                "INSERT INTO agents (id, business_id, name, role, responsibilities,"
                " tools, permissions, status, cost_per_month, performance, hired,"
                " avatar_hue)"
                " VALUES ('ag_old', 'biz_old', 'A', 'Role', '[]', '[]', '[]',"
                " 'available', 0, '{}', false, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, business_id, title, goal, status,"
                " progress, budget, ai_budget, ai_spent, deadline, team_ids,"
                " milestones)"
                " VALUES ('proj_old', 'biz_old', 'P', '', 'in_progress', 0, 0, 0, 0,"
                " '', '[]', '[]')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO tasks (id, business_id, project_id, title,"
                " owner_agent_id, status, progress, cost_accrued, eta_minutes,"
                " summary, attempts, gate_at)"
                " VALUES ('t_old', 'biz_old', 'proj_old', 'Keep me', 'ag_old',"
                " 'not_started', 0, 0, 0, '', 0, 100)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_reservations (id, business_id, project_id,"
                " task_id, attempt_key, amount, idempotency_key)"
                " VALUES ('res_old', 'biz_old', 'proj_old', 't_old', 'attempt:old',"
                " 1.00, 'reserve:attempt:old:1')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")

    assert {
        "execution_generation",
        "lease_owner",
        "lease_expires_at",
        "lease_deadline_at",
    } <= await _column_names(mig_db_url, "tasks")
    assert "execution_generation" in await _column_names(mig_db_url, "budget_reservations")
    assert {
        "ck_tasks_execution_generation_nonneg",
        "ck_tasks_lease_consistency",
        "ck_tasks_lease_within_deadline",
    } <= await _check_constraint_names(mig_db_url, "tasks")
    assert "ix_tasks_working_lease" in await _index_names(mig_db_url, "tasks")
    assert "ix_budget_reservations_business_task" in await _index_names(
        mig_db_url, "budget_reservations"
    )

    # Exact column types / nullability / server defaults.
    task_columns = await _column_details(mig_db_url, "tasks")
    assert str(task_columns["execution_generation"]["type"]) == "INTEGER"
    assert task_columns["execution_generation"]["nullable"] is False
    assert task_columns["execution_generation"]["default"] == "0"
    assert str(task_columns["lease_owner"]["type"]) == "VARCHAR(64)"
    assert task_columns["lease_owner"]["nullable"] is True
    assert task_columns["lease_owner"]["default"] is None
    for name in ("lease_expires_at", "lease_deadline_at"):
        column = task_columns[name]
        assert str(column["type"]).startswith("TIMESTAMP")
        assert getattr(column["type"], "timezone", None) is True
        assert column["nullable"] is True
        assert column["default"] is None

    reservation_columns = await _column_details(mig_db_url, "budget_reservations")
    reservation_generation = reservation_columns["execution_generation"]
    assert str(reservation_generation["type"]) == "INTEGER"
    assert reservation_generation["nullable"] is True
    assert reservation_generation["default"] is None

    # Index shape: partial predicate and reservation column order.
    task_index = (await _index_details(mig_db_url, "tasks"))["ix_tasks_working_lease"]
    assert task_index["column_names"] == ["lease_expires_at"]
    assert task_index["unique"] is False
    predicate = await _index_predicate(mig_db_url, "ix_tasks_working_lease")
    assert predicate is not None
    assert _normalize_sql(predicate) == "status='working'"

    reservation_index = (await _index_details(mig_db_url, "budget_reservations"))[
        "ix_budget_reservations_business_task"
    ]
    assert reservation_index["column_names"] == ["business_id", "task_id"]
    assert reservation_index["unique"] is False

    # All three CHECK constraints, not just their names.
    checks = await _check_constraint_texts(mig_db_url, "tasks")
    assert _normalize_sql(checks["ck_tasks_execution_generation_nonneg"]) == (
        "execution_generation>=0"
    )
    assert _normalize_sql(checks["ck_tasks_lease_consistency"]) == (
        "lease_ownerISNULLANDlease_expires_atISNULLANDlease_deadline_atISNULL"
        "ORlease_ownerISNOTNULLANDlease_expires_atISNOTNULL"
        "ANDlease_deadline_atISNOTNULL"
    )
    assert _normalize_sql(checks["ck_tasks_lease_within_deadline"]) == (
        "lease_expires_atISNULLORlease_deadline_atISNULLORlease_expires_at<=lease_deadline_at"
    )

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        task_row = (
            await conn.execute(
                text(
                    "SELECT title, execution_generation, lease_owner,"
                    " lease_expires_at, lease_deadline_at"
                    " FROM tasks WHERE id = 't_old'"
                )
            )
        ).one()
        reservation_row = (
            await conn.execute(
                text(
                    "SELECT status, execution_generation"
                    " FROM budget_reservations WHERE id = 'res_old'"
                )
            )
        ).one()
    await engine.dispose()
    assert task_row == ("Keep me", 0, None, None, None)
    assert reservation_row == ("active", None)

    # The all-or-nothing CHECK rejects a partial lease on real PostgreSQL.
    from sqlalchemy.exc import IntegrityError

    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE tasks SET lease_owner = 'w' WHERE id = 't_old'"))
    await engine.dispose()

    command.downgrade(cfg, "a9c1e3f5b7d9")
    assert "execution_generation" not in await _column_names(mig_db_url, "tasks")
    assert "lease_owner" not in await _column_names(mig_db_url, "tasks")
    assert "lease_expires_at" not in await _column_names(mig_db_url, "tasks")
    assert "lease_deadline_at" not in await _column_names(mig_db_url, "tasks")
    assert "execution_generation" not in await _column_names(mig_db_url, "budget_reservations")
    assert "ix_tasks_working_lease" not in await _index_names(mig_db_url, "tasks")
    assert "ix_budget_reservations_business_task" not in await _index_names(
        mig_db_url, "budget_reservations"
    )
    assert "ck_tasks_lease_consistency" not in await _check_constraint_names(mig_db_url, "tasks")
    assert "ck_tasks_lease_within_deadline" not in await _check_constraint_names(
        mig_db_url, "tasks"
    )
    assert "ck_tasks_execution_generation_nonneg" not in await _check_constraint_names(
        mig_db_url, "tasks"
    )

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        preserved_task = (
            await conn.execute(text("SELECT title FROM tasks WHERE id = 't_old'"))
        ).scalar_one()
        preserved_reservation = (
            await conn.execute(text("SELECT status FROM budget_reservations WHERE id = 'res_old'"))
        ).scalar_one()
    await engine.dispose()
    assert preserved_task == "Keep me"
    assert preserved_reservation == "active"

    command.upgrade(cfg, "head")
    assert "execution_generation" in await _column_names(mig_db_url, "tasks")
    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        generation = (
            await conn.execute(text("SELECT execution_generation FROM tasks WHERE id = 't_old'"))
        ).scalar_one()
    await engine.dispose()
    assert generation == 0


@pytest.mark.asyncio()
async def test_0011_usage_reconciliation_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0011 adds `reconciled` plus the append-only audit table, without data loss."""
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

    command.upgrade(cfg, "0010_execution_ownership")
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
                "INSERT INTO agents (id, business_id, name, role, responsibilities,"
                " tools, permissions, status, cost_per_month, performance, hired,"
                " avatar_hue)"
                " VALUES ('ag_old', 'biz_old', 'A', 'Role', '[]', '[]', '[]',"
                " 'available', 0, '{}', false, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, business_id, title, goal, status,"
                " progress, budget, ai_budget, ai_spent, deadline, team_ids,"
                " milestones)"
                " VALUES ('proj_old', 'biz_old', 'P', '', 'in_progress', 0, 0, 0, 0,"
                " '', '[]', '[]')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO tasks (id, business_id, project_id, title,"
                " owner_agent_id, status, progress, cost_accrued, eta_minutes,"
                " summary, attempts, gate_at)"
                " VALUES ('t_old', 'biz_old', 'proj_old', 'Keep me', 'ag_old',"
                " 'not_started', 0, 0, 0, '', 0, 100)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_reservations (id, business_id, project_id,"
                " task_id, attempt_key, amount, idempotency_key)"
                " VALUES ('res_old', 'biz_old', 'proj_old', 't_old', 'attempt:old',"
                " 2.00, 'reserve:attempt:old:1')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records (id, business_id, project_id, task_id,"
                " attempt_key, provider, model, idempotency_key, occurred_at,"
                " cost_amount, cost_source, usage_status)"
                " VALUES ('u_old', 'biz_old', 'proj_old', 't_old', 'attempt:old',"
                " 'openai', 'gpt-4o', 'usage:confirmed', now(), 2.00, 'provider',"
                " 'confirmed')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "head")

    assert "usage_reconciliations" in await _table_names(mig_db_url)
    assert {
        "id",
        "business_id",
        "project_id",
        "task_id",
        "reservation_id",
        "usage_record_id",
        "attempt",
        "attempt_key",
        "execution_generation",
        "outcome",
        "amount",
        "currency",
        "cost_source",
        "provider",
        "model",
        "provider_request_id",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "evidence",
        "attested",
        "actor_user_id",
        "actor_email",
    } <= await _column_names(mig_db_url, "usage_reconciliations")
    assert {
        "ck_usage_reconciliations_outcome",
        "ck_usage_reconciliations_cost_source",
        "ck_usage_reconciliations_amount",
        "ck_usage_reconciliations_usage_record",
        "ck_usage_reconciliations_tokens_nonneg",
        "ck_usage_reconciliations_attested",
    } <= await _check_constraint_names(mig_db_url, "usage_reconciliations")
    index_names = await _index_names(mig_db_url, "usage_reconciliations")
    assert {
        "ix_usage_reconciliations_business_id",
        "ix_usage_reconciliations_business_project",
        "ix_usage_reconciliations_business_task",
    } <= index_names
    indexes = await _index_details(mig_db_url, "usage_reconciliations")
    assert indexes["uq_usage_reconciliations_reservation"]["unique"] is True
    assert indexes["uq_usage_reconciliations_usage_record"]["unique"] is True

    checks = await _check_constraint_texts(mig_db_url, "budget_reservations")
    assert "reconciled" in checks["ck_budget_reservations_status"]

    # Existing data is untouched by the status CHECK replacement.
    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        preserved = (
            await conn.execute(
                text("SELECT status, amount FROM budget_reservations WHERE id = 'res_old'")
            )
        ).one()
    await engine.dispose()
    assert preserved == ("active", 2)

    # `reconciled` is accepted; the audit row and its guards behave.
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE budget_reservations SET status = 'reconciled' WHERE id = 'res_old'")
        )
        await conn.execute(
            text(
                "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                " task_id, reservation_id, usage_record_id, attempt_key, amount,"
                " provider, model, actor_email, evidence)"
                " VALUES ('rec_old', 'biz_old', 'proj_old', 't_old', 'res_old',"
                " 'u_old', 'attempt:old', 2.00, 'openai', 'gpt-4o',"
                " 'op@example.com', 'invoice line 1')"
            )
        )
    await engine.dispose()

    from sqlalchemy.exc import IntegrityError

    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, usage_record_id, attempt_key, amount,"
                    " provider, model)"
                    " VALUES ('rec_dup', 'biz_old', 'proj_old', 't_old', 'res_old',"
                    " 'u_old', 'attempt:old', 1.00, 'openai', 'gpt-4o')"
                )
            )
    await engine.dispose()

    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, usage_record_id, attempt_key, amount,"
                    " provider, model)"
                    " VALUES ('rec_zero', 'biz_old', 'proj_old', 't_old', 'res_old',"
                    " 'u_old', 'attempt:old', 0, 'openai', 'gpt-4o')"
                )
            )
    await engine.dispose()

    # Downgrade must refuse to silently discard confirmed-charge audit state.
    with pytest.raises(RuntimeError, match="reconciled budget reservations"):
        command.downgrade(cfg, "0010_execution_ownership")
    assert "usage_reconciliations" in await _table_names(mig_db_url)

    # After the state is explicitly resolved, downgrade is clean.
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM usage_reconciliations WHERE id = 'rec_old'"))
        await conn.execute(
            text("UPDATE budget_reservations SET status = 'active' WHERE id = 'res_old'")
        )
    await engine.dispose()

    command.downgrade(cfg, "0010_execution_ownership")
    assert "usage_reconciliations" not in await _table_names(mig_db_url)
    checks = await _check_constraint_texts(mig_db_url, "budget_reservations")
    assert "reconciled" not in checks["ck_budget_reservations_status"]

    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE budget_reservations SET status = 'reconciled' WHERE id = 'res_old'")
            )
    await engine.dispose()

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        kept = (await conn.execute(text("SELECT title FROM tasks WHERE id = 't_old'"))).scalar_one()
    await engine.dispose()
    assert kept == "Keep me"

    command.upgrade(cfg, "head")
    assert "usage_reconciliations" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_0012_no_charge_reconciliation_round_trip(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0012 adds the attested no-charge outcome, constraints and downgrade guards."""
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

    command.upgrade(cfg, "0011_usage_reconciliation")
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_nc', 'NC', '', '', '', '', 0, '[]', '{}', '[]', '')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agents (id, business_id, name, role, responsibilities,"
                " tools, permissions, status, cost_per_month, performance, hired,"
                " avatar_hue)"
                " VALUES ('ag_nc', 'biz_nc', 'A', 'Role', '[]', '[]', '[]',"
                " 'available', 0, '{}', false, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, business_id, title, goal, status,"
                " progress, budget, ai_budget, ai_spent, deadline, team_ids,"
                " milestones)"
                " VALUES ('proj_nc', 'biz_nc', 'P', '', 'in_progress', 0, 0, 0, 0,"
                " '', '[]', '[]')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO tasks (id, business_id, project_id, title,"
                " owner_agent_id, status, progress, cost_accrued, eta_minutes,"
                " summary, attempts, gate_at)"
                " VALUES ('t_nc', 'biz_nc', 'proj_nc', 'NC task', 'ag_nc',"
                " 'not_started', 0, 0, 0, '', 0, 100)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_reservations (id, business_id, project_id,"
                " task_id, attempt_key, amount, idempotency_key, status, reason)"
                " VALUES ('res_nc', 'biz_nc', 'proj_nc', 't_nc', 'attempt:nc',"
                " 0.00, 'reserve:nc', 'ambiguous', 'timeout')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_reservations (id, business_id, project_id,"
                " task_id, attempt_key, amount, idempotency_key)"
                " VALUES ('res_chg', 'biz_nc', 'proj_nc', 't_nc', 'attempt:chg',"
                " 1.00, 'reserve:chg')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records (id, business_id, project_id, task_id,"
                " attempt_key, provider, model, idempotency_key, occurred_at,"
                " cost_amount, cost_source, usage_status)"
                " VALUES ('u_nc', 'biz_nc', 'proj_nc', 't_nc', 'attempt:chg',"
                " 'openai', 'gpt-4o', 'usage:nc', now(), 1.00, 'provider',"
                " 'confirmed')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "0012_no_charge_reconciliation")

    columns = await _column_details(mig_db_url, "usage_reconciliations")
    assert columns["usage_record_id"]["nullable"] is True
    checks = await _check_constraint_names(mig_db_url, "usage_reconciliations")
    assert {
        "ck_usage_reconciliations_amount",
        "ck_usage_reconciliations_usage_record",
    } <= checks
    assert "ck_usage_reconciliations_amount_pos" not in checks

    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                " task_id, reservation_id, attempt_key, outcome, amount,"
                " cost_source, provider, model, evidence, attested, actor_email)"
                " VALUES ('rec_nc', 'biz_nc', 'proj_nc', 't_nc', 'res_nc',"
                " 'attempt:nc', 'no_charge', 0, 'none', '', '', 'operator says free',"
                " true, 'op@example.com')"
            )
        )
    await engine.dispose()

    from sqlalchemy.exc import IntegrityError

    # A charged row still requires its usage link.
    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, attempt_key, amount, provider, model)"
                    " VALUES ('rec_chg_null', 'biz_nc', 'proj_nc', 't_nc', 'res_chg',"
                    " 'attempt:chg', 1.00, 'openai', 'gpt-4o')"
                )
            )
    await engine.dispose()

    # A no-charge row cannot carry a positive amount.
    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, attempt_key, outcome, amount,"
                    " cost_source, provider, model)"
                    " VALUES ('rec_nc_paid', 'biz_nc', 'proj_nc', 't_nc', 'res_chg',"
                    " 'attempt:chg', 'no_charge', 1.00, 'none', '', '')"
                )
            )
    await engine.dispose()

    # A no-charge row cannot link a usage record.
    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, usage_record_id, attempt_key, outcome,"
                    " amount, cost_source, provider, model)"
                    " VALUES ('rec_nc_link', 'biz_nc', 'proj_nc', 't_nc', 'res_chg',"
                    " 'u_nc', 'attempt:chg', 'no_charge', 0, 'none', '', '')"
                )
            )
    await engine.dispose()

    # A charged row cannot claim the no-charge cost source.
    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, usage_record_id, attempt_key, outcome,"
                    " amount, cost_source, provider, model)"
                    " VALUES ('rec_chg_none', 'biz_nc', 'proj_nc', 't_nc', 'res_chg',"
                    " 'u_nc', 'attempt:chg', 'charged', 1.00, 'none', 'openai',"
                    " 'gpt-4o')"
                )
            )
    await engine.dispose()

    # A no-charge row cannot claim the provider cost source.
    engine = create_engine_from_url(mig_db_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                    " task_id, reservation_id, attempt_key, outcome, amount,"
                    " cost_source, provider, model)"
                    " VALUES ('rec_nc_provider', 'biz_nc', 'proj_nc', 't_nc',"
                    " 'res_chg', 'attempt:chg', 'no_charge', 0, 'provider', '', '')"
                )
            )
    await engine.dispose()

    # Downgrade refuses while an attested no-charge audit row exists.
    with pytest.raises(RuntimeError, match="no-charge reconciliation audit rows"):
        command.downgrade(cfg, "0011_usage_reconciliation")
    assert "usage_reconciliations" in await _table_names(mig_db_url)

    # And refuses while a released/manual_no_charge reservation exists.
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM usage_reconciliations WHERE id = 'rec_nc'"))
        await conn.execute(
            text(
                "UPDATE budget_reservations SET status = 'released',"
                " reason = 'manual_no_charge' WHERE id = 'res_nc'"
            )
        )
    await engine.dispose()
    with pytest.raises(RuntimeError, match="released/manual_no_charge reservations"):
        command.downgrade(cfg, "0011_usage_reconciliation")

    # Once the no-charge state is resolved, the downgrade is clean.
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE budget_reservations SET status = 'active', reason = 'timeout'"
                " WHERE id = 'res_nc'"
            )
        )
    await engine.dispose()

    command.downgrade(cfg, "0011_usage_reconciliation")
    columns = await _column_details(mig_db_url, "usage_reconciliations")
    assert columns["usage_record_id"]["nullable"] is False
    checks = await _check_constraint_names(mig_db_url, "usage_reconciliations")
    assert "ck_usage_reconciliations_amount_pos" in checks
    assert "ck_usage_reconciliations_amount" not in checks
    assert "ck_usage_reconciliations_usage_record" not in checks

    command.upgrade(cfg, "head")
    assert "usage_reconciliations" in await _table_names(mig_db_url)


@pytest.mark.asyncio()
async def test_0012_upgrade_preserves_existing_charged_audit_row(
    mig_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0012's DDL must not rewrite or reject a pre-existing charged audit row."""
    from datetime import UTC, datetime
    from decimal import Decimal

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

    command.upgrade(cfg, "0011_usage_reconciliation")
    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO businesses (id, name, tagline, industry, location,"
                " owner_name, health_score, health_breakdown, metrics,"
                " revenue_series, notes)"
                " VALUES ('biz_hist', 'Hist', '', '', '', '', 0, '[]', '{}', '[]', '')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agents (id, business_id, name, role, responsibilities,"
                " tools, permissions, status, cost_per_month, performance, hired,"
                " avatar_hue)"
                " VALUES ('ag_hist', 'biz_hist', 'A', 'Role', '[]', '[]', '[]',"
                " 'available', 0, '{}', false, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, business_id, title, goal, status,"
                " progress, budget, ai_budget, ai_spent, deadline, team_ids,"
                " milestones)"
                " VALUES ('proj_hist', 'biz_hist', 'P', '', 'in_progress', 0, 0, 0,"
                " 0, '', '[]', '[]')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO tasks (id, business_id, project_id, title,"
                " owner_agent_id, status, progress, cost_accrued, eta_minutes,"
                " summary, attempts, gate_at, execution_generation)"
                " VALUES ('t_hist', 'biz_hist', 'proj_hist', 'Hist task', 'ag_hist',"
                " 'not_started', 0, 0, 0, '', 0, 100, 7)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records (id, business_id, project_id, task_id,"
                " attempt_key, attempt, provider, model,"
                " idempotency_key, occurred_at, cost_amount, currency, cost_source,"
                " usage_status)"
                " VALUES ('u_hist', 'biz_hist', 'proj_hist', 't_hist',"
                " 'attempt:hist', 4, 'openai', 'gpt-4o', 'usage:hist', now(),"
                " 12.34, 'USD', 'provider', 'confirmed')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_reservations (id, business_id, project_id,"
                " task_id, attempt, attempt_key, execution_generation, amount,"
                " currency, status, reason, idempotency_key, usage_record_id)"
                " VALUES ('res_hist', 'biz_hist', 'proj_hist', 't_hist', 4,"
                " 'attempt:hist', 7, 12.34, 'USD', 'reconciled',"
                " 'manual_provider_charge', 'reserve:hist', 'u_hist')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO usage_reconciliations (id, business_id, project_id,"
                " task_id, reservation_id, usage_record_id, attempt, attempt_key,"
                " execution_generation, outcome, amount, currency, cost_source,"
                " provider, model, provider_request_id, prompt_tokens,"
                " completion_tokens, total_tokens, cached_tokens, reasoning_tokens,"
                " evidence, attested, actor_user_id, actor_email, created_at,"
                " updated_at)"
                " VALUES ('rec_hist', 'biz_hist', 'proj_hist', 't_hist', 'res_hist',"
                " 'u_hist', 4, 'attempt:hist', 7, 'charged', 12.34, 'USD',"
                " 'provider', 'openai', 'gpt-4o', 'req_hist', 11, 7, 18, 2, 3,"
                " 'invoice 42', true, NULL, 'auditor@example.com',"
                " '2026-01-02 03:04:05+00', '2026-01-03 04:05:06+00')"
            )
        )
    await engine.dispose()

    command.upgrade(cfg, "0012_no_charge_reconciliation")

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM usage_reconciliations WHERE id = 'rec_hist'")))
            .mappings()
            .one()
        )
    await engine.dispose()

    assert row["id"] == "rec_hist"
    assert row["business_id"] == "biz_hist"
    assert row["project_id"] == "proj_hist"
    assert row["task_id"] == "t_hist"
    assert row["reservation_id"] == "res_hist"
    assert row["usage_record_id"] == "u_hist"
    assert row["attempt"] == 4
    assert row["attempt_key"] == "attempt:hist"
    assert row["execution_generation"] == 7
    assert row["outcome"] == "charged"
    assert row["amount"] == Decimal("12.34")
    assert row["currency"] == "USD"
    assert row["cost_source"] == "provider"
    assert row["provider"] == "openai"
    assert row["model"] == "gpt-4o"
    assert row["provider_request_id"] == "req_hist"
    assert row["prompt_tokens"] == 11
    assert row["completion_tokens"] == 7
    assert row["total_tokens"] == 18
    assert row["cached_tokens"] == 2
    assert row["reasoning_tokens"] == 3
    assert row["evidence"] == "invoice 42"
    assert row["attested"] is True
    assert row["actor_user_id"] is None
    assert row["actor_email"] == "auditor@example.com"
    assert row["created_at"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert row["updated_at"] == datetime(2026, 1, 3, 4, 5, 6, tzinfo=UTC)

    # The new conditional CHECKs are present, and a self-update forces the
    # database to re-evaluate every constraint against the preserved row.
    checks = await _check_constraint_names(mig_db_url, "usage_reconciliations")
    assert {
        "ck_usage_reconciliations_outcome",
        "ck_usage_reconciliations_cost_source",
        "ck_usage_reconciliations_amount",
        "ck_usage_reconciliations_usage_record",
    } <= checks

    engine = create_engine_from_url(mig_db_url)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE usage_reconciliations SET amount = amount WHERE id = 'rec_hist'")
        )
        assert result.rowcount == 1
    await engine.dispose()

    engine = create_engine_from_url(mig_db_url)
    async with engine.connect() as conn:
        still_there = (
            await conn.execute(
                text(
                    "SELECT id, outcome, amount, usage_record_id"
                    " FROM usage_reconciliations WHERE id = 'rec_hist'"
                )
            )
        ).one()
    await engine.dispose()
    assert still_there == ("rec_hist", "charged", Decimal("12.34"), "u_hist")
