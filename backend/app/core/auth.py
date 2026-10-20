"""Shared API guards (Stage 5+).

Client-contract gating lives here. Per-request identity lives in
`app.core.identity` (session + membership); the Stage 5 operator bearer
was fully removed in Stage 6 — no fallback auth path exists by design.
"""

from fastapi import HTTPException, Request


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
