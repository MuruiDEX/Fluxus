"""Request-scoped session dependency (used by API routers from Stage 5).

One AsyncSession per request, committed on success and rolled back on error
by the caller (`async with session.begin()` pattern inside services).
Stage 2 tests manage their own sessions; this dependency only wires FastAPI.
"""

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Depends(get_session)
