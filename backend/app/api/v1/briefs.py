"""Team-Lead intake endpoints (Stages 8.1–8.3): goal → questions → brief → plan.

POST .../briefs runs one bounded Team-Lead generation (at most two LLM
calls) OUTSIDE any database transaction, then persists the validated Brief
and questions atomically. A failed generation stores nothing — there is no
draft row with partial questions. POST .../briefs/{id}/answers validates
the owner's answers against the stored questions, runs one bounded summary
generation outside any transaction, then finalizes answers + summary + the
brief_ready transition atomically. POST .../briefs/{id}/plan runs one
bounded plan generation outside any transaction, then materializes the
project graph (project + tasks + dependency edges + gate approvals) and the
brief_ready → planned transition in ONE transaction. GET re-reads saved rows
so clients resume after reload. Created tasks are never started here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.reads import _approval_response, _project_response
from app.api.v1.schemas import BriefResponse, PlanResponse
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.llm.base import create_provider
from app.llm.types import LlmConfigurationError, LlmError, ProviderAuthError
from app.persistence.exceptions import DanglingReferenceError
from app.persistence.repositories.concrete import (
    AgentRepository,
    ApprovalRepository,
    BriefRepository,
    BusinessRepository,
    PlanningRepository,
    ProjectRepository,
    TaskRepository,
)
from app.teamlead.planning import generate_plan
from app.teamlead.questions import BriefGenerationError, generate_questions
from app.teamlead.summary import generate_summary, validate_answers

router = APIRouter()
_briefs = BriefRepository()

_FINALIZED_CONFLICT = {
    "code": "conflict",
    "message": "Brief already finalized with different answers.",
}
_businesses = BusinessRepository()
_agents = AgentRepository()
_projects = ProjectRepository()
_tasks = TaskRepository()
_approvals = ApprovalRepository()
_planning = PlanningRepository()

_GUARD = [Depends(check_client_contract), Depends(require_business)]


class BriefCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


class BriefAnswerItem(BaseModel):
    question_id: str
    text: str = Field(min_length=1, max_length=2000)


class BriefAnswers(BaseModel):
    answers: list[BriefAnswerItem] = Field(min_length=1)


def _brief_response(brief, contract: str) -> BriefResponse:
    return BriefResponse(
        id=brief.id,
        businessId=brief.business_id,
        goal=brief.goal,
        status=brief.status,
        questions=brief.questions,
        answers=brief.answers,
        summary=brief.summary,
        projectId=brief.project_id,
        contract=contract,
    )


def _provider_error_to_http(exc: LlmError) -> HTTPException:
    # Never leak provider internals or secrets: fixed safe messages only.
    if isinstance(exc, LlmConfigurationError):
        return HTTPException(status_code=500, detail="LLM provider is not configured.")
    if isinstance(exc, ProviderAuthError):
        return HTTPException(
            status_code=500,
            detail={
                "code": "provider_auth_error",
                "message": "LLM provider rejected our credentials.",
            },
        )
    return HTTPException(status_code=500, detail="LLM request failed.")


@router.post(
    "/businesses/{business_id}/briefs",
    response_model=BriefResponse,
    dependencies=_GUARD,
)
async def create_brief(business_id: str, body: BriefCreate, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "goal must be a non-empty string."},
        )
    async with factory() as session:
        business = await _businesses.get(session, business_id, business_id)
    if business is None:
        # Membership implies existence; never assume it.
        raise HTTPException(status_code=404, detail="Business not found in scope.")
    try:
        provider = create_provider(settings)
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    try:
        questions = await generate_questions(
            provider, settings, business.name, business.industry, goal
        )
    except BriefGenerationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Model output failed validation.",
                "details": exc.violations,
            },
        ) from exc
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    async with factory() as session:
        async with session.begin():
            brief = await _briefs.create(
                session,
                business_id,
                {
                    "goal": goal,
                    "questions": questions,
                    "answers": [],
                    "summary": None,
                    "project_id": None,
                    "status": "questions_ready",
                },
            )
    return _brief_response(brief, settings.contract_version)


@router.get(
    "/businesses/{business_id}/briefs/{brief_id}",
    response_model=BriefResponse,
    dependencies=_GUARD,
)
async def read_brief(business_id: str, brief_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        brief = await _briefs.get(session, business_id, brief_id)
        if brief is None:
            # Uniform 404: never distinguish "missing" from "foreign scope".
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        return _brief_response(brief, settings.contract_version)


@router.post(
    "/businesses/{business_id}/briefs/{brief_id}/answers",
    response_model=BriefResponse,
    dependencies=_GUARD,
)
async def submit_answers(business_id: str, brief_id: str, body: BriefAnswers, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        brief = await _briefs.get(session, business_id, brief_id)
        if brief is None:
            # Uniform 404: never distinguish "missing" from "foreign scope".
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        stored_questions = list(brief.questions)
        stored_status = brief.status
        stored_answers = list(brief.answers)
        goal = brief.goal
    normalized, violations = validate_answers(
        stored_questions, [a.model_dump() for a in body.answers]
    )
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Answers failed validation.",
                "details": violations,
            },
        )
    if stored_status == "brief_ready":
        # Idempotent repeat returns the saved result; a different payload is
        # a conflict, never a silent overwrite.
        if stored_answers == normalized:
            async with factory() as session:
                saved = await _briefs.get(session, business_id, brief_id)
                if saved is None:  # pragma: no cover - row existed moments ago
                    raise HTTPException(status_code=404, detail="Brief not found in scope.")
                return _brief_response(saved, settings.contract_version)
        raise HTTPException(
            status_code=409,
            detail=_FINALIZED_CONFLICT,
        )
    if stored_status != "questions_ready":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Brief is {stored_status}, not answerable.",
            },
        )
    try:
        provider = create_provider(settings)
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    try:
        summary = await generate_summary(provider, settings, goal, stored_questions, normalized)
    except BriefGenerationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Model output failed validation.",
                "details": exc.violations,
            },
        ) from exc
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    async with factory() as session:
        async with session.begin():
            won = await _briefs.save_answers(
                session, business_id, brief_id, answers=normalized, summary=summary
            )
            if won:
                row = await _briefs.get(session, business_id, brief_id)
                if row is None:  # pragma: no cover - CAS win implies present
                    raise HTTPException(status_code=500, detail="Brief state unclear.")
                return _brief_response(row, settings.contract_version)
    # CAS lost: a concurrent finalize won — re-read under scope, never assume.
    async with factory() as session:
        current = await _briefs.get(session, business_id, brief_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        if current.status == "brief_ready" and list(current.answers) == normalized:
            return _brief_response(current, settings.contract_version)
        if current.status == "brief_ready":
            raise HTTPException(status_code=409, detail=_FINALIZED_CONFLICT)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Brief is {current.status}, not answerable.",
            },
        )


async def _plan_response(
    session, business_id: str, brief_id: str, project, tasks, approvals, contract: str
) -> PlanResponse:
    return PlanResponse(
        briefId=brief_id,
        project=_project_response(project, contract),
        tasks=await _task_responses(session, business_id, tasks, contract),
        approvals=[_approval_response(a, contract) for a in approvals],
        contract=contract,
    )


async def _saved_plan(session, business_id: str, brief, contract: str) -> PlanResponse:
    """Re-read the materialized graph for a planned brief (no LLM)."""
    project = await _projects.get(session, business_id, brief.project_id or "")
    if project is None:  # planned implies a project — anything else is corruption
        raise HTTPException(status_code=500, detail="Brief state unclear.")
    tasks = await _tasks.list_for_project(session, business_id, project.id)
    approvals = await _approvals.list_for_project(session, business_id, project.id)
    return await _plan_response(session, business_id, brief.id, project, tasks, approvals, contract)


class _PlanRaceLost(Exception):
    """Internal signal: CAS lost, roll back the graph and re-read the brief."""


@router.post(
    "/businesses/{business_id}/briefs/{brief_id}/plan",
    response_model=PlanResponse,
    dependencies=_GUARD,
)
async def create_plan(business_id: str, brief_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        brief = await _briefs.get(session, business_id, brief_id)
        if brief is None:
            # Uniform 404: never distinguish "missing" from "foreign scope".
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        if brief.status == "planned":
            # Idempotent repeat: the saved graph, no LLM call.
            return await _saved_plan(session, business_id, brief, settings.contract_version)
        if brief.status != "brief_ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conflict",
                    "message": f"Brief is {brief.status}, not plannable.",
                },
            )
        goal = brief.goal
        if not isinstance(brief.summary, dict):
            # brief_ready implies a summary — anything else is corruption.
            raise HTTPException(status_code=500, detail="Brief state unclear.")
        summary = dict(brief.summary)
        roster = [
            {"id": a.id, "name": a.name, "role": a.role}
            for a in await _agents.list(session, business_id)
        ]
    try:
        provider = create_provider(settings)
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    try:
        plan = await generate_plan(provider, settings, goal, summary, roster)
    except BriefGenerationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Model output failed validation.",
                "details": exc.violations,
            },
        ) from exc
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    try:
        async with factory() as session:
            async with session.begin():
                project, made_tasks, made_approvals = await _planning.create_plan_graph(
                    session, business_id, plan
                )
                won = await _briefs.mark_planned(
                    session, business_id, brief_id, project_id=project.id
                )
                if not won:
                    # Lost the race: roll everything back, re-read below.
                    raise _PlanRaceLost()
                response = await _plan_response(
                    session,
                    business_id,
                    brief_id,
                    project,
                    made_tasks,
                    made_approvals,
                    settings.contract_version,
                )
    except DanglingReferenceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "dangling_reference", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        # Defense in depth: pure plan validation rejects bad money/counts
        # before the transaction, so this is unreachable in practice.
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": str(exc)},
        ) from exc
    except _PlanRaceLost:
        pass
    except IntegrityError as exc:
        # Unreachable after validation + scoped checks; fail closed.
        raise HTTPException(status_code=500, detail="Plan could not be saved.") from exc
    except Exception as exc:
        # Fail closed with a fixed safe message (never leak internals).
        # CancelledError is BaseException, not Exception: cancellation still
        # propagates untouched and stores nothing.
        raise HTTPException(status_code=500, detail="Plan could not be saved.") from exc
    else:
        return response
    # CAS lost: a concurrent planning won — re-read under scope, never assume.
    async with factory() as session:
        current = await _briefs.get(session, business_id, brief_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        if current.status == "planned":
            return await _saved_plan(session, business_id, current, settings.contract_version)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Brief is {current.status}, not plannable.",
            },
        )
