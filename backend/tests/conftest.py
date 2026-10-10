"""Shared pytest fixtures — TestClient + isolated async Postgres.

Isolation strategy: one truncate-all (CASCADE) before each test on the
dedicated `muse_test` database. The migration suite uses its own `muse_mig`
database and never touches test data. If nothing listens on 5433 (Docker
`postgres-test`), an embedded PostgreSQL is started automatically
(tests/_pg_embedded.py) — same engine, zero system install.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.persistence.base import Base
from app.persistence.engine import create_engine_from_url, session_factory
from tests._pg_embedded import ensure_postgres

TABLES = (
    "task_dependencies",
    "activity_events",
    "approvals",
    "tasks",
    "brain_entries",
    "marketplace_listings",
    "projects",
    "agents",
    "businesses",
)


@pytest.fixture(scope="session")
def pg_base_url() -> str:
    return ensure_postgres()


@pytest.fixture(scope="session")
def test_db_url(pg_base_url: str) -> str:
    return pg_base_url.replace("postgresql://", "postgresql+asyncpg://") + "/muse_test"


@pytest.fixture(scope="session")
def mig_db_url(pg_base_url: str) -> str:
    return pg_base_url.replace("postgresql://", "postgresql+asyncpg://") + "/muse_mig"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine(test_db_url: str) -> AsyncEngine:
    engine = create_engine_from_url(test_db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(test_engine: AsyncEngine) -> AsyncSession:
    async with test_engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"TRUNCATE {table} CASCADE"))
    factory = session_factory(test_engine)
    async with factory() as sess:
        yield sess
        await sess.rollback()


@pytest.fixture()
def client() -> TestClient:
    settings = Settings(environment="test")
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=False)


OPERATOR_TOKEN = "test-operator-token"


def auth_headers(token: str = OPERATOR_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(loop_scope="session")
async def api_client(test_engine: AsyncEngine):
    """ASGI app bound to the test database, served in the test event loop.

    Starlette TestClient runs its own anyio portal loop, which breaks
    asyncpg futures bound to the pytest loop — httpx ASGITransport stays in
    the same loop, so DB connections work.
    """
    import httpx

    from app.persistence.engine import session_factory

    settings = Settings(environment="test", operator_token=OPERATOR_TOKEN)
    app = create_app(settings)
    app.state.session_factory = session_factory(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
