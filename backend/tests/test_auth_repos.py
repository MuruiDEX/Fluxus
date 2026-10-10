"""Auth repositories: users, memberships, sessions (Et2 service layer)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password
from app.persistence.repositories.concrete import (
    AuthSessionRepository,
    MembershipRepository,
    UserRepository,
)
from tests.factories import BIZ, make_business

_users = UserRepository()
_members = MembershipRepository()
_sessions = AuthSessionRepository()


async def test_user_get_by_email_and_password_change(session: AsyncSession) -> None:
    await make_business(session)
    await _users.create(
        session, id="u_1", email="Op@Example.com", password_hash=hash_password("pw-one-pw-one")
    )
    await session.commit()
    # Lookup is exact-match: callers normalize before querying.
    assert await _users.get_by_email(session, "Op@Example.com") is not None
    assert await _users.get_by_email(session, "op@example.com") is None
    assert await _users.set_password_hash(session, "u_1", hash_password("pw-two-pw-two")) is True
    assert await _users.set_password_hash(session, "nope", hash_password("x")) is False


async def test_membership_grant_revoke_has_access(session: AsyncSession) -> None:
    await make_business(session)
    await _users.create(
        session,
        id="u_1",
        email="op@example.com",
        password_hash=hash_password("pw-one-pw-one"),
    )
    await session.commit()

    assert await _members.has_access(session, "u_1", BIZ) is False
    await _members.grant(session, "u_1", BIZ, "owner")
    await session.commit()
    assert await _members.has_access(session, "u_1", BIZ) is True
    assert await _members.has_access(session, "u_1", "biz_ghost") is False
    links = await _members.businesses_of(session, "u_1")
    assert [(m.business_id, m.role) for m in links] == [(BIZ, "owner")]

    assert await _members.revoke(session, "u_1", BIZ) is True
    assert await _members.revoke(session, "u_1", BIZ) is False
    assert await _members.has_access(session, "u_1", BIZ) is False
