"""Ensure embedded test databases exist (muse_test, muse_mig)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_URL = "postgresql+asyncpg://muse:muse@127.0.0.1:5433/postgres"


async def main() -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        existing = {
            row[0] for row in (await conn.execute(text("SELECT datname FROM pg_database"))).all()
        }
        for db in ("muse_test", "muse_mig"):
            if db not in existing:
                await conn.execute(text(f'CREATE DATABASE "{db}"'))
                print(f"created {db}")
            else:
                print(f"exists {db}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
