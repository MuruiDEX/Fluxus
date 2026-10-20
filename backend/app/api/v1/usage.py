"""Usage ledger reads (Stage 16.1, contract v1.18).

Read-only, tenant-scoped exposure of the append-only usage ledger plus the
derived money summary. Unknown cost is surfaced as `null` `costAmount` — never
coerced to zero — while the summary keeps confirmed and estimated money apart
and reports pending budget reservations separately from settled spend.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v1.executions import _factory, _settings
from app.api.v1.reads import _at_iso
from app.api.v1.schemas import (
    UsageCallKindSummary,
    UsageItemResponse,
    UsageListResponse,
    UsageReservationSummary,
    UsageSummary,
)
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.persistence.repositories.concrete import ProjectRepository, TaskRepository
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)

router = APIRouter()
_usage = UsageRepository()
_reservations = BudgetReservationRepository()
_projects = ProjectRepository()
_tasks = TaskRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]

_LIMIT = Query(default=50, ge=1, le=200)
_OFFSET = Query(default=0, ge=0)


def _item(record) -> UsageItemResponse:
    return UsageItemResponse(
        id=record.id,
        businessId=record.business_id,
        projectId=record.project_id,
        taskId=record.task_id,
        agentId=record.agent_id,
        attempt=record.attempt,
        provider=record.provider,
        model=record.model,
        callKind=record.call_kind,
        providerRequestId=record.provider_request_id,
        promptTokens=record.prompt_tokens,
        completionTokens=record.completion_tokens,
        totalTokens=record.total_tokens,
        cachedTokens=record.cached_tokens,
        reasoningTokens=record.reasoning_tokens,
        costAmount=record.cost_amount,
        currency=record.currency,
        costSource=record.cost_source,
        usageStatus=record.usage_status,
        tariffVersion=record.tariff_version,
        occurredAt=_at_iso(record.occurred_at),
    )


@router.get(
    "/businesses/{business_id}/usage",
    response_model=UsageListResponse,
    dependencies=_GUARD,
)
async def list_usage(
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
        # Never leak across tenants: an out-of-scope filter is a uniform 404.
        if project_id is not None:
            project = await _projects.get(session, business_id, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found in scope.")
        if task_id is not None:
            task = await _tasks.get(session, business_id, task_id)
            if task is None or (project_id is not None and task.project_id != project_id):
                raise HTTPException(status_code=404, detail="Task not found in scope.")

        rows = await _usage.list_for_business(
            session,
            business_id,
            project_id=project_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
        total = await _usage.count_for_business(
            session, business_id, project_id=project_id, task_id=task_id
        )
        cost_totals = await _usage.totals_by_status(
            session, business_id, project_id=project_id, task_id=task_id
        )
        kind_totals = await _usage.totals_by_call_kind(
            session, business_id, project_id=project_id, task_id=task_id
        )
        reservation_totals = await _reservations.status_totals(
            session, business_id, project_id=project_id, task_id=task_id
        )

    confirmed = Decimal("0")
    estimated = Decimal("0")
    unknown_count = 0
    if "confirmed" in cost_totals:
        confirmed = cost_totals["confirmed"][0]
    if "estimated" in cost_totals:
        estimated = cost_totals["estimated"][0]
    if "unknown" in cost_totals:
        unknown_count = cost_totals["unknown"][1]

    reservations = UsageReservationSummary(
        active=reservation_totals.get("active", Decimal("0")),
        ambiguous=reservation_totals.get("ambiguous", Decimal("0")),
        consumed=reservation_totals.get("consumed", Decimal("0")),
        released=reservation_totals.get("released", Decimal("0")),
        writtenOff=reservation_totals.get("written_off", Decimal("0")),
    )
    by_call_kind: dict[str, UsageCallKindSummary] = {}
    for kind, statuses in kind_totals.items():
        by_call_kind[kind] = UsageCallKindSummary(
            confirmedCost=statuses.get("confirmed", (Decimal("0"), 0))[0],
            estimatedCost=statuses.get("estimated", (Decimal("0"), 0))[0],
            unknownCount=statuses.get("unknown", (Decimal("0"), 0))[1],
        )
    return UsageListResponse(
        items=[_item(record) for record in rows],
        total=total,
        limit=limit,
        offset=offset,
        summary=UsageSummary(
            confirmedCost=confirmed,
            estimatedCost=estimated,
            unknownCount=unknown_count,
            byCallKind=by_call_kind,
            reservations=reservations,
        ),
        contract=settings.contract_version,
    )
