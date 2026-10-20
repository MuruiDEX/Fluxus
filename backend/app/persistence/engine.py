"""Async engine factory — the ONLY place a backend engine is constructed.

The URL decides the backend (PostgreSQL primary, SQLite for the portability
check). Repositories only ever see AsyncSession: neither the API nor the
frontend can tell which engine is underneath.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_from_url(
    url: str, *, echo: bool = False, statement_timeout_ms: int | None = None
) -> AsyncEngine:
    kwargs: dict = {"echo": echo, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # File/queue pooling is wrong for SQLite; aiosqlite is single-file.
        kwargs.pop("pool_pre_ping", None)
        engine = create_async_engine(url, echo=echo)
    else:
        if statement_timeout_ms is not None:
            # Bounds every statement server-side: persist/compensate phases
            # and recovery CAS waits fail instead of hanging forever.
            kwargs["connect_args"] = {
                "server_settings": {"statement_timeout": str(int(statement_timeout_ms))}
            }
        engine = create_async_engine(url, pool_size=5, max_overflow=10, **kwargs)
    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
