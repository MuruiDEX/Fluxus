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
