"""Owner bootstrap CLI (Stage 12): no partial writes on usage errors.

Runs the real `scripts/bootstrap_owner.py` entry point against the isolated
test database (never dev, never prod). Covers: failed attach leaves no
user row behind, fresh business creation grants ownership, re-runs are
safe no-ops.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bootstrap_owner

from app.persistence.engine import create_engine_from_url, session_factory
from app.persistence.models.membership import Membership
from app.persistence.models.user import User


async def _user_by_email(test_db_url: str, email: str):
    engine = create_engine_from_url(test_db_url)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            return (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
    finally:
        await engine.dispose()


async def _memberships(test_db_url: str, email: str) -> list:
    engine = create_engine_from_url(test_db_url)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is None:
                return []
            rows = await session.execute(select(Membership).where(Membership.user_id == user.id))
            return list(rows.scalars().all())
    finally:
        await engine.dispose()


def test_failed_attach_leaves_no_user(test_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Usage error must not persist a membership-less user (rollback)."""
    monkeypatch.setattr(bootstrap_owner, "DB_URL", test_db_url)
    code = bootstrap_owner.main(
        [
            "create-owner",
            "--email",
            "clean-fail@example.com",
            "--password",
            "cleanpass123",
            "--business-id",
            "biz_missing",
        ]
    )
    assert code == 1
    import asyncio

    assert asyncio.run(_user_by_email(test_db_url, "clean-fail@example.com")) is None


def test_create_owner_with_fresh_business(
    test_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap_owner, "DB_URL", test_db_url)
    code = bootstrap_owner.main(
        [
            "create-owner",
            "--email",
            "clean-fresh@example.com",
            "--password",
            "cleanpass123",
            "--business-name",
            "Clean Biz",
        ]
    )
    assert code == 0
    import asyncio

    links = asyncio.run(_memberships(test_db_url, "clean-fresh@example.com"))
    assert len(links) == 1 and links[0].role == "owner"


def test_create_owner_rerun_is_noop(test_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap_owner, "DB_URL", test_db_url)
    argv = [
        "create-owner",
        "--email",
        "clean-rerun@example.com",
        "--password",
        "cleanpass123",
    ]
    assert bootstrap_owner.main(argv) == 0
    assert bootstrap_owner.main(argv) == 0
