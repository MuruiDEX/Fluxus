"""Auth endpoints (Stage 6): login / logout / me.

- POST /auth/login {email, password} → 200 sets HttpOnly session cookie and
  returns {user, csrf_token}; unknown email and wrong password are
  indistinguishable (uniform 401); brute force throttled (429).
- POST /auth/logout → requires valid session + CSRF (it mutates server
  state); revokes server-side, clears cookie, always 200 on success.
- GET /auth/me → 200 {user, csrf_token, businesses} or 401. The token is
  re-issued (same value) so a full page load — which wipes the browser's
  in-memory copy — recovers mutation ability without re-login.
- Fresh session id on every login (no fixation); password rehash when params
  evolve. Nothing secret is logged or returned — ever.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.auth import check_client_contract
from app.core.identity import hash_session_id, require_session, require_user
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.rate_limit import LoginThrottle
from app.persistence.repositories.concrete import (
    AuthSessionRepository,
    MembershipRepository,
    UserRepository,
)

log = logging.getLogger(__name__)

router = APIRouter()

_users = UserRepository()
_members = MembershipRepository()
_sessions = AuthSessionRepository()


def _throttle_for(request: Request) -> LoginThrottle:
    throttle = getattr(request.app.state, "login_throttle", None)
    if throttle is None:
        settings = request.app.state.settings
        throttle = LoginThrottle(settings.login_max_attempts, settings.login_window_seconds)
        request.app.state.login_throttle = throttle
    return throttle


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


def _session_cookie(request: Request, raw_id: str, max_age: int) -> dict:
    settings = request.app.state.settings
    return {
        "key": settings.session_cookie_name,
        "value": raw_id,
        "httponly": True,
        "samesite": "lax",
        # Secure only on real https deployments — enabling it blindly would
        # silently break local http (Tauri/dev). Explicit opt-in, never silent.
        "secure": bool(settings.session_cookie_secure),
        "path": "/",
        "max_age": max_age,
    }


def _bad_credentials() -> HTTPException:
    # Identical for unknown email and wrong password.
    return HTTPException(status_code=401, detail="Invalid email or password.")


_dummy_hash: str | None = None


def _verify_for_login(stored_hash: str | None, password: str) -> bool:
    """Password check with constant work across both failure cases.

    Unknown email costs one dummy Argon2 verify, so unknown-email and
    wrong-password logins share the same timing shape and response.
    The dummy hash is process-local and never persisted or returned.
    """
    global _dummy_hash
    if stored_hash is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password(secrets.token_urlsafe(32))
        verify_password(_dummy_hash, password)
        return False
    return verify_password(stored_hash, password)


@router.post("/auth/login", dependencies=[Depends(check_client_contract)])
async def login(body: LoginRequest, request: Request):
    settings = request.app.state.settings
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=500, detail="Persistence is not configured.")
    email = body.email.strip().lower()
    client = request.client.host if request.client else "unknown"
    throttle = _throttle_for(request)

    if not throttle.check(email):
        log.warning("login throttled", extra={"context": {"email": email}})
        raise HTTPException(status_code=429, detail="Too many login attempts.")
    throttle.record(email)

    async with factory() as session:
        user = await _users.get_by_email(session, email)
        ok = _verify_for_login(user.password_hash if user is not None else None, body.password)
        if not ok:
            # Same timing shape and same response either way; nothing logged
            # beyond the fact of a failed attempt (no email/user enumeration).
            log.info("login failed", extra={"context": {"client": client}})
            raise _bad_credentials()
        assert user is not None
        if needs_rehash(user.password_hash):
            await _users.set_password_hash(session, user.id, hash_password(body.password))
        raw_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        ttl = int(settings.session_ttl_seconds)
        await _sessions.create(
            session,
            id_hash=hash_session_id(raw_id),
            user_id=user.id,
            csrf_token=csrf_token,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        links = await _members.businesses_of(session, user.id)
        payload = {
            "user": {"id": user.id, "email": user.email, "displayName": user.display_name},
            "csrf_token": csrf_token,
            "businesses": sorted(m.business_id for m in links),
            "contract": settings.contract_version,
        }
        # Commit BEFORE responding: the cookie must never reference a
        # session row that failed to persist.
        await session.commit()
    response = JSONResponse(status_code=200, content=payload)
    response.set_cookie(**_session_cookie(request, raw_id, ttl))
    return response


@router.post(
    "/auth/logout",
    dependencies=[Depends(check_client_contract), Depends(require_session)],
)
async def logout(request: Request):
    settings = request.app.state.settings
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=500, detail="Persistence is not configured.")
    raw_id = request.cookies.get(settings.session_cookie_name, "")
    if raw_id:
        async with factory() as session:
            await _sessions.revoke(session, hash_session_id(raw_id))
            await session.commit()
    response = JSONResponse(status_code=200, content={"contract": settings.contract_version})
    # Mirror the Secure/SameSite flags used at login: a Secure cookie is
    # only cleared by a Secure clearing cookie in the browser.
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        samesite="lax",
        secure=bool(settings.session_cookie_secure),
    )
    return response


@router.get("/auth/me", dependencies=[Depends(check_client_contract)])
async def me(request: Request):
    from app.core.identity import Identity

    settings = request.app.state.settings
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=500, detail="Persistence is not configured.")
    identity: Identity = await require_user(request)
    async with factory() as session:
        user = await _users.get(session, identity.user_id)
        if user is None:  # user row vanished mid-session → treat as logged out
            raise HTTPException(status_code=401, detail="Authentication required.")
        links = await _members.businesses_of(session, user.id)
        # Re-issue the session-bound CSRF token: the browser keeps it in
        # memory only, so a full page load would otherwise leave an
        # authenticated session with no usable token (every later mutation
        # 403s until re-login). Same value, same session — never rotated here.
        raw_id = request.cookies.get(settings.session_cookie_name, "")
        row = await _sessions.get_valid(session, hash_session_id(raw_id), datetime.now(UTC))
        if row is None:  # pragma: no cover - require_user just validated it
            raise HTTPException(status_code=401, detail="Authentication required.")
    return {
        "user": {"id": user.id, "email": user.email, "displayName": user.display_name},
        "csrf_token": row.csrf_token,
        "businesses": sorted(m.business_id for m in links),
        "contract": settings.contract_version,
    }


# Re-exported for tests that assert the throttle bounds without waiting.
__all__ = ["router", "LoginThrottle", "_throttle_for"]
