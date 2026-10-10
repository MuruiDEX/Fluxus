"""Request identity: session cookie → user (+ CSRF for mutations).

Single trust boundary for every domain operation: resolve the session id
from the HttpOnly cookie, look it up BY HASH, reject missing/revoked/
expired rows, and hand the caller (user_id, set(business_ids)). Repos stay
unchanged — callers pass an already-authorized business_id, and every
existing scope filter keeps applying underneath.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Identity:
    user_id: str
    business_ids: frozenset[str]


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentication required.")


def _forbidden_csrf() -> HTTPException:
    return HTTPException(status_code=403, detail="CSRF validation failed.")


def hash_session_id(raw_id: str) -> str:
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


async def resolve_identity(request: Request) -> Identity:
    """Authenticate the request cookie and authorize nothing yet.

    Raises 401 for: no cookie, unknown session, revoked session, expired
    session, missing factory. Never distinguishes these cases to the client.
    """
    from app.persistence.repositories.concrete import MembershipRepository

    row = await _valid_session_row(request)
    if row is None:
        raise _unauthorized()
    members = MembershipRepository()
    factory = getattr(request.app.state, "session_factory", None)
    assert factory is not None  # checked inside _valid_session_row
    async with factory() as session:
        links = await members.businesses_of(session, row.user_id)
    return Identity(user_id=row.user_id, business_ids=frozenset(m.business_id for m in links))


async def _valid_session_row(request: Request):
    """Shared session lookup: valid row or None (never raises auth errors)."""
    from app.persistence.repositories.concrete import AuthSessionRepository

    settings = request.app.state.settings
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=500, detail="Persistence is not configured.")
    raw_id = request.cookies.get(settings.session_cookie_name, "")
    if not raw_id:
        return None
    sessions = AuthSessionRepository()
    async with factory() as session:
        return await sessions.get_valid(session, hash_session_id(raw_id), datetime.now(UTC))


async def require_user(request: Request) -> Identity:
    """Dependency: authenticated caller identity (any membership, even empty)."""
    return await resolve_identity(request)


async def require_session(request: Request) -> Identity:
    """Dependency: valid session + CSRF check for state-changing methods.

    Safe methods (GET/HEAD/OPTIONS) skip the CSRF check — there is nothing
    to forge. Unsafe methods require the session-bound `X-CSRF-Token`
    header, compared in constant time. A missing/invalid session is 401
    either way; a session without a matching token on mutation is 403.
    """
    row = await _valid_session_row(request)
    if row is None:
        raise _unauthorized()
    if request.method in _UNSAFE_METHODS:
        presented = request.headers.get("x-csrf-token", "")
        if not presented or not hmac.compare_digest(presented, row.csrf_token):
            raise _forbidden_csrf()
    from app.persistence.repositories.concrete import MembershipRepository

    factory = getattr(request.app.state, "session_factory", None)
    assert factory is not None
    async with factory() as session:
        members = MembershipRepository()
        links = await members.businesses_of(session, row.user_id)
    return Identity(
        user_id=row.user_id,
        business_ids=frozenset(m.business_id for m in links),
    )


async def require_business(request: Request) -> Identity:
    """Dependency: identity PLUS current membership in the path business.

    The business_id comes ONLY from the URL path and is checked against the
    caller's live membership set on every request. Unknown business and
    foreign business are indistinguishable (uniform 404) — existence of
    other tenants is never confirmed. State-changing methods additionally
    require the session-bound `X-CSRF-Token` header (403 otherwise): the
    cookie alone never authorizes a mutation.
    """
    row = await _valid_session_row(request)
    if row is None:
        raise _unauthorized()
    if request.method in _UNSAFE_METHODS:
        presented = request.headers.get("x-csrf-token", "")
        if not presented or not hmac.compare_digest(presented, row.csrf_token):
            raise _forbidden_csrf()
    from app.persistence.repositories.concrete import MembershipRepository

    factory = getattr(request.app.state, "session_factory", None)
    assert factory is not None  # checked inside _valid_session_row
    async with factory() as session:
        members = MembershipRepository()
        links = await members.businesses_of(session, row.user_id)
    business_ids = frozenset(m.business_id for m in links)
    business_id = str(request.path_params.get("business_id", ""))
    if not business_id or business_id not in business_ids:
        raise HTTPException(status_code=404, detail="Business not found in scope.")
    return Identity(user_id=row.user_id, business_ids=business_ids)
