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
from app.api.v1.schemas import DecisionResponse, IntegrationActionReconciliationResponse
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business, require_business_owner
from app.integrations import service
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
    decision: DecisionResponse | JSONResponse
    async with factory() as session:
        async with session.begin():
            external = await service.actions.get_for_approval(session, business_id, approval_id)
            if external is not None and not await service.memberships.lock_owner(
                session, identity.user_id, business_id
            ):
                raise HTTPException(status_code=403, detail="Business owner role required.")
            decided = await _approvals.decide(
                session, business_id, approval_id, to_status=to_status
            )
            if decided:
                approval = await _approvals.get(session, business_id, approval_id)
                if approval is None:  # pragma: no cover - decided implies present
                    # Never assume: roll back instead of recording a stray event.
                    raise HTTPException(status_code=500, detail="Approval state unclear.")
                if external is not None and to_status == "rejected":
                    await service.reject_action(session, external)
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
                decision = DecisionResponse(
                    outcome=to_status,
                    approvalId=approval_id,
                    status=to_status,
                    contract=settings.contract_version,
                )
            else:
                # CAS lost: the row is missing, already decided, or
                # concurrently decided — re-read under scope, never assume.
                current = await _approvals.get(session, business_id, approval_id)
                if current is None:
                    raise HTTPException(status_code=404, detail="Approval not found in scope.")
                if current.status == to_status:
                    decision = DecisionResponse(
                        outcome=f"already_{to_status}",
                        approvalId=approval_id,
                        status=current.status,
                        contract=settings.contract_version,
                    )
                else:
                    payload = DecisionResponse(
                        outcome="conflict",
                        approvalId=approval_id,
                        status=current.status,
                        contract=settings.contract_version,
                    ).model_dump(by_alias=True)
                    # 409: the row exists but is terminal for the other
                    # decision — terminal states never reopen.
                    decision = JSONResponse(status_code=409, content=payload)

    if external is not None and isinstance(decision, DecisionResponse):
        # The decision is committed. A linked external action (Gmail send or
        # draft) may now run; failures are recorded on the action and never
        # roll back the human decision.
        await service.handle_approval_decision(
            factory,
            settings,
            business_id=business_id,
            approval_id=approval_id,
            status=decision.outcome,
            user_id=identity.user_id,
        )
    return decision


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


@router.post(
    "/businesses/{business_id}/approvals/{approval_id}/reconcile-action",
    response_model=IntegrationActionReconciliationResponse,
    dependencies=_GUARD,
)
async def reconcile_approval_action(
    business_id: str,
    approval_id: str,
    request: Request,
    identity: Identity = Depends(require_business_owner),  # noqa: B008
):
    factory = _factory(request)
    async with factory() as session:
        async with session.begin():
            if not await service.memberships.lock_owner(session, identity.user_id, business_id):
                raise HTTPException(status_code=403, detail="Business owner role required.")
            action = await service.reconcile_action(session, business_id, approval_id)
            if action is None:
                raise HTTPException(status_code=404, detail="External action not found in scope.")
            return {
                "approvalId": approval_id,
                "actionId": action.id,
                "status": action.status,
                "contract": _settings(request).contract_version,
            }
