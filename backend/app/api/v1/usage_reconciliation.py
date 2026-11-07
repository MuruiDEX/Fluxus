"""Manual ambiguous-reservation reconciliation (Stage 18 S2.2.4.6 slice 4).

Read surface: any authenticated member of the path business can list and
inspect ambiguous *task-execution* reservations. Taskless Team Lead/project
holds and every other tenant are excluded by construction.

Write surface: business owners only. The endpoint confirms an ACTUAL provider
charge; it never releases a hold or records a no-charge outcome, never calls the
provider, and never writes a second charge.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v1.executions import _factory, _settings
from app.api.v1.reads import _at_iso
from app.api.v1.schemas import (
    AmbiguousReservationDetailResponse,
    AmbiguousReservationListResponse,
    AmbiguousReservationResponse,
    ConfirmChargeRequest,
    ConfirmChargeResponse,
)
from app.api.v1.usage import _item as _usage_item
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business, require_business_owner
from app.persistence.enums import ReservationStatus
from app.persistence.repositories.concrete import (
    ProjectRepository,
    TaskRepository,
    UserRepository,
)
from app.usage.manual_reconciliation import (
    ChargeCommand,
    ReconciliationConflict,
    ReconciliationInvalid,
    ReconciliationNotFound,
    confirm_provider_charge,
    get_ambiguous,
    list_ambiguous,
)

router = APIRouter()
_projects = ProjectRepository()
_tasks = TaskRepository()
_users = UserRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]

_LIMIT = Query(default=50, ge=1, le=200)
_OFFSET = Query(default=0, ge=0)


def _reservation_item(reservation) -> AmbiguousReservationResponse:
    return AmbiguousReservationResponse(
        id=reservation.id,
        businessId=reservation.business_id,
        projectId=reservation.project_id,
        taskId=reservation.task_id,
        attempt=reservation.attempt,
        attemptKey=reservation.attempt_key,
        executionGeneration=reservation.execution_generation,
        runSeq=reservation.run_seq,
        amount=reservation.amount,
        currency=reservation.currency,
        status=reservation.status,
        reason=reservation.reason,
        mode=reservation.mode,
        createdAt=_at_iso(reservation.created_at),
        updatedAt=_at_iso(reservation.updated_at),
    )


async def _actor_email(session, identity: Identity) -> str:
    user = await _users.get(session, identity.user_id)
    return user.email if user is not None else identity.user_id


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "not_found",
            "message": "Ambiguous reservation not found in scope.",
        },
    )


@router.get(
    "/businesses/{business_id}/usage/ambiguous-reservations",
    response_model=AmbiguousReservationListResponse,
    dependencies=_GUARD,
)
async def list_ambiguous_reservations(
    business_id: str,
    request: Request,
    project_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = _LIMIT,
    offset: int = _OFFSET,
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        # Out-of-scope filters are a uniform 404, like the usage list.
        if project_id is not None:
            if await _projects.get(session, business_id, project_id) is None:
                raise HTTPException(status_code=404, detail="Project not found in scope.")
        if task_id is not None:
            task = await _tasks.get(session, business_id, task_id)
            if task is None or (project_id is not None and task.project_id != project_id):
                raise HTTPException(status_code=404, detail="Task not found in scope.")

        rows, total = await list_ambiguous(
            session,
            business_id,
            project_id=project_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
        return AmbiguousReservationListResponse(
            items=[_reservation_item(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/usage/ambiguous-reservations/{reservation_id}",
    response_model=AmbiguousReservationDetailResponse,
    dependencies=_GUARD,
)
async def get_ambiguous_reservation(
    business_id: str,
    reservation_id: str,
    request: Request,
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        found = await get_ambiguous(session, business_id, reservation_id)
        if found is None:
            raise _not_found()
        reservation, usage_rows = found
        base = _reservation_item(reservation)
        return AmbiguousReservationDetailResponse(
            **base.model_dump(by_alias=False),
            usage=[_usage_item(row) for row in usage_rows],
            contract=settings.contract_version,
        )


@router.post(
    "/businesses/{business_id}/usage/ambiguous-reservations/{reservation_id}/confirm-charge",
    response_model=ConfirmChargeResponse,
    dependencies=_GUARD,
)
async def confirm_charge(
    business_id: str,
    reservation_id: str,
    body: ConfirmChargeRequest,
    request: Request,
    identity: Identity = Depends(require_business_owner),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    command = ChargeCommand(
        task_id=body.task_id,
        attempt_key=body.attempt_key,
        execution_generation=body.execution_generation,
        provider=body.provider,
        model=body.model,
        amount=body.amount,
        currency=body.currency,
        provider_request_id=body.provider_request_id,
        prompt_tokens=body.prompt_tokens,
        completion_tokens=body.completion_tokens,
        total_tokens=body.total_tokens,
        cached_tokens=body.cached_tokens,
        reasoning_tokens=body.reasoning_tokens,
        evidence=body.evidence,
    )
    try:
        async with factory() as session:
            async with session.begin():
                actor_email = await _actor_email(session, identity)
                result = await confirm_provider_charge(
                    session,
                    business_id,
                    reservation_id,
                    command,
                    actor_user_id=identity.user_id,
                    actor_email=actor_email,
                )
                reservation = result.reservation
                record = result.usage_record
                reconciliation = result.reconciliation
                return ConfirmChargeResponse(
                    outcome="reconciled",
                    reservationId=reservation.id,
                    status=ReservationStatus.RECONCILED.value,
                    usageRecordId=record.id,
                    reconciliationId=reconciliation.id,
                    taskId=reservation.task_id,
                    projectId=reservation.project_id,
                    attemptKey=reservation.attempt_key,
                    executionGeneration=reservation.execution_generation,
                    amount=reconciliation.amount,
                    currency=reconciliation.currency,
                    costSource=reconciliation.cost_source,
                    provider=reconciliation.provider,
                    model=reconciliation.model,
                    providerRequestId=reconciliation.provider_request_id,
                    actorEmail=reconciliation.actor_email,
                    reconciledAt=_at_iso(record.occurred_at),
                    taskCost=result.task_cost,
                    projectSpend=result.project_spend,
                    contract=settings.contract_version,
                )
    except ReconciliationNotFound as exc:
        raise _not_found() from exc
    except ReconciliationConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except ReconciliationInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": str(exc)},
        ) from exc
