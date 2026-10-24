"""Read-only domain API (Stage 7.0) — lists + single gets, no writes.

Every route is a safe GET under the standard guard: contract header +
`require_business` (session + live membership against the path
`business_id`; foreign scope is a uniform 404). No CSRF token is required
for safe methods. Shapes mirror contract §1 field-for-field; internal
columns and secrets never leave the process.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.schemas import (
    ActivityListResponse,
    ActivityResponse,
    AgentListResponse,
    AgentResponse,
    ApprovalListResponse,
    ApprovalResponse,
    BrainListResponse,
    BrainResponse,
    BusinessResponse,
    ProjectListResponse,
    ProjectResponse,
    TaskListResponse,
)
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.persistence.enums import BrainCategory
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    ApprovalRepository,
    BrainRepository,
    BusinessRepository,
    ProjectRepository,
    TaskRepository,
)

router = APIRouter()
_businesses = BusinessRepository()
_projects = ProjectRepository()
_tasks = TaskRepository()
_agents = AgentRepository()
_approvals = ApprovalRepository()
_activity = ActivityRepository()
_brain = BrainRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_LIMIT = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT)
_OFFSET = Query(default=0, ge=0)


def _at_iso(value: datetime | None) -> str:
    """Server timestamp as RFC 3339 (§4); naive values are UTC by storage."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _business_response(business, contract: str) -> BusinessResponse:
    return BusinessResponse(
        id=business.id,
        name=business.name,
        tagline=business.tagline,
        industry=business.industry,
        location=business.location,
        ownerName=business.owner_name,
        healthScore=business.health_score,
        healthBreakdown=business.health_breakdown,
        metrics=business.metrics,
        revenueSeries=business.revenue_series,
        aiBudget=business.ai_budget,
        allowUnpricedAi=business.allow_unpriced_ai,
        contract=contract,
    )


def _project_response(project, contract: str) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        businessId=project.business_id,
        title=project.title,
        goal=project.goal,
        status=project.status,
        progress=project.progress,
        budget=project.budget,
        aiBudget=project.ai_budget,
        aiSpent=project.ai_spent,
        deadline=project.deadline,
        teamIds=project.team_ids,
        milestones=project.milestones,
        allowUnpricedAi=project.allow_unpriced_ai,
        contract=contract,
    )


def _agent_response(agent, contract: str) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        businessId=agent.business_id,
        name=agent.name,
        role=agent.role,
        responsibilities=agent.responsibilities,
        tools=agent.tools,
        permissions=agent.permissions,
        status=agent.status,
        currentTask=agent.current_task,
        costPerMonth=agent.cost_per_month,
        performance=agent.performance,
        hired=agent.hired,
        avatarHue=agent.avatar_hue,
        contract=contract,
    )


def _approval_response(approval, contract: str) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        businessId=approval.business_id,
        projectId=approval.project_id,
        title=approval.title,
        requestedBy=approval.requested_by,
        requestedByAgentId=approval.requested_by_agent_id,
        ageLabel=approval.age_label,
        status=approval.status,
        what=approval.what,
        expectedResult=approval.expected_result,
        cost=approval.cost,
        affects=approval.affects,
        rationale=approval.rationale,
        gatesTaskId=approval.gates_task_id,
        contract=contract,
    )


def _activity_response(event, contract: str) -> ActivityResponse:
    return ActivityResponse(
        id=event.id,
        at=_at_iso(event.created_at),
        agentName=event.agent_name,
        text=event.text,
        kind=event.kind,
        cost=event.cost,
        contract=contract,
    )


def _brain_response(entry, contract: str) -> BrainResponse:
    return BrainResponse(
        id=entry.id,
        businessId=entry.business_id,
        category=entry.category,
        title=entry.title,
        body=entry.body,
        updatedLabel=entry.updated_label,
        contract=contract,
    )


async def _require_project(session, business_id: str, project_id: str):
    """Project row in scope, or uniform 404 (never confirm foreign rows)."""
    project = await _projects.get(session, business_id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found in scope.")
    return project


@router.get(
    "/businesses/{business_id}",
    response_model=BusinessResponse,
    dependencies=_GUARD,
)
async def read_business(business_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        business = await _businesses.get(session, business_id, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="Business not found in scope.")
        return _business_response(business, settings.contract_version)


@router.get(
    "/businesses/{business_id}/projects",
    response_model=ProjectListResponse,
    dependencies=_GUARD,
)
async def list_projects(
    business_id: str, request: Request, limit: int = _LIMIT, offset: int = _OFFSET
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        rows = await _projects.list(session, business_id, limit=limit, offset=offset)
        total = await _projects.count(session, business_id)
        return ProjectListResponse(
            items=[_project_response(p, settings.contract_version) for p in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/projects/{project_id}",
    response_model=ProjectResponse,
    dependencies=_GUARD,
)
async def read_project(business_id: str, project_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        project = await _require_project(session, business_id, project_id)
        return _project_response(project, settings.contract_version)


@router.get(
    "/businesses/{business_id}/projects/{project_id}/tasks",
    response_model=TaskListResponse,
    dependencies=_GUARD,
)
async def list_tasks(
    business_id: str,
    project_id: str,
    request: Request,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        await _require_project(session, business_id, project_id)
        rows = await _tasks.list_for_project(
            session, business_id, project_id, limit=limit, offset=offset
        )
        total = await _tasks.count_for_project(session, business_id, project_id)
        return TaskListResponse(
            items=await _task_responses(session, business_id, rows, settings.contract_version),
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/agents",
    response_model=AgentListResponse,
    dependencies=_GUARD,
)
async def list_agents(
    business_id: str, request: Request, limit: int = _LIMIT, offset: int = _OFFSET
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        rows = await _agents.list(session, business_id, limit=limit, offset=offset)
        total = await _agents.count(session, business_id)
        return AgentListResponse(
            items=[_agent_response(a, settings.contract_version) for a in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/agents/{agent_id}",
    response_model=AgentResponse,
    dependencies=_GUARD,
)
async def read_agent(business_id: str, agent_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        agent = await _agents.get(session, business_id, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found in scope.")
        return _agent_response(agent, settings.contract_version)


@router.get(
    "/businesses/{business_id}/approvals",
    response_model=ApprovalListResponse,
    dependencies=_GUARD,
)
async def list_approvals(
    business_id: str, request: Request, limit: int = _LIMIT, offset: int = _OFFSET
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        rows = await _approvals.list(session, business_id, limit=limit, offset=offset)
        total = await _approvals.count(session, business_id)
        return ApprovalListResponse(
            items=[_approval_response(a, settings.contract_version) for a in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/approvals/{approval_id}",
    response_model=ApprovalResponse,
    dependencies=_GUARD,
)
async def read_approval(business_id: str, approval_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        approval = await _approvals.get(session, business_id, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found in scope.")
        return _approval_response(approval, settings.contract_version)


@router.get(
    "/businesses/{business_id}/activity",
    response_model=ActivityListResponse,
    dependencies=_GUARD,
)
async def list_activity(
    business_id: str,
    request: Request,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
    project_id: str | None = Query(default=None),
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        if project_id is not None:
            await _require_project(session, business_id, project_id)
        rows = await _activity.list(
            session, business_id, project_id=project_id, limit=limit, offset=offset
        )
        total = await _activity.count(session, business_id, project_id=project_id)
        return ActivityListResponse(
            items=[_activity_response(e, settings.contract_version) for e in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/brain",
    response_model=BrainListResponse,
    dependencies=_GUARD,
)
async def list_brain(
    business_id: str,
    request: Request,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
    category: str | None = Query(default=None),
):
    settings = _settings(request)
    factory = _factory(request)
    if category is not None and category not in {c.value for c in BrainCategory}:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_enum", "message": f"Unknown brain category: {category!r}."},
        )
    async with factory() as session:
        rows = await _brain.list(
            session, business_id, category=category, limit=limit, offset=offset
        )
        total = await _brain.count(session, business_id, category=category)
        return BrainListResponse(
            items=[_brain_response(e, settings.contract_version) for e in rows],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )
