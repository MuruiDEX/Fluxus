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
    "auth_sessions",
    "memberships",
    "users",
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


OPERATOR_EMAIL = "op@example.com"
OPERATOR_PASSWORD = "correct-horse-1"


async def login_as(client, email: str = OPERATOR_EMAIL, password: str = OPERATOR_PASSWORD) -> str:
    """Log in through the real endpoint; returns the CSRF token.

    The session cookie stays in the client's jar. Raises on failure —
    a broken login must fail the test loudly, not silently unauthenticated.
    """
    res = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


async def seed_operator(session, biz_id: str = "biz_test") -> None:
    """Create the standard test operator + owner membership (committed)."""
    from app.core.passwords import hash_password
    from app.persistence.models.user import User
    from app.persistence.repositories.concrete import MembershipRepository

    session.add(
        User(
            id="u_operator",
            email=OPERATOR_EMAIL,
            password_hash=hash_password(OPERATOR_PASSWORD),
            display_name="Op",
        )
    )
    await session.flush()
    await MembershipRepository().grant(session, "u_operator", biz_id, "owner")
    await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def api_client(test_engine: AsyncEngine):
    """ASGI app bound to the test database, served in the test event loop.

    Starlette TestClient runs its own anyio portal loop, which breaks
    asyncpg futures bound to the pytest loop — httpx ASGITransport stays in
    the same loop, so DB connections work.
    """
    import httpx

    from app.persistence.engine import session_factory

    settings = Settings(environment="test")
    app = create_app(settings)
    app.state.session_factory = session_factory(test_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
