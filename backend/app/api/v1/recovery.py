"""Stale-working recovery endpoint (Stage 5).

Resets a provably-stale `working` row so a crashed execution does not wedge
a task forever. Safety comes from `TaskRepository.recover_stale`: ONE
conditional UPDATE evaluated by the database (status must be `working` AND
`updated_at` older than the threshold, compared in SQL), plus the audit
event in the SAME transaction. No force flag, no confirm flag: either the
row qualifies and everything is recorded atomically, or nothing is written
at all.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.executions import _factory, _settings
from app.api.v1.schemas import RecoveryResponse
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.persistence.repositories.concrete import ActivityRepository, TaskRepository
from app.usage.reconciliation import reconcile_task_reservations

router = APIRouter()
_tasks = TaskRepository()
_activity = ActivityRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]


@router.post(
    "/businesses/{business_id}/projects/{project_id}/tasks/{task_id}/recover",
    response_model=RecoveryResponse,
    dependencies=_GUARD,
)
async def recover_task(business_id: str, project_id: str, task_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    stale_seconds = float(settings.recovery_stale_minutes * 60)
    async with factory() as session:
        # Scope check first: the CAS below only filters business+id, so a
        # project mismatch must be rejected before anything is written.
        # (No endpoint in this stage mutates project_id, so no TOCTOU here.)
        scoped = await _tasks.get(session, business_id, task_id)
        if scoped is None or scoped.project_id != project_id:
            raise HTTPException(status_code=404, detail="Task not found in scope.")
        # Discard the read transaction (nothing written yet); the CAS below
        # must be top-level, like the executor claim.
        await session.rollback()
        async with session.begin():
            reset_to = await _tasks.recover_stale(
                session, business_id, task_id, stale_seconds=stale_seconds
            )
            if reset_to is None:
                current = await _tasks.get(session, business_id, task_id)
                status = current.status if current else None
                payload = RecoveryResponse(
                    outcome="not_stale",
                    taskId=task_id,
                    status=status,
                    contract=settings.contract_version,
                ).model_dump(by_alias=True)
                # 409: the row exists but does not qualify (live/fresh run or
                # already finished) вЂ” nothing was written.
                return JSONResponse(status_code=409, content=payload)
            task = await _tasks.get(session, business_id, task_id)
            if task is None or task.project_id != project_id:
                # Unreachable in this stage (nothing else writes tasks), but
                # never assume: roll back instead of recording a stray event.
                raise HTTPException(status_code=500, detail="Recovery state unclear.")
            await _activity.append(
                session,
                business_id,
                {
                    "project_id": project_id,
                    "agent_name": "recovery",
                    "kind": "system",
                    "text": (
                        f"Task {task_id} recovered from stale working state (reset to {reset_to})."
                    ),
                },
            )
            # The crashed run may have reached the provider: hold (never drop)
            # its budget reservation so the exposure stays counted.
            await reconcile_task_reservations(
                session,
                business_id,
                task_id,
                ambiguous_release_minutes=settings.usage_ambiguous_release_minutes,
            )
    return RecoveryResponse(
        outcome="recovered", taskId=task_id, status=reset_to, contract=settings.contract_version
    )
