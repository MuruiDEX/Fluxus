"""Management writes (Stage 7.1) — manual Brain entries + metadata patches.

Additive endpoints; responses reuse the frozen §1 models:

- POST .../brain — manual knowledge entry (category/title/body, server id).
- PATCH .../projects/{project_id} — project metadata (title/goal/status/
  budget/ai_budget/deadline/team_ids/milestones).
- PATCH .../projects/{project_id}/tasks/{task_id} — task metadata
  (title/summary/eta_minutes/owner_agent_id/gate_at).
- PATCH .../businesses/{business_id} — business-scoped AI budget policy
  (ai_budget/allow_unpriced_ai, Stage 18 S2).

What is deliberately NOT writable here (422 via extra="forbid" schemas):

- Task status/progress/cost_accrued/attempts/approval_gate_id/cost_rate —
  owned by the executor (claim semantics) and the ai_spent recompute path
  (§5.1). Execution state can only change through executions/recovery.
- Project ai_spent/progress — derived caches, never accepted on write.
- Business/activity/brief/approval lifecycles — owned by their endpoints.
- No DELETE anywhere in this slice: tasks/approvals have terminal states
  through execution/decisions, activity is append-only by design.

Every mutation runs in ONE transaction together with its audit event
(kind "system", attributed to the deciding user), following the approvals
pattern. Cross-entity references resolve in-scope or 422 dangling_reference.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.reads import _brain_response, _business_response, _project_response
from app.api.v1.schemas import (
    BrainCreate,
    BrainResponse,
    BusinessPatch,
    BusinessResponse,
    ProjectPatch,
    ProjectResponse,
    TaskPatch,
    TaskResponse,
)
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business, require_business_owner
from app.persistence.enums import BrainCategory, ProjectStatus
from app.persistence.exceptions import AgentDisabledError, DanglingReferenceError, new_id
from app.persistence.repositories.base import (
    clamp_progress,
    quantize_ai_budget,
    quantize_money,
)
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    BrainRepository,
    BusinessRepository,
    ProjectRepository,
    TaskRepository,
    UserRepository,
)

router = APIRouter()
_brain = BrainRepository()
_businesses = BusinessRepository()
_projects = ProjectRepository()
_tasks = TaskRepository()
_agents = AgentRepository()
_activity = ActivityRepository()
_users = UserRepository()

# require_business is declared once as a parameter (like approvals.py):
# FastAPI evaluates it a single time per request (dependency cache), so the
# handler also gets the Identity value to attribute the audit event.
_GUARD = [Depends(check_client_contract)]


def _invalid_enum(detail: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "invalid_enum", "message": detail},
    )


def _dangling(exc: DanglingReferenceError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "dangling_reference", "message": str(exc)},
    )


def _disabled(exc: AgentDisabledError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "agent_disabled", "message": str(exc)},
    )


def _invalid_value(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "validation_error", "message": str(exc)},
    )


async def _actor_email(session, identity: Identity) -> str:
    user = await _users.get(session, identity.user_id)
    return user.email if user is not None else identity.user_id


@router.patch(
    "/businesses/{business_id}",
    response_model=BusinessResponse,
    dependencies=_GUARD,
)
async def patch_business(
    business_id: str,
    body: BusinessPatch,
    request: Request,
    identity: Identity = Depends(require_business_owner),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    patch: dict = {}
    if body.ai_budget is not None:
        try:
            patch["ai_budget"] = quantize_ai_budget(body.ai_budget)
        except ValueError as exc:
            raise _invalid_value(exc) from exc
    if body.allow_unpriced_ai is not None:
        patch["allow_unpriced_ai"] = body.allow_unpriced_ai
    async with factory() as session:
        async with session.begin():
            business = await _businesses.get(session, business_id, business_id)
            if business is None:
                raise HTTPException(status_code=404, detail="Business not found in scope.")
            for key, value in patch.items():
                setattr(business, key, value)
            await session.flush()
            actor = await _actor_email(session, identity)
            await _activity.append(
                session,
                business_id,
                {
                    "agent_name": actor,
                    "kind": "system",
                    "text": f"Business {business_id} updated by {actor}.",
                },
            )
            return _business_response(business, settings.contract_version)


@router.post(
    "/businesses/{business_id}/brain",
    response_model=BrainResponse,
    status_code=201,
    dependencies=_GUARD,
)
async def create_brain_entry(
    business_id: str,
    body: BrainCreate,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    if body.category not in {c.value for c in BrainCategory}:
        raise _invalid_enum(f"Unknown brain category: {body.category!r}.")
    title = body.title.strip()
    if not title:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "Brain title must be non-empty."},
        )
    try:
        async with factory() as session:
            async with session.begin():
                entry = await _brain.create(
                    session,
                    business_id,
                    {"id": new_id(), "category": body.category, "title": title, "body": body.body},
                )
                actor = await _actor_email(session, identity)
                await _activity.append(
                    session,
                    business_id,
                    {
                        "agent_name": actor,
                        "kind": "system",
                        "text": f"Brain entry {entry.id} created by {actor}.",
                    },
                )
                return _brain_response(entry, settings.contract_version)
    except DanglingReferenceError as exc:  # pragma: no cover - business FK always exists in scope
        raise _dangling(exc) from exc


@router.patch(
    "/businesses/{business_id}/projects/{project_id}",
    response_model=ProjectResponse,
    dependencies=_GUARD,
)
async def patch_project(
    business_id: str,
    project_id: str,
    body: ProjectPatch,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    # Project metadata remains member-editable, but changes that affect AI
    # budget enforcement require the established business-owner guard.
    if body.ai_budget is not None or body.allow_unpriced_ai is not None:
        await require_business_owner(request, identity)
    if body.status is not None and body.status not in {s.value for s in ProjectStatus}:
        raise _invalid_enum(f"Unknown project status: {body.status!r}.")
    patch: dict = {}
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status_code=422,
                detail={"code": "validation_error", "message": "Project title must be non-empty."},
            )
        patch["title"] = title
    if body.goal is not None:
        patch["goal"] = body.goal
    if body.status is not None:
        patch["status"] = body.status
    if body.deadline is not None:
        patch["deadline"] = body.deadline
    if body.milestones is not None:
        patch["milestones"] = body.milestones
    if body.team_ids is not None:
        patch["team_ids"] = list(body.team_ids)
    if body.allow_unpriced_ai is not None:
        patch["allow_unpriced_ai"] = body.allow_unpriced_ai
    try:
        if body.budget is not None:
            patch["budget"] = quantize_money(body.budget)
        if body.ai_budget is not None:
            patch["ai_budget"] = quantize_money(body.ai_budget)
    except ValueError as exc:
        raise _invalid_value(exc) from exc
    try:
        async with factory() as session:
            async with session.begin():
                project = await _projects.get(session, business_id, project_id)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found in scope.")
                if "team_ids" in patch:
                    await _projects._require_agents(session, business_id, patch["team_ids"])
                if patch:
                    for key, value in patch.items():
                        setattr(project, key, value)
                    project.progress = clamp_progress(project.progress)
                    await session.flush()
                actor = await _actor_email(session, identity)
                await _activity.append(
                    session,
                    business_id,
                    {
                        "project_id": project.id,
                        "agent_name": actor,
                        "kind": "system",
                        "text": f"Project {project_id} updated by {actor}.",
                    },
                )
                return _project_response(project, settings.contract_version)
    except DanglingReferenceError as exc:
        raise _dangling(exc) from exc
    except AgentDisabledError as exc:
        raise _disabled(exc) from exc


@router.patch(
    "/businesses/{business_id}/projects/{project_id}/tasks/{task_id}",
    response_model=TaskResponse,
    dependencies=_GUARD,
)
async def patch_task(
    business_id: str,
    project_id: str,
    task_id: str,
    body: TaskPatch,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    patch: dict = {}
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status_code=422,
                detail={"code": "validation_error", "message": "Task title must be non-empty."},
            )
        patch["title"] = title
    if body.summary is not None:
        patch["summary"] = body.summary
    if body.eta_minutes is not None:
        patch["eta_minutes"] = body.eta_minutes
    if body.gate_at is not None:
        patch["gate_at"] = body.gate_at
    if body.owner_agent_id is not None:
        patch["owner_agent_id"] = body.owner_agent_id
    try:
        async with factory() as session:
            async with session.begin():
                task = await _tasks.get(session, business_id, task_id)
                if task is None or task.project_id != project_id:
                    # Uniform 404: never distinguish "missing" from "foreign scope".
                    raise HTTPException(status_code=404, detail="Task not found in scope.")
                if "owner_agent_id" in patch:
                    owner = await _agents.get(session, business_id, patch["owner_agent_id"])
                    if owner is None:
                        raise DanglingReferenceError(
                            f"task references missing agent: {patch['owner_agent_id']}"
                        )
                    if not owner.hired:
                        raise AgentDisabledError(
                            f"task references disabled agent: {patch['owner_agent_id']}"
                        )
                if patch:
                    for key, value in patch.items():
                        setattr(task, key, value)
                    await session.flush()
                actor = await _actor_email(session, identity)
                await _activity.append(
                    session,
                    business_id,
                    {
                        "project_id": task.project_id,
                        "agent_name": actor,
                        "kind": "system",
                        "text": f"Task {task_id} updated by {actor}.",
                    },
                )
                (response,) = await _task_responses(
                    session, business_id, [task], settings.contract_version
                )
                return response
    except DanglingReferenceError as exc:
        raise _dangling(exc) from exc
    except AgentDisabledError as exc:
        raise _disabled(exc) from exc
