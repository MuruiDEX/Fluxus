"""Create the two dedicated databases on an explicitly selected test server.

The server is never inferred.  Pass a validated base URL for a numeric
loopback server on a non-protected port::

    python scripts/pg_ensure_dbs.py --base-url postgresql://user:pass@127.0.0.1:55434

Only ``muse_test`` and ``muse_mig`` are created.  This script does not start a
server, connect to a default port, or print credentials.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from tests._pg_test_urls import validate_pg_base_url  # noqa: E402

_TEST_DATABASES = ("muse_test", "muse_mig")


def _admin_url(base_url: str) -> str:
    """Return an asyncpg admin URL without changing the validated base."""
    if base_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + base_url[len("postgresql://") :] + "/postgres"
    return base_url + "/postgres"


async def _ensure_databases(admin_url: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            existing = {
                row[0]
                for row in (await conn.execute(text("SELECT datname FROM pg_database"))).all()
            }
            for db in _TEST_DATABASES:
                if db not in existing:
                    await conn.execute(text(f'CREATE DATABASE "{db}"'))
                    print(f"created {db}")
                else:
                    print(f"exists {db}")
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        metavar="URL",
        help=(
            "Explicit numeric loopback PostgreSQL base URL on a non-protected "
            "port, for example postgresql://USER:PASSWORD@127.0.0.1:55434."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        base_url = validate_pg_base_url(args.base_url)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    try:
        asyncio.run(_ensure_databases(_admin_url(base_url)))
    except Exception as exc:  # noqa: BLE001 - keep credentials out of output
        raise SystemExit(f"error: database operation failed ({type(exc).__name__})") from None


if __name__ == "__main__":
    main()
