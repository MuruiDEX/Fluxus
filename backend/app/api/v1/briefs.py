"""Team-Lead intake endpoints (Stages 8.1–8.5): goal → questions → brief → plan.

POST .../briefs runs one bounded Team-Lead generation (at most two LLM
calls) OUTSIDE any database transaction, then persists the validated Brief
and questions atomically. A failed generation stores nothing — there is no
draft row with partial questions. POST .../briefs/{id}/answers validates
the owner's answers against the stored questions, runs one bounded summary
generation outside any transaction, then finalizes answers + summary + the
brief_ready transition atomically. POST .../briefs/{id}/plan runs one
bounded plan generation outside any transaction, then materializes the
project graph (project + tasks + dependency edges + gate approvals) and the
brief_ready → planned transition in ONE transaction. POST
.../briefs/{id}/finalize runs one bounded report generation outside any
transaction, then persists the validated report + Brain learnings + the
planned → completed transition + audit event in ONE transaction. GET
re-reads saved rows so clients resume after reload. Created tasks are never
started here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.reads import _approval_response, _project_response
from app.api.v1.schemas import (
    BriefResponse,
    FinalizeResponse,
    PlanResponse,
    ProjectWorkflowResponse,
)
from app.core.auth import check_client_contract
from app.core.identity import require_business
from app.integrations.repositories import IntegrationActionRepository
from app.llm.base import create_provider
from app.llm.types import LlmConfigurationError, LlmError, ProviderAuthError
from app.persistence.enums import IntegrationActionStatus, UsageCallKind
from app.persistence.exceptions import AgentDisabledError, DanglingReferenceError, new_id
from app.persistence.repositories.concrete import (
    AgentRepository,
    ApprovalRepository,
    BriefRepository,
    BusinessRepository,
    FinalizationRepository,
    FinalizeNotEligible,
    PlanningRepository,
    ProjectRepository,
    TaskRepository,
)
from app.persistence.repositories.project_runs import (
    finalization_blocked,
    get_project_run,
    lock_project_admission,
)
from app.teamlead.finalize import generate_finalization
from app.teamlead.planning import generate_plan
from app.teamlead.questions import BriefGenerationError, generate_questions
from app.teamlead.summary import generate_summary, validate_answers
from app.usage.metering import MeteringRefused, TeamLeadMeter

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
_integration_actions = IntegrationActionRepository()
_planning = PlanningRepository()
_finalizing = FinalizationRepository()

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
        finalReport=brief.final_report,
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


def _metering_error_to_http(exc: MeteringRefused) -> HTTPException:
    # Budget guardrails (fail closed before the provider call) — 409 like the
    # executor's refusal envelope, with a machine-readable code.
    return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message})


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
    brief_id = new_id()
    meter = TeamLeadMeter(
        factory,
        settings,
        business_id=business_id,
        brief_id=brief_id,
        call_kind=UsageCallKind.BRIEF_QUESTIONS.value,
    )
    try:
        questions = await generate_questions(
            provider, settings, business.name, business.industry, goal, meter=meter
        )
    except MeteringRefused as exc:
        raise _metering_error_to_http(exc) from exc
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
                    "id": brief_id,
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
    meter = TeamLeadMeter(
        factory,
        settings,
        business_id=business_id,
        brief_id=brief_id,
        call_kind=UsageCallKind.BRIEF_SUMMARY.value,
    )
    try:
        summary = await generate_summary(
            provider, settings, goal, stored_questions, normalized, meter=meter
        )
    except MeteringRefused as exc:
        raise _metering_error_to_http(exc) from exc
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
            for a in await _agents.list_assignable(session, business_id)
        ]
        if not roster:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_eligible_agents",
                    "message": "Hire or enable an agent in this business before building a plan.",
                },
            )
    try:
        provider = create_provider(settings)
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    # Generation happens BEFORE the project exists, so this call is
    # business-scoped (no project to attribute to or reserve against).
    meter = TeamLeadMeter(
        factory,
        settings,
        business_id=business_id,
        brief_id=brief_id,
        call_kind=UsageCallKind.PLAN_GENERATION.value,
    )
    try:
        plan = await generate_plan(provider, settings, goal, summary, roster, meter=meter)
    except MeteringRefused as exc:
        raise _metering_error_to_http(exc) from exc
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
    except AgentDisabledError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_disabled", "message": str(exc)},
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


def _finalize_response(envelope: dict, brief_id: str, contract: str) -> FinalizeResponse:
    return FinalizeResponse(
        briefId=brief_id,
        projectId=envelope["project_id"],
        status="completed",
        report=envelope["report"],
        learnings=envelope["learnings"],
        costs=envelope["costs"],
        contract=contract,
    )


def _stored_finalization(brief, contract: str) -> FinalizeResponse:
    """Serve the persisted finalization result (no LLM, idempotent repeat)."""
    envelope = brief.final_report
    if not isinstance(envelope, dict):
        # completed implies a report — anything else is corruption.
        raise HTTPException(status_code=500, detail="Brief state unclear.")
    return _finalize_response(envelope, brief.id, contract)


@router.get(
    "/businesses/{business_id}/projects/{project_id}/workflow",
    response_model=ProjectWorkflowResponse,
    dependencies=_GUARD,
)
async def read_project_workflow(business_id: str, project_id: str, request: Request):
    contract = _settings(request).contract_version
    factory = _factory(request)
    async with factory() as session:
        if await _projects.get(session, business_id, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found in scope.")
        brief = await _briefs.get_for_project(session, business_id, project_id)
        if brief is None:
            return ProjectWorkflowResponse(contract=contract)
        if brief.status == "completed":
            return ProjectWorkflowResponse(
                briefId=brief.id,
                phase="finalized",
                finalization=_stored_finalization(brief, contract),
                contract=contract,
            )
        if brief.status == "running":
            return ProjectWorkflowResponse(briefId=brief.id, phase="running", contract=contract)
        if brief.status != "planned":
            return ProjectWorkflowResponse(
                briefId=brief.id, contract=contract, phase="blocked",
                message=f"Brief is {brief.status} and cannot be finalized.",
            )
        tasks = await _tasks.list_for_project(session, business_id, project_id)
        approvals = await _approvals.list_for_project(session, business_id, project_id)
        actions = await _integration_actions.report_facts(session, business_id, project_id)
        edges = await _tasks.dependency_map(session, [task.id for task in tasks])
    phase, message = _workflow_phase(tasks, approvals, actions, edges)
    return ProjectWorkflowResponse(
        briefId=brief.id, phase=phase, message=message, contract=contract
    )


def _workflow_phase(tasks, approvals, actions, edges):
    if any(task.status == "working" for task in tasks):
        return "running", "A task is running on the server."
    if _unfinished_task(tasks) is None:
        if any(approval.status == "pending" for approval in approvals):
            return "waiting_approval", "Waiting for approval."
        if _undecided_approval(approvals) is None:
            blocker = _external_action_blocker(actions)
            if blocker:
                return "blocked", blocker
            return "awaiting_finalization", "All tasks reached a terminal state. Ready to finalize."
    approvals_by_id = {approval.id: approval for approval in approvals}
    completed_ids = {task.id for task in tasks if task.status == "completed"}
    for task in tasks:
        if task.status not in ("not_started", "failed") or not task.approval_gate_id:
            continue
        approval = approvals_by_id.get(task.approval_gate_id)
        if approval is not None and approval.status == "pending":
            if completed_ids.issuperset(edges.get(task.id, ())):
                return "waiting_approval", f"Waiting for approval: {approval.title}."
    if any(approval.status == "rejected" for approval in approvals):
        return "blocked", "A rejected approval may block remaining tasks."
    return "ready", None


@router.post(
    "/businesses/{business_id}/briefs/{brief_id}/finalize",
    response_model=FinalizeResponse,
    dependencies=_GUARD,
)
async def finalize_brief(business_id: str, brief_id: str, request: Request):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        brief = await _briefs.get(session, business_id, brief_id)
        if brief is None:
            # Uniform 404: never distinguish "missing" from "foreign scope".
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        if brief.project_id:
            await lock_project_admission(session, business_id, brief.project_id)
            await session.refresh(brief)
        if brief.status == "completed":
            # Idempotent repeat: the stored report, no LLM call.
            return _stored_finalization(brief, settings.contract_version)
        if brief.status != "planned":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conflict",
                    "message": f"Brief is {brief.status}, not finalizable.",
                },
            )
        if not brief.project_id:
            raise HTTPException(status_code=500, detail="Brief state unclear.")
        goal = brief.goal
        project_id = brief.project_id
        run_version = await _finalization_run_version(session, business_id, project_id)
        project = await _projects.get(session, business_id, project_id)
        if project is None:
            raise HTTPException(status_code=500, detail="Brief state unclear.")
        tasks = await _tasks.list_for_project(session, business_id, project_id, limit=None)
        approvals = await _approvals.list_for_project(session, business_id, project_id, limit=None)
        task_snapshot = _finalizing.task_snapshot(tasks)
        external_actions = await _integration_actions.report_facts(session, business_id, project_id)
        facts = {
            "goal": goal,
            "tasks": [
                {"id": t.id, "title": t.title, "status": t.status, "summary": t.summary}
                for t in tasks
            ],
            "approvals": [{"id": a.id, "title": a.title, "status": a.status} for a in approvals],
            "ledger": {
                "budget": str(project.budget),
                "ai_budget": str(project.ai_budget),
                "ai_spent": str(project.ai_spent),
            },
        }
    # Pre-LLM eligibility: refuse early without spending the generation
    # budget. Revalidated inside the transaction — races re-check there.
    _require_finalizable(tasks, approvals)
    if external_actions:
        facts["external_actions"] = external_actions
        try:
            result = _external_action_report(facts)
        except FinalizeNotEligible as exc:
            raise HTTPException(
                status_code=409, detail={"code": "conflict", "message": str(exc)}
            ) from exc
    else:
        result = await _generate_report(factory, settings, business_id, brief_id, project_id, facts)
    try:
        async with factory() as session:
            async with session.begin():
                await lock_project_admission(session, business_id, project_id)
                if await _finalization_run_version(session, business_id, project_id) != run_version:
                    raise FinalizeNotEligible("The project run changed. Refresh and try again.")
                outcome = await _finalizing.finalize(
                    session,
                    business_id,
                    brief_id,
                    result=result,
                    external_actions=external_actions,
                    task_snapshot=task_snapshot,
                )
                if outcome is None:
                    raise _FinalizeRaceLost()
                envelope, _, _ = outcome
                response = _finalize_response(envelope, brief_id, settings.contract_version)
    except FinalizeNotEligible as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": str(exc)},
        ) from exc
    except _FinalizeRaceLost:
        pass
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=500, detail="Brief could not be finalized.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Brief could not be finalized.") from exc
    else:
        return response
    async with factory() as session:
        current = await _briefs.get(session, business_id, brief_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Brief not found in scope.")
        if current.status == "completed":
            return _stored_finalization(current, settings.contract_version)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Brief is {current.status}, not finalizable.",
            },
        )


async def _finalization_run_version(session, business_id: str, project_id: str) -> int | None:
    run = await get_project_run(session, business_id, project_id)
    if finalization_blocked(run):
        raise HTTPException(
            409,
            detail={
                "code": "run_plan_locked",
                "message": "Pause the run and wait for active tasks to settle before finalizing.",
            },
        )
    return run.version if run is not None else None


async def _generate_report(factory, settings, business_id, brief_id, project_id, facts) -> dict:
    try:
        provider = create_provider(settings)
    except LlmError as exc:
        raise _provider_error_to_http(exc) from exc
    # Project-scoped: the brief already has a project, so this call is
    # budget-enforced (fail closed on unpriced budgets unless opted in).
    meter = TeamLeadMeter(
        factory,
        settings,
        business_id=business_id,
        brief_id=brief_id,
        call_kind=UsageCallKind.BRIEF_FINALIZE.value,
        project_id=project_id,
    )
    try:
        return await generate_finalization(provider, settings, facts, meter=meter)
    except MeteringRefused as exc:
        raise _metering_error_to_http(exc) from exc
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


_EXTERNAL_ACTION_OUTCOMES = {
    IntegrationActionStatus.SUCCEEDED: "Provider confirmed the action.",
    IntegrationActionStatus.FAILED: "Action failed. Provider success was not confirmed.",
    IntegrationActionStatus.REJECTED: "Action was rejected before dispatch.",
    IntegrationActionStatus.UNKNOWN: "Provider outcome is uncertain. Delivery is unconfirmed.",
}


def _external_action_blocker(actions) -> str | None:
    for action in actions:
        if action["status"] not in _EXTERNAL_ACTION_OUTCOMES:
            return (
                f"External action {action['id']} is {action['status']}. "
                "Resolve dispatch before finalizing."
            )
    return None


def _external_action_report(facts: dict) -> dict:
    actions = facts["external_actions"]
    blocker = _external_action_blocker(actions)
    if blocker:
        raise FinalizeNotEligible(blocker)
    limitations = [
        f"{action['capability']} ({action['id']}): {_EXTERNAL_ACTION_OUTCOMES[action['status']]}"
        for action in actions
    ]
    # Task/model prose can conflate an approval with provider success. Render
    # the report from persisted states alone when a project has external writes.
    return {
        "report": {
            "goal": facts["goal"],
            "completed_tasks": [
                {"id": task["id"], "title": task["title"], "summary": "Task completed."}
                for task in facts["tasks"]
                if task["status"] == "completed"
            ],
            "failed_tasks": [
                {"id": task["id"], "title": task["title"], "reason": "Task failed."}
                for task in facts["tasks"]
                if task["status"] == "failed"
            ],
            "limitations": limitations,
            "recommendations": [],
            "external_actions": facts["external_actions"],
        },
        "learnings": [],
    }


class _FinalizeRaceLost(Exception):
    """Internal signal: CAS lost, roll back and re-read the brief."""


def _unfinished_task(tasks):
    return next((task for task in tasks if task.status not in ("completed", "failed")), None)


def _undecided_approval(approvals):
    return next(
        (approval for approval in approvals if approval.status not in ("approved", "rejected")),
        None,
    )


def _require_finalizable(tasks, approvals) -> None:
    """Pre-LLM terminal-state check; revalidated in-transaction at commit."""
    task = _unfinished_task(tasks)
    approval = _undecided_approval(approvals)
    if task is not None:
        message = f"Task {task.id} is {task.status}, not terminal."
    elif approval is not None:
        message = f"Approval {approval.id} is {approval.status}, not terminal."
    else:
        return
    raise HTTPException(status_code=409, detail={"code": "conflict", "message": message})
