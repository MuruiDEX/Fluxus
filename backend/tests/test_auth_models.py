"""Auth tables: constraints, cascades, no business-data coupling (Et1)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.auth_session import AuthSession
from app.persistence.models.membership import Membership
from app.persistence.models.user import User
from tests.factories import BIZ, make_business


async def _user(session: AsyncSession, uid: str = "u_1", email: str = "op@example.com") -> User:
    obj = User(id=uid, email=email, password_hash="argon2id$placeholder", display_name="Op")
    session.add(obj)
    await session.flush()
    return obj


async def test_user_email_unique(session: AsyncSession) -> None:
    await _user(session)
    with pytest.raises(IntegrityError):
        await _user(session, uid="u_2", email="op@example.com")


async def test_membership_role_check_and_composite_pk(session: AsyncSession) -> None:
    await make_business(session)
    await _user(session)
    session.add(Membership(user_id="u_1", business_id=BIZ, role="owner"))
    await session.flush()
    with pytest.raises(IntegrityError):
        dup = Membership(user_id="u_1", business_id=BIZ, role="member")
        session.add(dup)
        await session.flush()
    await session.rollback()
    with pytest.raises(IntegrityError):
        bad = Membership(user_id="u_1", business_id=BIZ, role="superadmin")
        session.add(bad)
        await session.flush()


async def test_membership_cascades_never_touch_businesses(session: AsyncSession) -> None:
    await make_business(session)
    await _user(session, "u_9", "gone@example.com")
    session.add(Membership(user_id="u_9", business_id=BIZ, role="member"))
    await session.flush()

    from app.persistence.models.business import Business

    await session.delete(await session.get(Business, BIZ))
    await session.flush()

    remaining = (await session.execute(select(Membership))).scalars().all()
    assert remaining == []
    assert await session.get(User, "u_9") is not None  # user survives business deletion
    assert await session.get(Business, BIZ) is None


async def test_session_row_roundtrip(session: AsyncSession) -> None:
    await _user(session)
    now = datetime.now(UTC)
    obj = AuthSession(
        id="sess_1",
        user_id="u_1",
        csrf_token="csrf-placeholder",
        created_at=now,
        expires_at=now + timedelta(hours=12),
    )
    session.add(obj)
    await session.flush()
    assert (await session.get(AuthSession, "sess_1")) is not None
