"""Alembic environment — async engine, URL from APP_DATABASE_URL.

Dev default points at the docker-compose postgres. Tests override the URL
per-database (see tests/test_migrations.py) and never touch dev data.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.persistence.base import Base

# Import every model so metadata is complete for autogenerate/offline use.
from app.persistence.models import (  # noqa: F401
    activity_event,
    agent,
    approval,
    auth_session,
    brain_entry,
    brief,
    business,
    marketplace_listing,
    membership,
    pack,
    project,
    task,
    user,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DEFAULT_URL = "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse"


def get_url() -> str:
    url = os.environ.get("APP_DATABASE_URL", DEFAULT_URL)
    # Alembic runs async here: a bare postgresql:// URL means asyncpg.
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    context.configure(url=get_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def _run_online_sync() -> None:
    """Run the async migration in a fresh loop.

    Alembic entry points are synchronous. When invoked from inside a running
    loop (pytest-asyncio), asyncio.run() is illegal, so the migration runs
    on a helper thread with its own loop instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_migrations_online())
        return
    import threading

    failure: list[BaseException] = []

    def _target() -> None:
        try:
            asyncio.run(run_migrations_online())
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            failure.append(exc)

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout=300)
    if failure:
        raise failure[0]


if context.is_offline_mode():
    run_migrations_offline()
else:
    _run_online_sync()
