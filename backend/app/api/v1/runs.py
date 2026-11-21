"""Explicit project run controls. This module does not execute tasks."""

from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.executions import _factory, _settings
from app.api.v1.schemas import ProjectRunResponse, ResumeRunCommand, RunCommand
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business
from app.persistence.exceptions import new_id
from app.persistence.models.approval import Approval
from app.persistence.models.project_run import ProjectRun, RunState
from app.persistence.models.task import Task
from app.persistence.repositories.concrete import (
    ActivityRepository,
    BriefRepository,
    MembershipRepository,
    ProjectRepository,
    TaskRepository,
)
from app.persistence.repositories.project_runs import (
    get_project_run,
    has_working_task,
    lock_project_admission,
)

router = APIRouter(dependencies=[Depends(check_client_contract)])
Actor = Annotated[Identity, Depends(require_business)]
Action = Literal["start", "pause", "resume", "cancel"]
_PATH = "/businesses/{business_id}/projects/{project_id}/run"
_TRANSITIONS = {
    RunState.RUNNING: {"pause": RunState.PAUSED, "cancel": RunState.CANCELLED},
    RunState.WAITING_APPROVAL: {"pause": RunState.PAUSED, "cancel": RunState.CANCELLED},
    RunState.PAUSED: {"resume": RunState.RUNNING, "cancel": RunState.CANCELLED},
    RunState.NEEDS_ATTENTION: {"resume": RunState.RUNNING, "cancel": RunState.CANCELLED},
}


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"code": code, "message": message})


async def _require_project(session: AsyncSession, business_id: str, project_id: str) -> None:
    if await ProjectRepository().get(session, business_id, project_id) is None:
        raise HTTPException(404, detail="Project not found in scope.")


def _view(
    run: ProjectRun | None, actor_id: str, role: str, contract: str, *, can_start: bool = False
) -> ProjectRunResponse:
    if run is None:
        return ProjectRunResponse(
            state="idle",
            version=0,
            scheduling_mode="manual",
            allowed_actions=["start"] if can_start else [],
            contract=contract,
        )
    controls = role == "owner" or run.authorizing_user_id == actor_id
    return ProjectRunResponse(
        run_id=run.id,
        state=run.state,
        version=run.version,
        scheduling_mode="managed",
        reason_code=run.reason_code,
        blocker_id=run.blocker_id,
        active_task_id=run.active_task_id,
        active_task_generation=run.active_task_generation,
        allowed_actions=list(_TRANSITIONS.get(run.state, {})) if controls else [],
        updated_at=run.updated_at,
        contract=contract,
    )


async def _validate_plan(session: AsyncSession, business_id: str, project_id: str) -> None:
    brief = await BriefRepository().get_for_project(session, business_id, project_id)
    if brief is None or brief.status != "planned":
        raise _conflict("run_plan_required", "A planned brief is required.")
    if await has_working_task(session, business_id, project_id):
        raise _conflict("run_task_in_flight", "Wait for the active task to settle or recover it.")
    tasks = (
        await session.execute(
            select(Task.id, Task.approval_gate_id).where(
                Task.business_id == business_id,
                Task.project_id == project_id,
            )
        )
    ).all()
    if not tasks:
        raise _conflict("run_plan_required", "The project has no tasks.")
    task_ids = {row.id for row in tasks}
    edges = await TaskRepository().dependency_map(session, list(task_ids))
    if any(not task_ids.issuperset(deps) for deps in edges.values()):
        raise _conflict("run_invalid_plan", "A dependency is outside the project.")
    try:
        TopologicalSorter({task_id: edges.get(task_id, ()) for task_id in task_ids}).prepare()
    except CycleError as exc:
        raise _conflict("run_invalid_plan", "The task graph contains a cycle.") from exc
    gate_ids = {row.approval_gate_id for row in tasks if row.approval_gate_id is not None}
    if gate_ids:
        gates = await session.scalars(
            select(Approval.id).where(
                Approval.business_id == business_id,
                Approval.project_id == project_id,
                Approval.id.in_(gate_ids),
            )
        )
        if set(gates) != gate_ids:
            raise _conflict("run_invalid_plan", "An approval gate is outside the project.")


@router.get(_PATH, response_model=ProjectRunResponse)
async def read_run(business_id: str, project_id: str, request: Request, identity: Actor):
    async with _factory(request)() as session:
        await _require_project(session, business_id, project_id)
        run = await get_project_run(session, business_id, project_id)
        can_start = False
        if run is None:
            brief = await BriefRepository().get_for_project(session, business_id, project_id)
            can_start = brief is not None and brief.status == "planned"
        role = "owner" if business_id in identity.owner_business_ids else "member"
        return _view(
            run, identity.user_id, role, _settings(request).contract_version, can_start=can_start
        )


async def _command(
    action: Action,
    business_id: str,
    project_id: str,
    request: Request,
    identity: Identity,
    command: RunCommand,
    *,
    assume_responsibility: bool = False,
) -> ProjectRunResponse:
    actor_id = identity.user_id
    contract = _settings(request).contract_version
    async with _factory(request)() as session, session.begin():
        await lock_project_admission(session, business_id, project_id)
        role = await MembershipRepository().lock_role(session, actor_id, business_id)
        if role is None:
            raise HTTPException(404, detail="Business not found in scope.")
        await _require_project(session, business_id, project_id)
        run = await get_project_run(session, business_id, project_id)
        previous_actor = run.authorizing_user_id if run is not None else None
        previous_state = run.state if run is not None else "idle"
        if action == "start":
            if run is not None:
                if (
                    command.expected_version == 0
                    and run.version == 1
                    and previous_actor == actor_id
                ):
                    return _view(run, actor_id, role, contract)
                raise _conflict("run_conflict", "This project already has a run.")
            if command.expected_version != 0:
                raise _conflict("run_version_conflict", "The idle project has version 0.")
            await _validate_plan(session, business_id, project_id)
            run = ProjectRun(
                id=new_id(),
                business_id=business_id,
                project_id=project_id,
                authorizing_user_id=actor_id,
                state=RunState.RUNNING,
                version=1,
            )
            session.add(run)
        else:
            if run is None:
                raise _conflict("run_conflict", "Start the project before controlling its run.")
            if role != "owner" and previous_actor != actor_id:
                raise HTTPException(403, detail="Run authorizer or business owner required.")
            if command.expected_version != run.version:
                raise _conflict("run_version_conflict", "The run has changed. Refresh and retry.")
            target = _TRANSITIONS.get(run.state, {}).get(action)
            if target is None:
                raise _conflict("run_conflict", f"Cannot {action} a {run.state} run.")
            if action == "resume":
                if previous_actor != actor_id and not assume_responsibility:
                    raise _conflict(
                        "run_authorization_required", "Explicitly assume responsibility."
                    )
                if run.active_task_id is not None:
                    raise _conflict("run_task_in_flight", "Wait for the active task to settle.")
                await _validate_plan(session, business_id, project_id)
                run.authorizing_user_id = actor_id
            run.state = target
            run.version += 1
        if run.active_task_id is None:
            run.reason_code = None
            run.blocker_id = None
        run.next_check_at = (
            await session.scalar(select(func.now()))
            if run.state == "running" or run.active_task_id is not None
            else None
        )
        await ActivityRepository().append(
            session,
            business_id,
            {
                "project_id": project_id,
                "agent_name": actor_id,
                "kind": "system",
                "text": (
                    f"Run {run.id} {previous_state} → {run.state} (version {run.version}) "
                    f"by {actor_id}. Authorizer: {previous_actor or actor_id} "
                    f"→ {run.authorizing_user_id}."
                ),
            },
        )
        await session.refresh(run)
        return _view(run, actor_id, role, contract)


@router.post(_PATH + "/start", response_model=ProjectRunResponse)
async def start_run(
    business_id: str, project_id: str, body: RunCommand, request: Request, identity: Actor
):
    return await _command("start", business_id, project_id, request, identity, body)


@router.post(_PATH + "/pause", response_model=ProjectRunResponse)
async def pause_run(
    business_id: str, project_id: str, body: RunCommand, request: Request, identity: Actor
):
    return await _command("pause", business_id, project_id, request, identity, body)


@router.post(_PATH + "/resume", response_model=ProjectRunResponse)
async def resume_run(
    business_id: str, project_id: str, body: ResumeRunCommand, request: Request, identity: Actor
):
    return await _command(
        "resume",
        business_id,
        project_id,
        request,
        identity,
        body,
        assume_responsibility=body.assume_responsibility,
    )


@router.post(_PATH + "/cancel", response_model=ProjectRunResponse)
async def cancel_run(
    business_id: str, project_id: str, body: RunCommand, request: Request, identity: Actor
):
    return await _command("cancel", business_id, project_id, request, identity, body)
