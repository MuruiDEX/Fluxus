"""Minimal reproduction: single-flush of FK-related rows across tables.

Diagnostic only (NOT a product path). Run: python scripts/repro_flush_anomaly.py
Requires an explicit REPRO_DATABASE_URL for the isolated numeric-loopback test
server (``/muse_test`` only) and APP_ALLOW_REPRO=1. Remote hosts are not
supported: the URL must be numeric loopback before anything connects. There is
no default. It creates and drops scratch tables, so point it at the isolated
``muse_test`` database, never production.

Observed (2026-10-09, PG16 embedded, SQLAlchemy 2.1.4 + asyncpg):
- business+agent (or any parent+child pair) added, ONE flush() → only the
  child INSERT is emitted → ForeignKeyViolationError; parent never attempted.
- Same pair flushed sequentially (two flush calls) → both succeed.
- Same single-flush scenario on SQLite → succeeds with correct order.
"""

import asyncio
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import ForeignKey, String  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # noqa: E402

from app.core.script_safety import UnsafeScriptError, guard_development_script  # noqa: E402
from tests._pg_test_urls import validate_pg_base_url  # noqa: E402


class B(DeclarativeBase):
    pass


class P(B):
    __tablename__ = "repro_parent"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)


class C(B):
    __tablename__ = "repro_child"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pid: Mapped[str] = mapped_column(String(64), ForeignKey("repro_parent.id"))


async def scenario(engine_url: str, label: str, sequential: bool) -> str:
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = create_async_engine(engine_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(B.metadata.drop_all)
        await conn.run_sync(B.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                if sequential:
                    session.add(P(id="p1"))
                    await session.flush()
                    session.add(C(id="c1", pid="p1"))
                    await session.flush()
                else:
                    session.add(P(id="p1"))
                    session.add(C(id="c1", pid="p1"))
                    await session.flush()
        outcome = "OK"
    except Exception as exc:  # noqa: BLE001 - outcome reporting only
        outcome = f"{type(exc).__name__}"
    await engine.dispose()
    print(f"{label}: single_flush={not sequential} -> {outcome}", flush=True)
    return outcome


def _explicit_target() -> str:
    """Return a validated explicit ``muse_test`` URL without echoing it."""
    raw = os.environ.get("REPRO_DATABASE_URL")
    if not raw:
        raise SystemExit(
            "error: REPRO_DATABASE_URL is required; point it at the isolated "
            "127.0.0.1:55434/muse_test test database"
        )

    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise SystemExit("error: REPRO_DATABASE_URL is not a valid URL") from None
    # Literal delimiters count even when they parse as an empty query/fragment.
    if parsed.query or parsed.fragment or "?" in raw or "#" in raw:
        raise SystemExit("error: REPRO_DATABASE_URL must not include a query or fragment")
    if parsed.path != "/muse_test":
        raise SystemExit("error: REPRO_DATABASE_URL must target the /muse_test database")

    try:
        validate_pg_base_url(f"{parsed.scheme}://{parsed.netloc}")
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None
    return raw


async def main() -> None:
    pg = _explicit_target()
    try:
        guard_development_script(
            "repro_flush_anomaly",
            pg,
            opt_in_env="APP_ALLOW_REPRO",
            allow_remote_env="APP_ALLOW_REMOTE_REPRO",
        )
    except UnsafeScriptError as exc:
        raise SystemExit(f"error: {exc}") from exc
    single = await scenario(pg, "postgres", sequential=False)
    seq = await scenario(pg, "postgres", sequential=True)
    print(f"SUMMARY postgres single-flush={single} sequential={seq}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
