"""Execution + task read endpoints (Stage 5).

POST .../executions runs ONE task synchronously through `execute_task` and
returns its outcome. No background jobs, no queues: the HTTP request lives
exactly as long as the bounded execution. A client disconnect cancels the
request; the executor's own CancelledError path then applies (compensate or
propagate вЂ” never silent).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.schemas import ExecutionCreate, ExecutionResponse, TaskResponse
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.execution.executor import ExecutionRequest, execute_task
from app.llm.base import create_provider
from app.persistence.repositories.concrete import TaskRepository

router = APIRouter()
_tasks = TaskRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]

_OUTCOME_HTTP = {
    "completed": 200,
    "failed": 200,
    "already_completed": 200,
    "already_running": 409,
    "refused": None,  # decided by error_code below
    "unknown": 500,
}

_REFUSED_HTTP = {
    "not_found": 404,
    "conflict": 409,
    "budget_exceeded": 409,
    "approval_required": 409,
}


def _task_response(task, contract: str) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        businessId=task.business_id,
        projectId=task.project_id,
        title=task.title,
        ownerAgentId=task.owner_agent_id,
        status=task.status,
        progress=task.progress,
        costAccrued=task.cost_accrued,
        etaMinutes=task.eta_minutes,
        summary=task.summary,
        attempts=task.attempts,
        approvalGateId=task.approval_gate_id,
        gateAt=task.gate_at,
        costRate=task.cost_rate,
        contract=contract,
    )


def _factory(request: Request):
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=500, detail="Persistence is not configured.")
    return factory


def _settings(request: Request):
    return request.app.state.settings


@router.post(
    "/businesses/{business_id}/projects/{project_id}/tasks/{task_id}/executions",
    response_model=ExecutionResponse,
    dependencies=_GUARD,
)
async def run_execution(
    business_id: str,
    project_id: str,
    task_id: str,
    body: ExecutionCreate,
    request: Request,
):
    settings = _settings(request)
    factory = _factory(request)
    timeout_s = body.timeout_s if body.timeout_s is not None else settings.llm_timeout_s
    timeout_s = min(timeout_s, settings.execution_max_timeout_s)
    provider = create_provider(settings)
    async with factory() as session:
        result = await execute_task(
            session,
            provider,
            ExecutionRequest(
                business_id=business_id,
                project_id=project_id,
                task_id=task_id,
                timeout_s=timeout_s,
            ),
        )
    status_code = _OUTCOME_HTTP[result.outcome]
    error = None
    if result.outcome == "refused":
        status_code = _REFUSED_HTTP.get(result.error_code or "", 409)
        error = {"code": result.error_code or "conflict", "message": result.error_message or ""}
    elif result.outcome == "unknown":
        error = {
            "code": result.error_code or "internal",
            "message": result.error_message or "Execution state unknown.",
        }
    elif result.outcome == "already_running":
        error = {"code": "conflict", "message": result.error_message or ""}
    elif result.outcome == "failed":
        error = {"code": result.error_code or "internal", "message": result.error_message or ""}

    payload = ExecutionResponse(
        outcome=result.outcome,
        taskId=result.task_id,
        status=result.status,
        attempts=result.attempts,
        llmAttempts=result.llm_attempts,
        usage=result.usage,
        error=error,
        contract=settings.contract_version,
    ).model_dump(by_alias=True, mode="json")
    return JSONResponse(status_code=status_code, content=payload)
