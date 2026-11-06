"""Stale/expired-working recovery endpoint (Stage 5, Stage 18 S2.2.4.4).

Makes a crashed execution safely claimable again. There are two disjoint
paths, both decided by the database:

- lease-bearing rows: an expired lease is taken over atomically
  (`TaskRepository.takeover_expired`), the generation is bumped and a new
  owner/lease is installed; the new token then fences the row back to the
  existing claimable state. A live lease or a passed absolute deadline is
  refused without writes.
- legacy rows with no lease keep the original `updated_at` clock
  (`TaskRepository.recover_stale`).

The audit event and the pre-existing reservation reconciliation run in the
SAME transaction as a successful recovery. Refusal paths write nothing.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.executions import _factory, _settings
from app.api.v1.schemas import RecoveryResponse
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.execution.recovery import (
    MODE_LEASE_TAKEOVER,
    NOT_FOUND,
    RECOVERED,
)
from app.execution.recovery import (
    recover_task as recover_task_ownership,
)
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
            outcome = await recover_task_ownership(
                session,
                business_id,
                project_id,
                task_id,
                recovery_owner=f"recovery:{uuid.uuid4().hex}",
                lease_ttl_seconds=settings.execution_lease_ttl_s,
                lease_wall_seconds=settings.execution_lease_wall_s,
                stale_seconds=stale_seconds,
            )
            if outcome.outcome != RECOVERED:
                payload = RecoveryResponse(
                    outcome=outcome.outcome,
                    taskId=task_id,
                    status=outcome.status,
                    contract=settings.contract_version,
                ).model_dump(by_alias=True)
                if outcome.outcome == NOT_FOUND:
                    raise HTTPException(status_code=404, detail="Task not found in scope.")
                # 409: the row exists but does not qualify (live lease, passed
                # deadline, inconsistent state, lost race, or a finished run).
                return JSONResponse(status_code=409, content=payload)
            if outcome.mode == MODE_LEASE_TAKEOVER:
                text = (
                    f"Task {task_id} recovered from expired lease "
                    f"(generation {outcome.generation}, reset to {outcome.status})."
                )
            else:
                text = (
                    f"Task {task_id} recovered from stale working state "
                    f"(reset to {outcome.status})."
                )
            await _activity.append(
                session,
                business_id,
                {
                    "project_id": project_id,
                    "agent_name": "recovery",
                    "kind": "system",
                    "text": text,
                },
            )
            # The crashed run may have reached the provider: hold (never drop)
            # its budget reservation so the exposure stays counted. On a lease
            # takeover only NULL/older-generation holds are reconciled; the new
            # generation's holds must remain untouched (S2.2.4.5). Ambiguous
            # holds are never released by age (S2.2.4.6).
            await reconcile_task_reservations(
                session,
                business_id,
                task_id,
                before_generation=(
                    outcome.generation if outcome.mode == MODE_LEASE_TAKEOVER else None
                ),
            )
    return RecoveryResponse(
        outcome="recovered",
        taskId=task_id,
        status=outcome.status,
        contract=settings.contract_version,
    )
