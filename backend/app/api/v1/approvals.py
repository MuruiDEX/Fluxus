"""Approval decision endpoints (Stage 7.6).

POST .../approvals/{approval_id}/approve (or /reject) records a human
decision on a pending approval. Safety comes from
`ApprovalRepository.decide`: ONE conditional UPDATE (row must be `pending`
in the caller's business) plus the audit event in the SAME transaction —
either the decision lands with its audit trail, or nothing is written at
all. The linked task is deliberately NOT touched: the executor re-reads
the gate live on every run (`approved` lets it proceed, anything else
refuses with `approval_required`), so mutating the task here could only
corrupt claim semantics.

Idempotency: repeating the same decision is success 200
(`already_approved` / `already_rejected`). The opposite decision after a
terminal state is 409 `conflict` — terminal states never reopen.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.executions import _factory, _settings
from app.api.v1.schemas import DecisionResponse
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business
from app.persistence.repositories.concrete import (
    ActivityRepository,
    ApprovalRepository,
    UserRepository,
)

router = APIRouter()
_approvals = ApprovalRepository()
_activity = ActivityRepository()
_users = UserRepository()

# require_business is declared once, as a parameter: FastAPI evaluates it a
# single time per request (dependency cache), so enforcement is identical to
# the sibling routers — but here the handler also needs the Identity value
# to attribute the audit event to the deciding user.
_GUARD = [Depends(check_client_contract)]


async def _decide(
    business_id: str,
    approval_id: str,
    to_status: str,
    identity: Identity,
    request: Request,
) -> DecisionResponse | JSONResponse:
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        async with session.begin():
            decided = await _approvals.decide(
                session, business_id, approval_id, to_status=to_status
            )
            if decided:
                approval = await _approvals.get(session, business_id, approval_id)
                if approval is None:  # pragma: no cover - decided implies present
                    # Never assume: roll back instead of recording a stray event.
                    raise HTTPException(status_code=500, detail="Approval state unclear.")
                user = await _users.get(session, identity.user_id)
                actor = user.email if user is not None else identity.user_id
                await _activity.append(
                    session,
                    business_id,
                    {
                        "project_id": approval.project_id,
                        "agent_name": actor,
                        "kind": "approval",
                        "text": f"Approval {approval_id} {to_status} by {actor}.",
                    },
                )
                return DecisionResponse(
                    outcome=to_status,
                    approvalId=approval_id,
                    status=to_status,
                    contract=settings.contract_version,
                )
        # CAS lost: the row is missing, already decided, or concurrently
        # decided — re-read under scope, never assume.
        current = await _approvals.get(session, business_id, approval_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Approval not found in scope.")
        if current.status == to_status:
            return DecisionResponse(
                outcome=f"already_{to_status}",
                approvalId=approval_id,
                status=current.status,
                contract=settings.contract_version,
            )
        payload = DecisionResponse(
            outcome="conflict",
            approvalId=approval_id,
            status=current.status,
            contract=settings.contract_version,
        ).model_dump(by_alias=True)
        # 409: the row exists but is terminal for the other decision —
        # terminal states never reopen.
        return JSONResponse(status_code=409, content=payload)


@router.post(
    "/businesses/{business_id}/approvals/{approval_id}/approve",
    response_model=DecisionResponse,
    dependencies=_GUARD,
)
async def approve_approval(
    business_id: str,
    approval_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    return await _decide(business_id, approval_id, "approved", identity, request)


@router.post(
    "/businesses/{business_id}/approvals/{approval_id}/reject",
    response_model=DecisionResponse,
    dependencies=_GUARD,
)
async def reject_approval(
    business_id: str,
    approval_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    return await _decide(business_id, approval_id, "rejected", identity, request)
