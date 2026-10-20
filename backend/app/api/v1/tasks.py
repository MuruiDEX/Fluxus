"""Task read endpoint (Stage 5) вЂ” the single justified read exception.

Returns the status and result (summary) of one task, strictly within the
path scope. No writes, no side effects. Protected like everything else:
operator auth + contract header apply.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.schemas import TaskResponse
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.persistence.repositories.concrete import TaskRepository

router = APIRouter()
_tasks = TaskRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]


@router.get(
    "/businesses/{business_id}/projects/{project_id}/tasks/{task_id}",
    response_model=TaskResponse,
    dependencies=_GUARD,
)
async def read_task(business_id: str, project_id: str, task_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        task = await _tasks.get(session, business_id, task_id)
        if task is None or task.project_id != project_id:
            # Uniform 404: never distinguish "missing" from "foreign scope".
            raise HTTPException(status_code=404, detail="Task not found in scope.")
        (response,) = await _task_responses(session, business_id, [task], settings.contract_version)
        return response
