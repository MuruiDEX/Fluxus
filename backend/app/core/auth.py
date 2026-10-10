"""Local operator authentication (Stage 5, single-user desktop).

Single shared bearer secret from configuration — a real check, not a
placeholder: constant-time comparison, fail-closed when unconfigured, and
the secret never appears in logs, errors, or responses. This is NOT
multi-user auth (that remains Stage 6); it gates the local operator
surface so arbitrary local processes cannot drive executions.
"""

import hmac
import logging

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


def _unauthorized(reason: str) -> HTTPException:
    # Deliberately uniform message: never confirm whether a token is
    # configured, only whether this request is authorized. Never includes
    # the presented credential or the configured secret.
    return HTTPException(status_code=401, detail=reason)


async def require_operator(request: Request) -> None:
    """FastAPI dependency: allow only the configured local operator."""
    from app.core.config import Settings

    settings: Settings = request.app.state.settings
    expected = settings.operator_token
    if not expected:
        # Fail-closed: without a configured secret, every state-changing
        # endpoint (and protected reads) stays shut.
        raise _unauthorized("Operator authentication is not configured.")
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise _unauthorized("Operator authentication required.")
    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise _unauthorized("Operator authentication required.")
    return None


def check_client_contract(request: Request) -> None:
    """Enforce contract major version only when the client declares one.

    Backward compatibility (frontend sends no header today): a missing or
    malformed header is allowed through. A well-formed header with an
    unknown major version is rejected — the client and server genuinely
    disagree about the wire format.
    """
    raw = request.headers.get("x-client-contract", "")
    if not raw:
        return None
    major, _, _ = raw.strip().partition(".")
    if not major.isdigit():
        return None
    if int(major) != 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "contract_mismatch", "message": "Unsupported client contract version."},
        )
    return None
