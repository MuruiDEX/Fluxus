"""Select due projects, then dispatch through the existing task executor."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.execution.executor import ExecutionRequest, ExecutionResult, WorkerAdmission, execute_task
from app.execution.recovery import LEASE_COMPLETE, RECOVERED, lease_state, recover_task_transaction
from app.integrations.repositories import IntegrationActionRepository
from app.llm.base import LlmProvider
from app.persistence.models.approval import Approval
from app.persistence.models.project_run import ProjectRun, RunState
from app.persistence.models.task import Task, TaskDependency
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import (
    ApprovalRepository,
    MembershipRepository,
    TaskRepository,
)
from app.persistence.repositories.project_runs import (
    get_project_run,
    lock_project_admission,
    unresolved_task_hold,
)

log = get_logger(__name__)
_SCAN_DELAY = timedelta(seconds=5)
_RUNNABLE_STATES = (RunState.RUNNING, RunState.WAITING_APPROVAL)
_WAITING_ACTIONS = {"pending", "executing"}
_RECHECK = {"run_candidate_stale", "worker_stopping", "approval_required", "conflict"}


@dataclass(frozen=True)
class RunScope:
    id: str
    business_id: str
    project_id: str


@dataclass(frozen=True)
class Candidate:
    scope: RunScope
    version: int
    task_id: str
    generation: int


@dataclass(frozen=True)
class Selection:
    task: Task | None = None
    state: RunState = RunState.RUNNING
    reason: str | None = None
    blocker: str | None = None


def select_task(
    tasks: Sequence[Task],
    edges: dict[str, list[str]],
    approvals: Sequence[Approval],
    actions: list[dict],
    retry: tuple[str, int] | None = None,
) -> Selection:
    """Validate the complete graph and pick the first ready task by ID."""
    nodes = {task.id: task for task in sorted(tasks, key=lambda task: task.id)}
    gates = {approval.id: approval for approval in approvals}
    by_gate = {action["approval_id"]: action for action in actions}
    if (
        not nodes
        or any(not nodes.keys() >= set(deps) for deps in edges.values())
        or any(
            task.approval_gate_id is not None and task.approval_gate_id not in gates
            for task in nodes.values()
        )
    ):
        return Selection(state=RunState.NEEDS_ATTENTION, reason="invalid_plan")
    try:
        TopologicalSorter({task_id: edges.get(task_id, ()) for task_id in nodes}).prepare()
    except CycleError:
        return Selection(state=RunState.NEEDS_ATTENTION, reason="cyclic_plan")

    retry_task = nodes.get(retry[0]) if retry is not None else None
    if retry is not None and (
        retry_task is None
        or retry_task.execution_generation != retry[1]
        or retry_task.status not in ("failed", "not_started")
    ):
        return Selection(
            state=RunState.NEEDS_ATTENTION, reason="retry_generation_conflict", blocker=retry[0]
        )
    for task in nodes.values() if retry is None else ():
        if task.status == "failed":
            return Selection(state=RunState.NEEDS_ATTENTION, reason="task_failed", blocker=task.id)
        if task.status == "working" or (
            task.status != "completed" and (task.execution_generation > 0 or task.attempts > 0)
        ):
            return Selection(
                state=RunState.NEEDS_ATTENTION, reason="recovery_required", blocker=task.id
            )
    for action in actions:
        if action["status"] not in _WAITING_ACTIONS | {"succeeded"}:
            return Selection(
                state=RunState.NEEDS_ATTENTION,
                reason=f"external_action_{action['status']}",
                blocker=action["id"],
            )

    for task in nodes.values():
        if (task.status != "not_started" and retry != (task.id, task.execution_generation)) or any(
            nodes[dep].status != "completed" for dep in edges.get(task.id, ())
        ):
            continue
        gate = gates.get(task.approval_gate_id)
        action = by_gate.get(task.approval_gate_id)
        if (gate is None or gate.status == "approved") and (
            action is None or action["status"] == "succeeded"
        ):
            if retry is None or retry == (task.id, task.execution_generation):
                return Selection(task=task)

    pending = next((gate for gate in approvals if gate.status == "pending"), None)
    if pending is not None:
        return Selection(
            state=RunState.WAITING_APPROVAL, reason="approval_pending", blocker=pending.id
        )
    action = next((action for action in actions if action["status"] in _WAITING_ACTIONS), None)
    if action is not None:
        return Selection(
            state=RunState.WAITING_APPROVAL, reason="external_action_pending", blocker=action["id"]
        )
    rejected = next((gate for gate in approvals if gate.status == "rejected"), None)
    if rejected is not None:
        return Selection(
            state=RunState.NEEDS_ATTENTION, reason="approval_rejected", blocker=rejected.id
        )
    if all(task.status == "completed" for task in nodes.values()):
        return Selection(state=RunState.COMPLETED)
    return Selection(state=RunState.NEEDS_ATTENTION, reason="tasks_blocked")


async def _now(session: AsyncSession) -> datetime:
    return (await session.execute(select(func.clock_timestamp()))).scalar_one()


async def claim_due_run(factory: async_sessionmaker[AsyncSession]) -> RunScope | None:
    """Advance a scan timestamp with a short row lock. No execution is granted here."""
    async with factory() as session, session.begin():
        now = await _now(session)
        run = await session.scalar(
            select(ProjectRun)
            .where(
                ProjectRun.next_check_at <= now,
                or_(
                    ProjectRun.state.in_(_RUNNABLE_STATES),
                    ProjectRun.active_task_id.is_not(None),
                ),
            )
            .order_by(ProjectRun.next_check_at, ProjectRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        run.next_check_at = now + _SCAN_DELAY
        return RunScope(run.id, run.business_id, run.project_id)


async def _lock_run(session: AsyncSession, scope: RunScope) -> ProjectRun | None:
    await lock_project_admission(session, scope.business_id, scope.project_id)
    run = await get_project_run(session, scope.business_id, scope.project_id)
    return run if run is not None and run.id == scope.id else None


def _record(
    run: ProjectRun,
    selection: Selection,
    next_check_at: datetime | None,
) -> None:
    if run.state != selection.state:
        run.state = selection.state
        run.version += 1
    run.reason_code = selection.reason
    run.blocker_id = selection.blocker
    run.next_check_at = next_check_at


def _attention(run: ProjectRun, reason: str, blocker: str | None = None) -> None:
    state = RunState.NEEDS_ATTENTION if run.state in _RUNNABLE_STATES else RunState(run.state)
    _record(run, Selection(state=state, reason=reason, blocker=blocker), None)


async def _observe_active(
    session: AsyncSession, run: ProjectRun, now: datetime, settings: Settings
) -> bool:
    """Return true only when the recorded attempt completed and was settled."""
    task_id, generation = run.active_task_id, run.active_task_generation
    task = await TaskRepository().lock_for_update(session, run.business_id, task_id)
    expected_generation = run.recovered_task_generation or generation
    if (
        task is None
        or task.project_id != run.project_id
        or task.execution_generation != expected_generation
    ):
        _attention(run, "task_generation_mismatch", task_id)
        return False
    if task.status == "working":
        if (
            run.recovered_task_generation is not None
            or lease_state(task) != LEASE_COMPLETE
            or task.lease_expires_at > task.lease_deadline_at
        ):
            _attention(run, "inconsistent_lease", task_id)
            return False
        if task.lease_expires_at > now:
            run.next_check_at = now + _SCAN_DELAY
            return False
        outcome = await recover_task_transaction(
            session, run.business_id, run.project_id, task_id, settings
        )
        if outcome.outcome != RECOVERED:
            if outcome.reason == "lease_active":
                run.next_check_at = now + _SCAN_DELAY
            else:
                _attention(run, outcome.reason or "lease_recovery_required", task_id)
            return False
        run.recovered_task_generation = outcome.generation
        await session.refresh(task)
    statuses = (
        await session.scalars(
            select(BudgetReservation.status)
            .where(
                BudgetReservation.business_id == run.business_id,
                BudgetReservation.project_id == run.project_id,
                BudgetReservation.task_id == task_id,
                BudgetReservation.execution_generation == generation,
            )
            .limit(2)
        )
    ).all()
    reservation = statuses[0] if len(statuses) == 1 else None
    if reservation not in ("consumed", "released", "reconciled"):
        _attention(run, "ambiguous_execution", task_id)
        return False
    if task.status == "completed" and reservation != "consumed":
        _attention(run, "execution_settlement_mismatch", task_id)
        return False
    recovered = run.recovered_task_generation is not None
    run.recovered_task_generation = None
    run.active_task_id = None
    run.active_task_generation = None
    if task.status != "completed":
        reason = (
            "retry_required"
            if recovered
            else ("task_failed" if task.status == "failed" else "attempt_interrupted")
        )
        _attention(run, reason, task_id)
        return False
    run.next_check_at = now if run.state in _RUNNABLE_STATES else None
    return True


async def prepare_candidate(
    factory: async_sessionmaker[AsyncSession],
    scope: RunScope,
    settings: Settings | None = None,
) -> Candidate | None:
    async with factory() as session, session.begin():
        run = await _lock_run(session, scope)
        if run is None:
            return None
        now = await _now(session)
        if run.active_task_id is not None and not await _observe_active(
            session, run, now, settings or get_settings()
        ):
            return None
        if run.state not in _RUNNABLE_STATES:
            return None
        if not await MembershipRepository().has_access(
            session, run.authorizing_user_id, run.business_id
        ):
            _attention(run, "access_revoked")
            return None
        unresolved = await unresolved_task_hold(session, scope.business_id, scope.project_id)
        if unresolved is not None:
            _attention(run, "ambiguous_execution", unresolved)
            return None

        tasks = await TaskRepository().list_for_project(
            session, scope.business_id, scope.project_id, limit=None
        )
        edges: dict[str, list[str]] = {}
        rows = await session.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_id)
            .join(Task, Task.id == TaskDependency.task_id)
            .where(Task.business_id == scope.business_id, Task.project_id == scope.project_id)
        )
        for task_id, dep_id in rows:
            edges.setdefault(task_id, []).append(dep_id)
        approvals = await ApprovalRepository().list_for_project(
            session, scope.business_id, scope.project_id, limit=None
        )
        actions = await IntegrationActionRepository().report_facts(
            session, scope.business_id, scope.project_id
        )
        retry = (
            (run.retry_task_id, run.retry_task_generation)
            if run.retry_task_id is not None
            else None
        )
        selection = select_task(tasks, edges, approvals, actions, retry)
        _record(run, selection, now + _SCAN_DELAY if selection.state in _RUNNABLE_STATES else None)
        if selection.task is None:
            return None
        return Candidate(scope, run.version, selection.task.id, selection.task.execution_generation)


async def _finish_candidate(
    factory: async_sessionmaker[AsyncSession],
    candidate: Candidate,
    result: ExecutionResult,
    settings: Settings,
) -> None:
    async with factory() as session, session.begin():
        run = await _lock_run(session, candidate.scope)
        if run is None:
            return
        if run.active_task_id is not None:
            if (run.active_task_id, run.active_task_generation) == (
                candidate.task_id,
                candidate.generation + 1,
            ):
                await _observe_active(session, run, await _now(session), settings)
            return
        if (
            run.state != RunState.RUNNING
            or run.version != candidate.version
            or result.outcome not in ("refused", "unknown")
            or result.error_code in _RECHECK
        ):
            return
        task = await TaskRepository().get(session, run.business_id, candidate.task_id)
        if task is not None and task.execution_generation == candidate.generation:
            _attention(run, result.error_code or "execution_refused", task.id)


async def run_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    provider_factory: Callable[[Settings], LlmProvider],
    stopping: asyncio.Event,
) -> bool:
    if stopping.is_set():
        return False
    scope = await claim_due_run(factory)
    if scope is None:
        return False
    candidate = await prepare_candidate(factory, scope, settings)
    if candidate is None or stopping.is_set():
        return True
    async with factory() as session:
        result = await execute_task(
            session,
            provider_factory(settings),
            ExecutionRequest(
                scope.business_id,
                scope.project_id,
                candidate.task_id,
                timeout_s=min(settings.llm_timeout_s, settings.execution_max_timeout_s),
                settings=settings,
                session_factory=factory,
                worker=WorkerAdmission(scope.id, candidate.version, candidate.generation, stopping),
            ),
        )
    await _finish_candidate(factory, candidate, result, settings)
    log.info(
        "worker task outcome",
        extra={
            "context": {
                "run_id": scope.id,
                "project_id": scope.project_id,
                "task_id": candidate.task_id,
                "outcome": result.outcome,
                "code": result.error_code,
            }
        },
    )
    return True
