"""Owner bootstrap + password recovery CLI (Stage 6, local operator only).

Runs against the database directly - never over HTTP, never with a browser.
There is no public registration endpoint by design; the first owner account
(and any password recovery) happens here, on the machine that hosts the data.

Usage (from backend/, with .venv active):
  python scripts/bootstrap_owner.py create-owner --email op@example.com [--password ...]
  python scripts/bootstrap_owner.py reset-password --email op@example.com [--password ...]
  python scripts/bootstrap_owner.py list-users
  python scripts/bootstrap_owner.py grant --email op@example.com --business biz_1 [--role owner]

- `--password` omitted -> secure prompt via getpass (never argv, never logs).
- Re-running create-owner for an existing email is a safe no-op for the user
  row (reports "already exists"); use reset-password to change credentials.
- `--business/--business-name` (with create-owner) creates a fresh business
  and grants owner membership explicitly. Pre-existing businesses are NEVER
  auto-attached: orphan data stays invisible until an explicit grant.
- Requires APP_DATABASE_URL to point at a MIGRATED database
  (`alembic upgrade head` first).

Exit codes: 0 ok, 1 usage/data error, 2 operational failure.
"""

import argparse
import asyncio
import getpass
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.passwords import hash_password  # noqa: E402
from app.persistence.engine import create_engine_from_url, session_factory  # noqa: E402
from app.persistence.exceptions import validate_id  # noqa: E402
from app.persistence.repositories.concrete import (  # noqa: E402
    BusinessRepository,
    MembershipRepository,
    UserRepository,
)

DB_URL = os.environ.get("APP_DATABASE_URL", "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")

_users = UserRepository()
_members = MembershipRepository()
_businesses = BusinessRepository()


def _password(args: argparse.Namespace) -> str | None:
    if args.password is not None:
        return args.password
    if not sys.stdin.isatty():
        print("error: --password is required when stdin is not a terminal", file=sys.stderr)
        return None
    first = getpass.getpass("Password: ")
    if not first or len(first) < 8:
        print("error: password must be at least 8 characters", file=sys.stderr)
        return None
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("error: passwords do not match", file=sys.stderr)
        return None
    return first


def _valid_email(raw: str) -> str | None:
    email = raw.strip().lower()
    if "@" not in email or len(email) > 320 or len(email) < 3:
        return None
    return email


async def cmd_create_owner(args: argparse.Namespace) -> int:
    email = _valid_email(args.email or "")
    if email is None:
        print("error: valid --email is required", file=sys.stderr)
        return 1
    password = _password(args)
    if password is None:
        return 1
    engine = create_engine_from_url(DB_URL)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            async with session.begin():
                if await _users.get_by_email(session, email) is not None:
                    print(
                        f"user already exists: {email} (use reset-password to change credentials)"
                    )
                    return 0
                # Validate the attach target BEFORE any write: returning
                # inside `session.begin()` commits, so a late usage error
                # would otherwise persist a membership-less user row.
                if args.business_id:
                    validate_id(args.business_id)
                    if await _businesses.get(session, args.business_id, args.business_id) is None:
                        print(f"error: business not found: {args.business_id}", file=sys.stderr)
                        return 1
                uid = args.user_id or uuid.uuid4().hex
                validate_id(uid)
                await _users.create(
                    session,
                    id=uid,
                    email=email,
                    password_hash=hash_password(password),
                    display_name=args.name or "",
                )
                if args.business_id:
                    await _members.grant(session, uid, args.business_id, "owner")
                    print(f"created owner {email} attached to business {args.business_id}")
                elif args.business_name:
                    bid = uuid.uuid4().hex
                    await _businesses.create(session, bid, {"id": bid, "name": args.business_name})
                    await _members.grant(session, uid, bid, "owner")
                    print(f"created owner {email} with new business {bid}")
                else:
                    print(f"created owner {email} with no business attached")
    except Exception as exc:  # noqa: BLE001 - CLI boundary, message only
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    return 0


async def cmd_reset_password(args: argparse.Namespace) -> int:
    email = _valid_email(args.email or "")
    if email is None:
        print("error: valid --email is required", file=sys.stderr)
        return 1
    password = _password(args)
    if password is None:
        return 1
    engine = create_engine_from_url(DB_URL)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            async with session.begin():
                user = await _users.get_by_email(session, email)
                if user is None:
                    print(f"error: unknown user: {email}", file=sys.stderr)
                    return 1
                await _users.set_password_hash(session, user.id, hash_password(password))
                print(f"password updated for {email} (existing sessions remain valid)")
    except Exception as exc:  # noqa: BLE001 - CLI boundary, message only
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    return 0


async def cmd_list_users(args: argparse.Namespace) -> int:
    _ = args
    from sqlalchemy import select  # noqa: E402

    from app.persistence.models.user import User  # noqa: E402

    engine = create_engine_from_url(DB_URL)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            rows = (await session.execute(select(User.id, User.email))).all()
            if not rows:
                print("no users")
                return 0
            for uid, email in rows:
                links = await _members.businesses_of(session, uid)
                roles = ",".join(f"{m.business_id}:{m.role}" for m in links) or "-"
                print(f"{email}  id={uid}  businesses=[{roles}]")
    except Exception as exc:  # noqa: BLE001 - CLI boundary, message only
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    return 0


async def cmd_grant(args: argparse.Namespace) -> int:
    email = _valid_email(args.email or "")
    if email is None or not args.business:
        print("error: --email and --business are required", file=sys.stderr)
        return 1
    if args.role not in ("owner", "member"):
        print("error: --role must be owner|member", file=sys.stderr)
        return 1
    engine = create_engine_from_url(DB_URL)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            async with session.begin():
                user = await _users.get_by_email(session, email)
                if user is None:
                    print(f"error: unknown user: {email}", file=sys.stderr)
                    return 1
                validate_id(args.business)
                if await _businesses.get(session, args.business, args.business) is None:
                    print(f"error: business not found: {args.business}", file=sys.stderr)
                    return 1
                await _members.grant(session, user.id, args.business, args.role)
                print(f"granted {args.role} on {args.business} to {email}")
    except Exception as exc:  # noqa: BLE001 - CLI boundary, message only
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-owner", help="create the first owner account")
    p_create.add_argument("--email", required=True)
    p_create.add_argument("--password", default=None, help="omit for secure prompt")
    p_create.add_argument("--user-id", default=None)
    p_create.add_argument("--name", default="")
    p_create.add_argument("--business-id", default=None, help="attach to existing business")
    p_create.add_argument("--business-name", default=None, help="or create a fresh business")
    p_create.set_defaults(func=cmd_create_owner)

    p_reset = sub.add_parser("reset-password", help="recover access: set a new password")
    p_reset.add_argument("--email", required=True)
    p_reset.add_argument("--password", default=None, help="omit for secure prompt")
    p_reset.set_defaults(func=cmd_reset_password)

    p_list = sub.add_parser("list-users", help="list users and their memberships")
    p_list.set_defaults(func=cmd_list_users)

    p_grant = sub.add_parser("grant", help="explicitly attach a user to a business")
    p_grant.add_argument("--email", required=True)
    p_grant.add_argument("--business", required=True)
    p_grant.add_argument("--role", default="member")
    p_grant.set_defaults(func=cmd_grant)

    args = parser.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
