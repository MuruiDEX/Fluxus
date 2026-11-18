"""Built-in capability handlers.

Every handler is an ordinary async function that runs INSIDE the engine's
per-call transaction. It must not commit, roll back or open a nested
transaction by itself. Validation happens before any write; a failure raised
before a write therefore leaves nothing behind, and the engine's savepoint
guards accidental partial writes anyway.

Handlers only touch the caller's business/project scope. Outputs are bounded
so a model can never be fed an unbounded amount of company data.
"""

from typing import Any

from app.capabilities.types import (
    CapabilityContext,
    CapabilityError,
    CapabilityResult,
    text_limit,
)
from app.persistence.enums import BrainCategory, TaskStatus
from app.persistence.exceptions import DanglingReferenceError
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    ApprovalRepository,
    BrainRepository,
    BusinessRepository,
    ProjectRepository,
    TaskRepository,
)

_activity = ActivityRepository()
_agents = AgentRepository()
_approvals = ApprovalRepository()
_brains = BrainRepository()
_businesses = BusinessRepository()
_projects = ProjectRepository()
_tasks = TaskRepository()

_MAX_TEXT = 2000
_MAX_LIST = 50
_MAX_KNOWLEDGE_ENTRIES = 10
_MAX_KNOWLEDGE_BODY = 400


def _check_args(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise CapabilityError(
            "invalid_arguments",
            "Unsupported argument(s): " + ", ".join(sorted(unknown)) + ".",
        )


def _string(
    arguments: dict[str, Any],
    name: str,
    *,
    required: bool = False,
    max_chars: int = _MAX_TEXT,
) -> str:
    value = arguments.get(name)
    if value is None:
        if required:
            raise CapabilityError("invalid_arguments", f"Argument {name!r} is required.")
        return ""
    if not isinstance(value, str):
        raise CapabilityError("invalid_arguments", f"Argument {name!r} must be a string.")
    value = value.strip()
    if required and not value:
        raise CapabilityError("invalid_arguments", f"Argument {name!r} must not be empty.")
    if len(value) > max_chars:
        raise CapabilityError(
            "invalid_arguments",
            f"Argument {name!r} exceeds {max_chars} characters.",
        )
    return value


def _integer(
    arguments: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapabilityError("invalid_arguments", f"Argument {name!r} must be an integer.")
    if not minimum <= value <= maximum:
        raise CapabilityError(
            "invalid_arguments",
            f"Argument {name!r} must be between {minimum} and {maximum}.",
        )
    return value


def _write_guard(ctx: CapabilityContext) -> None:
    if ctx.attempt != 0:
        raise CapabilityError(
            "first_attempt_only",
            "Write capabilities are available only on the first attempt; this run is a retry.",
        )
    limit = int(getattr(ctx.settings, "execution_max_write_calls_per_run", 3))
    if ctx.writes_used >= limit:
        raise CapabilityError(
            "write_limit_reached",
            f"At most {limit} write capability calls are allowed per run.",
        )


async def business_context(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(arguments, set())
    business = await _businesses.get(ctx.session, ctx.business_id, ctx.business_id)
    project = await _projects.get(ctx.session, ctx.business_id, ctx.project_id)
    if business is None or project is None:
        raise CapabilityError("not_found", "Business or project context is unavailable.")
    entries = await _brains.list(ctx.session, ctx.business_id, limit=8)
    tasks = await _tasks.list_for_project(ctx.session, ctx.business_id, ctx.project_id, limit=200)
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return CapabilityResult(
        capability="business.context",
        ok=True,
        data={
            "business": {
                "name": business.name,
                "industry": business.industry,
                "location": business.location,
                "tagline": text_limit(business.tagline or "", 300),
            },
            "project": {
                "title": project.title,
                "goal": text_limit(project.goal or "", 1000),
                "status": project.status,
                "progress": project.progress,
            },
            "taskCounts": counts,
            "knowledge": [
                {
                    "category": entry.category,
                    "title": entry.title,
                    "body": text_limit(entry.body or "", 300),
                }
                for entry in entries
            ],
        },
    )


async def project_tasks(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(arguments, {"status", "limit"})
    status = _string(arguments, "status", max_chars=32)
    if status and status not in {item.value for item in TaskStatus}:
        raise CapabilityError("invalid_arguments", "Unknown task status filter.")
    limit = _integer(arguments, "limit", default=20, minimum=1, maximum=_MAX_LIST)
    rows = await _tasks.list_for_project(ctx.session, ctx.business_id, ctx.project_id, limit=200)
    selected = [task for task in rows if not status or task.status == status][:limit]
    return CapabilityResult(
        capability="project.tasks",
        ok=True,
        data={
            "tasks": [
                {
                    "id": task.id,
                    "title": text_limit(task.title, 300),
                    "status": task.status,
                    "ownerAgentId": task.owner_agent_id,
                    "progress": task.progress,
                    "attempts": task.attempts,
                    "approvalGateId": task.approval_gate_id,
                }
                for task in selected
            ],
            "total": len(selected),
        },
    )


async def task_result(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(arguments, {"task_id"})
    task_id = _string(arguments, "task_id", required=True, max_chars=64)
    task = await _tasks.get(ctx.session, ctx.business_id, task_id)
    if task is None or task.project_id != ctx.project_id:
        raise CapabilityError("not_found", "Task not found in this project.")
    return CapabilityResult(
        capability="task.result",
        ok=True,
        data={
            "id": task.id,
            "title": text_limit(task.title, 300),
            "status": task.status,
            "progress": task.progress,
            "summary": text_limit(task.summary or "", 1500),
            "attempts": task.attempts,
            "approvalGateId": task.approval_gate_id,
        },
    )


async def knowledge_search(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(arguments, {"query", "limit", "category"})
    query = _string(arguments, "query", required=True, max_chars=200)
    limit = _integer(arguments, "limit", default=5, minimum=1, maximum=_MAX_KNOWLEDGE_ENTRIES)
    category = _string(arguments, "category", max_chars=32)
    if category and category not in {item.value for item in BrainCategory}:
        raise CapabilityError("invalid_arguments", "Unknown knowledge category.")
    entries = await _brains.list(ctx.session, ctx.business_id, category=category or None, limit=200)
    words = [word for word in query.lower().split() if len(word) > 1]
    scored: list[tuple[int, str, Any]] = []
    for entry in entries:
        haystack = f"{entry.title}\n{entry.body}".lower()
        score = sum(
            (2 if word in entry.title.lower() else 0) + (1 if word in haystack else 0)
            for word in words
        )
        if score > 0:
            scored.append((score, entry.id, entry))
    scored.sort(key=lambda row: (-row[0], row[1]))
    results = [
        {
            "id": entry.id,
            "category": entry.category,
            "title": text_limit(entry.title, 300),
            "body": text_limit(entry.body or "", _MAX_KNOWLEDGE_BODY),
        }
        for _, _, entry in scored[:limit]
    ]
    return CapabilityResult(
        capability="knowledge.search",
        ok=True,
        data={"query": query, "results": results, "total": len(results)},
    )


async def request_approval(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(
        arguments,
        {"title", "what", "expected_result", "cost", "affects", "rationale"},
    )
    _write_guard(ctx)
    title = _string(arguments, "title", required=True, max_chars=200)
    what = _string(arguments, "what", max_chars=_MAX_TEXT)
    expected_result = _string(arguments, "expected_result", max_chars=_MAX_TEXT)
    cost = _string(arguments, "cost", max_chars=100)
    affects = _string(arguments, "affects", max_chars=_MAX_TEXT)
    rationale = _string(arguments, "rationale", max_chars=_MAX_TEXT)
    try:
        approval = await _approvals.create(
            ctx.session,
            ctx.business_id,
            {
                "project_id": ctx.project_id,
                "title": title,
                "requested_by": ctx.agent_name,
                "requested_by_agent_id": ctx.agent_id,
                "status": "pending",
                "what": what,
                "expected_result": expected_result,
                "cost": cost,
                "affects": affects,
                "rationale": rationale,
                "gates_task_id": ctx.task_id,
            },
        )
    except DanglingReferenceError as exc:
        raise CapabilityError("invalid_reference", str(exc)) from exc
    await _activity.append(
        ctx.session,
        ctx.business_id,
        {
            "project_id": ctx.project_id,
            "agent_name": ctx.agent_name,
            "kind": "approval",
            "text": f"Approval requested by {ctx.agent_name}: {title}",
        },
    )
    ctx.consume_write()
    return CapabilityResult(
        capability="approvals.request",
        ok=True,
        data={
            "approvalId": approval.id,
            "status": approval.status,
            "title": approval.title,
        },
        approval_id=approval.id,
    )


async def delegate_task(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
    _check_args(arguments, {"title", "owner_agent_id", "notes", "eta_minutes"})
    _write_guard(ctx)
    title = _string(arguments, "title", required=True, max_chars=200)
    owner_agent_id = _string(arguments, "owner_agent_id", required=True, max_chars=64)
    notes = _string(arguments, "notes", max_chars=1000)
    eta_minutes = _integer(arguments, "eta_minutes", default=60, minimum=1, maximum=10080)
    owner = await _agents.get(ctx.session, ctx.business_id, owner_agent_id)
    if owner is None or not owner.hired:
        raise CapabilityError(
            "agent_unavailable",
            "Target agent is not an active member of this business.",
        )
    if owner.id == ctx.agent_id:
        raise CapabilityError("invalid_owner", "An agent cannot delegate to itself.")
    project = await _projects.get(ctx.session, ctx.business_id, ctx.project_id)
    if project is None:
        raise CapabilityError("not_found", "Project is unavailable.")
    team = list(project.team_ids or [])
    if team and owner.id not in team:
        raise CapabilityError(
            "agent_not_in_project",
            "Target agent is not staffed on this project.",
        )
    try:
        approval = await _approvals.create(
            ctx.session,
            ctx.business_id,
            {
                "project_id": ctx.project_id,
                "title": text_limit(f"Approve delegated task: {title}", 300),
                "requested_by": ctx.agent_name,
                "requested_by_agent_id": ctx.agent_id,
                "status": "pending",
                "what": (
                    f"Delegate '{title}' to {owner.name} ({owner.role}). "
                    "The child task is blocked until this approval is granted."
                ),
                "expected_result": text_limit(notes, 500),
                "gates_task_id": None,
            },
        )
        task = await _tasks.create(
            ctx.session,
            ctx.business_id,
            {
                "project_id": ctx.project_id,
                "title": title,
                "owner_agent_id": owner.id,
                "status": TaskStatus.NOT_STARTED.value,
                "summary": notes,
                "eta_minutes": eta_minutes,
                "approval_gate_id": approval.id,
                "depends_on": [ctx.task_id],
            },
        )
        approval.gates_task_id = task.id
        await ctx.session.flush()
    except DanglingReferenceError as exc:
        raise CapabilityError("invalid_reference", str(exc)) from exc
    await _activity.append(
        ctx.session,
        ctx.business_id,
        {
            "project_id": ctx.project_id,
            "agent_name": ctx.agent_name,
            "kind": "approval",
            "text": (
                f"{ctx.agent_name} delegated '{title}' to {owner.name}; "
                f"approval {approval.id} is pending."
            ),
        },
    )
    ctx.consume_write()
    return CapabilityResult(
        capability="task.delegate",
        ok=True,
        data={
            "taskId": task.id,
            "approvalId": approval.id,
            "ownerAgentId": owner.id,
            "status": task.status,
        },
        approval_id=approval.id,
        task_id=task.id,
    )


HANDLERS = {
    "business.context": business_context,
    "project.tasks": project_tasks,
    "task.result": task_result,
    "knowledge.search": knowledge_search,
    "approvals.request": request_approval,
    "task.delegate": delegate_task,
}

__all__ = ["HANDLERS"]
