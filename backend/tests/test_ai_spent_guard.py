"""ai_spent write guard: the derived cache rejects direct writes."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import ImmutableFieldError
from tests.factories import BIZ, make_world, projects


async def test_ai_spent_rejected_in_update(session: AsyncSession) -> None:
    await make_world(session)
    with pytest.raises(ImmutableFieldError):
        await projects.update(session, BIZ, "proj_1", {"ai_spent": 100})
    # Other fields still writable through the same path.
    updated = await projects.update(session, BIZ, "proj_1", {"progress": 42})
    assert updated is not None and updated.progress == 42
    assert float(updated.ai_spent) == 0.0
