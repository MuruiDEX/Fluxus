"""Single-task execution: claim в†’ context в†’ LLM в†’ validate в†’ persist.

Transaction discipline (binding):
- txn1 (claim): atomic conditional UPDATE, committed BEFORE any network call.
  No transaction or row lock is ever held across the LLM request.
- LLM call: outside all transactions.
- txn2 (persist): status + summary + activity event + spend recalculation,
  committed atomically. One aggregate per flush (flush-anomaly constraint).
- Compensation (cancel / persist failure): separate txn, only after proving
  ownership вЂ” status still `working` AND attempts unchanged since our claim.
  Anything else ( С‡СѓР¶РѕР№ or newer run touched the row) в†’ hands off, report
  `unknown`, never reset С‡СѓР¶РѕР№ work.

Stale ownership (S2.2.4.6): when the compensation fence proves our generation
is gone, the old execution settles only its OWN reservation by exact identity
(business, reservation id, task, attempt key, integer generation). It becomes
`ambiguous` unless provider evidence proves the request was never sent, in which
case it is `released`. No task row, ledger row, task cost or spend is written;
`ambiguous` stays part of pending budget.

Cancellation: `asyncio.CancelledError` is never converted or retried. After a
confirmed-safe compensation the original CancelledError is re-raised so
structured concurrency (TaskGroup / wait_for) keeps working; the cleanup is
observable in the database, not in a swallowed result.
"""

import asyncio
import json
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.execution import prompts
from app.execution.heartbeat import LeaseHeartbeat
from app.execution.prompts import (
    MAX_ENVELOPE_CHARS,
    RESULT_SCHEMA,
    build_envelope,
    build_system_prompt,
    build_user_prompt,
    fit_envelope,
)
from app.llm.base import LlmProvider
from app.llm.types import (
    BadResponseError,
    LlmConfigurationError,
    LlmError,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    StructuredOutput,
)
from app.persistence.engine import session_factory as make_session_factory
from app.persistence.enums import TaskStatus, UsageCallKind
from app.persistence.models.agent import Agent
from app.persistence.models.business import Business
from app.persistence.models.project import Project
from app.persistence.models.task import Task, TaskDependency
from app.persistence.repositories.base import quantize_money
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    ApprovalRepository,
    BusinessRepository,
    ProjectRepository,
    TaskOwnership,
    TaskRepository,
)
from app.persistence.repositories.spend import recalculate_project_spend
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage import pricing
from app.usage.settlement import (
    SettlementRejected,
    mark_ambiguous,
    price_response,
    release_reservation,
    settle_estimated_failure,
    settle_success,
)

_budget_reservations = BudgetReservationRepository()

log = get_logger(__name__)

MAX_ATTEMPTS = 3  # initial try + reruns; attempts counts completed failures

# Statuses the executor may claim from. Everything else is refused without
# touching the row: completed в†’ no-op, working в†’ already-running,
# waiting_* в†’ not runnable by this executor.
_CLAIMABLE = (TaskStatus.NOT_STARTED.value, TaskStatus.FAILED.value)

# Update() field allowlist for the finish path. Anything else (notably
# ai_spent вЂ” additionally guarded in BaseRepository.update) is rejected here.
_FINISH_FIELDS = {"status", "summary", "progress", "attempts"}

# Error classes that may NOT be retried by re-running: configuration problems
# fail before any claim is made, so they never reach the attempts budget.
_NONSTARTERS = (LlmConfigurationError,)


@dataclass
class ExecutionRequest:
    business_id: str
    project_id: str
    task_id: str
    timeout_s: float | None = None
    # Optional injected settings (tests); production passes app settings so the
    # output cap / unpriced reserve / tariff config match the running app.
    settings: Settings | None = None
    # Separate-session factory for the lease heartbeat. Production passes the
    # app factory; tests may leave it unset when the session is engine-bound.
    session_factory: async_sessionmaker[AsyncSession] | None = None


@dataclass
class ExecutionResult:
    outcome: str  # completed | failed | already_completed | already_running
    #            | refused | unknown
    task_id: str
    status: str | None = None
    attempts: int | None = None
    llm_attempts: int = 0
    usage: dict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    compensated: bool = False
    # Explicit signal for the next slice: the lease was lost while work was in
    # flight, so status/settlement were deliberately left untouched.
    ownership_lost: bool = False


@dataclass
class _Claim:
    task_id: str
    pre_status: str
    pre_attempts: int
    project_id: str = ""
    # Stable per-attempt identity derived BEFORE the network call and stored on
    # the reservation, so a crash+recovery re-derives the same key (no second
    # ledger row for the same attempt).
    attempt_key: str = ""
    # Immutable ownership granted by the successful claim (Stage 18 S2.2.4.3:
    # every persistence path proves it still matches). None only before/without
    # a claim, in which case no execution state may be written.
    ownership: TaskOwnership | None = None


class _BudgetRaceLost(Exception):
    """Reservation lost the budget race; the surrounding txn must roll back."""


class _BudgetPolicyRefused(Exception):
    """Current (locked) project policy refuses the call before the provider."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _OwnershipLost(Exception):
    """The lease heartbeat proved the caller no longer owns the task."""


def _heartbeat_factory(session: AsyncSession) -> async_sessionmaker[AsyncSession] | None:
    """Build a factory for heartbeat sessions bound to the same engine/pool."""
    bind = session.bind
    if isinstance(bind, AsyncEngine):
        return make_session_factory(bind)
    return None


def _ownership_lost_result(task_id: str, status: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        outcome="unknown",
        task_id=task_id,
        status=status,
        error_code="ownership_lost",
        error_message=(
            "Lease ownership was lost during execution; no task status was "
            "written and only this execution's reservation was settled "
            "conservatively. Manual review required."
        ),
        ownership_lost=True,
    )


async def _complete_with_ownership(
    provider: LlmProvider,
    request: LlmRequest,
    heartbeat: LeaseHeartbeat,
) -> LlmResponse:
    """Run the single provider call, aborting it if ownership is lost.

    The provider is cancelled as soon as the heartbeat signals loss, so no
    additional provider attempt can start. If it already completed, its
    result is discarded rather than persisted under stale ownership.
    """
    provider_task = asyncio.create_task(provider.complete(request))
    lost_task = asyncio.create_task(heartbeat.wait_lost())
    try:
        done, _ = await asyncio.wait(
            (provider_task, lost_task), return_when=asyncio.FIRST_COMPLETED
        )
        if lost_task in done:
            if not provider_task.done():
                provider_task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await provider_task
            raise _OwnershipLost
        lost_task.cancel()
        with suppress(asyncio.CancelledError):
            await lost_task
        return await provider_task
    finally:
        for task in (provider_task, lost_task):
            if not task.done():
                task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await task


def _project_reservation_policy(
    project: Project,
    bound: pricing.CostQuote | None,
    settings: Settings,
) -> tuple[Decimal, str]:
    """Choose amount/mode from the CURRENT project row, under its lock.

    A stale context snapshot must never decide that an unbudgeted project gets
    a zero-sized `guaranteed` hold after a concurrent PATCH activates a budget.
    """
    ai_budget = project.ai_budget if project.ai_budget is not None else Decimal("0")
    if bound is not None and bound.cost is not None:
        return bound.cost, "guaranteed"
    if ai_budget > 0 and not project.allow_unpriced_ai:
        raise _BudgetPolicyRefused(
            "budget_unpriced",
            "Project has an AI budget but this model has no priceable tariff; "
            "set a tariff or enable allow_unpriced_ai.",
        )
    if project.allow_unpriced_ai and ai_budget > 0:
        # best_effort: internal reserve only, NOT a proven maximum.
        return quantize_money(settings.usage_unpriced_reserve_amount), "best_effort"
    # No budget to protect and no bound: nothing is guaranteed.
    return Decimal("0"), "best_effort"


_tasks = TaskRepository()
_agents = AgentRepository()
_projects = ProjectRepository()
_businesses = BusinessRepository()
_approvals = ApprovalRepository()
_activity = ActivityRepository()


async def _load_context(
    session: AsyncSession, business_id: str, project_id: str, task_id: str
) -> tuple[Task, Agent, Project, Business] | None:
    """Load task + owner agent + project + business, all in one scope."""
    task = await _tasks.get(session, business_id, task_id)
    if task is None or task.project_id != project_id:
        return None
    agent = await _agents.get(session, business_id, task.owner_agent_id)
    project = await _projects.get(session, business_id, project_id)
    business = await _businesses.get(session, business_id, business_id)
    if agent is None or project is None or business is None:
        return None
    return task, agent, project, business


async def _dependencies_completed(
    session: AsyncSession, business_id: str, task_id: str
) -> bool | None:
    """True if all deps completed; None if a dep reference dangles (corrupt data)."""
    dep_ids = (
        (
            await session.execute(
                select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)
            )
        )
        .scalars()
        .all()
    )
    if not dep_ids:
        return True
    rows = (
        await session.execute(
            select(Task.id, Task.status, Task.business_id).where(Task.id.in_(dep_ids))
        )
    ).all()
    found = {row[0]: (row[1], row[2]) for row in rows}
    for dep_id in dep_ids:
        hit = found.get(dep_id)
        if hit is None or hit[1] != business_id:
            return None
        if hit[0] != TaskStatus.COMPLETED.value:
            return False
    return True


async def _append_event(
    session: AsyncSession,
    business_id: str,
    project_id: str,
    agent_name: str,
    kind: str,
    text: str,
    cost: Decimal | None = None,
) -> None:
    await _activity.append(
        session,
        business_id,
        {
            "project_id": project_id,
            "agent_name": agent_name,
            "kind": kind,
            "text": text,
            "cost": cost,
        },
    )


def _usage_dict(response_usage: object) -> dict:
    usage = getattr(response_usage, "model_dump", None)
    if callable(usage):
        data = response_usage.model_dump(exclude_none=False)  # type: ignore[union-attr]
        return {
            k: (str(v) if k == "cost_estimate" and v is not None else v) for k, v in data.items()
        }
    return {}


def _cancel_requested() -> bool:
    """True when the current task has a pending cancellation.

    Cancellation can surface as a foreign exception (e.g. asyncpg may raise
    a connection error instead of delivering CancelledError). Checking the
    task flag keeps cancellation semantics exact: a requested cancel is never
    mistaken for a regular failure, and structured concurrency keeps working.
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    return task is not None and task.cancelling() > 0


async def _on_cancelled(
    session: AsyncSession,
    business_id: str,
    claim: _Claim,
    reservation_id: str | None = None,
) -> None:
    """Compensate a cancelled run, then raise CancelledError. Never returns.

    Cancellation mid-flight may leave the provider call unanswered — a
    possibly-billed outcome — so the reservation is settled `ambiguous`
    (held, never auto-released), not released.
    """
    try:
        await _compensate_claim(
            session, business_id, claim, reservation_id=reservation_id, settle="ambiguous"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("cancel compensation failed", extra={"context": {"task_id": claim.task_id}})
    raise asyncio.CancelledError()


async def _settle_stale_reservation(
    session: AsyncSession,
    business_id: str,
    claim: _Claim,
    *,
    reservation_id: str | None,
    settle: str,
) -> bool:
    """Settle ONLY this execution's reservation after task ownership is lost.

    The task row belongs to the current owner, so it is never written here. The
    old execution's own hold, however, must not stay `active` forever. The
    transition is one CAS keyed by the full identity — business, reservation id,
    task, attempt key and exact integer generation — so it can never settle a
    newer generation, a legacy NULL-generation row, another attempt or another
    business. It writes no ledger row, task cost or project spend; an
    `ambiguous` hold remains part of pending budget.

    `settle` must reflect provider evidence: `release` only when the request
    provably never reached the provider, otherwise `ambiguous`. An unknown value
    is treated as `ambiguous`. Already `ambiguous`, `released`, `consumed` or
    `written_off` rows match no `active` predicate, so repeated or racing
    compensation is a safe no-op.
    """
    ownership = claim.ownership
    if ownership is None or reservation_id is None:
        return False
    generation = ownership.execution_generation
    # `set_status` omits the generation predicate when given None. Never let
    # that legacy fallback widen a stale-owner transition: this path is valid
    # only for a positive integer generation. NULL is never zero.
    if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        return False
    if settle not in ("ambiguous", "release"):
        settle = "ambiguous"
    try:
        async with session.begin():
            if settle == "release":
                return await release_reservation(
                    session,
                    business_id,
                    reservation_id,
                    "ownership_lost_pre_send",
                    task_id=claim.task_id,
                    attempt_key=claim.attempt_key,
                    execution_generation=generation,
                )
            return await mark_ambiguous(
                session,
                business_id,
                reservation_id,
                "ownership_lost",
                task_id=claim.task_id,
                attempt_key=claim.attempt_key,
                execution_generation=generation,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning(
            "stale reservation settlement failed",
            exc_info=True,
            extra={
                "context": {
                    "task_id": claim.task_id,
                    "reservation_id": reservation_id,
                }
            },
        )
        return False


async def _compensate_claim(
    session: AsyncSession,
    business_id: str,
    claim: _Claim,
    *,
    reservation_id: str | None = None,
    settle: str | None = None,
) -> bool:
    """Revert OUR claim only, fenced by the exact ownership tuple.

    The fence (`working` + generation + owner + live lease) is the
    authoritative check: a stale worker can never overwrite a newer execution,
    even if its status/attempts happen to match. On zero rows no task state is
    written at all — no task update, no event, no spend. The old execution's OWN
    reservation is still settled by exact generation (S2.2.4.6): `release` when
    the caller proved a pre-send outcome, otherwise `ambiguous`.
    """
    ownership = claim.ownership
    if ownership is None:
        return False
    stale = False
    async with session.begin():
        fenced = await _tasks.fence_owned(
            session,
            business_id,
            claim.task_id,
            execution_generation=ownership.execution_generation,
            lease_owner=ownership.lease_owner,
            values={"status": claim.pre_status},
        )
        if not fenced:
            stale = True
        else:
            await _append_event(
                session,
                business_id,
                claim.project_id,
                "executor",
                "system",
                f"Task {claim.task_id} reverted to {claim.pre_status} after cancelled run.",
            )
            if reservation_id is not None and settle == "ambiguous":
                await mark_ambiguous(
                    session,
                    business_id,
                    reservation_id,
                    "cancelled",
                    task_id=claim.task_id,
                    attempt_key=claim.attempt_key,
                    execution_generation=ownership.execution_generation,
                )
            elif reservation_id is not None and settle == "release":
                await release_reservation(
                    session,
                    business_id,
                    reservation_id,
                    "pre_send",
                    task_id=claim.task_id,
                    attempt_key=claim.attempt_key,
                    execution_generation=ownership.execution_generation,
                )
    if stale:
        # The task fence proved our generation is gone. Never touch the task,
        # but do not leave our own hold stuck `active` forever either.
        await _settle_stale_reservation(
            session,
            business_id,
            claim,
            reservation_id=reservation_id,
            settle=settle or "ambiguous",
        )
    return not stale


async def execute_task(
    session: AsyncSession, provider: LlmProvider, request: ExecutionRequest
) -> ExecutionResult:
    if session.in_transaction():
        # The executor owns its transaction boundaries (claim must commit
        # before any network call). A caller-held transaction would silently
        # downgrade every phase to savepoints — durable claims included — so
        # this is a loud error, not a nested transaction. Commit or roll back
        # caller state first.
        raise RuntimeError("execute_task requires a session without an open transaction")
    try:
        return await _execute_inner(session, provider, request)
    finally:
        # Leave the session without an open transaction: all writes above
        # committed explicitly; only trailing reads may linger. Best-effort:
        # never mask the real outcome (or cancellation) with cleanup noise.
        try:
            await session.rollback()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def _execute_inner(
    session: AsyncSession, provider: LlmProvider, request: ExecutionRequest
) -> ExecutionResult:
    business_id, project_id, task_id = request.business_id, request.project_id, request.task_id
    settings = request.settings or get_settings()
    provider_name = getattr(provider, "name", "unknown")
    model_name = getattr(provider, "_default_model", "mock")
    heartbeat_factory = request.session_factory or _heartbeat_factory(session)
    if heartbeat_factory is None:
        # The claim grants a lease; without a separate session factory the
        # heartbeat cannot re-prove ownership. Fail before any write.
        raise RuntimeError(
            "execute_task requires an engine-bound session or an explicit session_factory"
        )

    # ---- Phase 0: load + dispatch (reads only, no writes, no attempts) ----
    loaded = await _load_context(session, business_id, project_id, task_id)
    if loaded is None:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            error_code="not_found",
            error_message="Task (or its agent/project/business) not found in scope.",
        )
    task, agent, project, business = loaded
    if task.status == TaskStatus.COMPLETED.value:
        return ExecutionResult(
            outcome="already_completed",
            task_id=task_id,
            status=task.status,
            attempts=task.attempts,
        )
    if task.status == TaskStatus.WORKING.value:
        return ExecutionResult(
            outcome="already_running",
            task_id=task_id,
            status=task.status,
            attempts=task.attempts,
            error_code="conflict",
            error_message="Task is already being executed.",
        )
    if task.status == TaskStatus.FAILED.value and task.attempts >= MAX_ATTEMPTS:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=task.status,
            attempts=task.attempts,
            error_code="conflict",
            error_message=(
                f"Attempt budget exhausted ({task.attempts}/{MAX_ATTEMPTS}); "
                "human decision required."
            ),
        )
    if task.status not in _CLAIMABLE:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=task.status,
            attempts=task.attempts,
            error_code="conflict",
            error_message=f"Task status {task.status!r} is not runnable by this executor.",
        )
    if not agent.hired:
        # Disabled owners take no NEW work: in-flight runs are untouched
        # (working short-circuits above), history is untouched, but no fresh
        # claim may start. Terminal tasks answer idempotently above.
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=task.status,
            attempts=task.attempts,
            error_code="agent_disabled",
            error_message=f"Owner agent {agent.id!r} is disabled.",
        )

    # ---- Phase 1: pre-checks (reads only; failures touch nothing) ----
    async def _refuse(code: str, message: str) -> ExecutionResult:
        # Snapshot while the read transaction is still open, then discard it:
        # nothing was written yet, so rollback loses nothing and the event
        # write below is a top-level commit, not a savepoint.
        status, attempts, agent_name = task.status, task.attempts, agent.name
        await session.rollback()
        async with session.begin():
            await _append_event(
                session,
                business_id,
                project_id,
                agent_name,
                "system",
                f"Execution refused for task {task_id}: {message}",
            )
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=status,
            attempts=attempts,
            error_code=code,
            error_message=message,
        )

    deps = await _dependencies_completed(session, business_id, task_id)
    if deps is None:
        return await _refuse("internal", "Task has a dangling dependency reference.")
    if deps is False:
        return await _refuse("conflict", "Task dependencies are not completed.")
    if task.approval_gate_id is not None:
        gate = await _approvals.get(session, business_id, task.approval_gate_id)
        if gate is None:
            return await _refuse("internal", "Task references a missing approval gate.")
        if gate.status != "approved":
            return await _refuse(
                "approval_required",
                f"Approval gate '{gate.title}' is {gate.status}; human approval required.",
            )
    # Fast-path refusal for an already-exhausted snapshot. The authoritative
    # budget/opt-in decision is still made later under the project row lock;
    # this snapshot must not decide reservation size or mode (Stage 18 C3).
    ai_budget = project.ai_budget if project.ai_budget is not None else 0
    if ai_budget > 0 and (project.ai_spent or 0) >= ai_budget:
        return await _refuse(
            "budget_exceeded",
            f"Project AI budget exhausted ({project.ai_spent}/{ai_budget}).",
        )
    # A conservative bound requires a known tariff AND a capped output.
    input_bound = pricing.estimate_input_token_bound(
        build_system_prompt(agent.role, list(agent.responsibilities or [])),
        build_user_prompt(
            business.name,
            project.title,
            project.goal or "",
            task.title,
            task.summary or "",
        ),
        json.dumps(dict(RESULT_SCHEMA)),
        message_count=2,
    )
    bound = pricing.upper_bound_cost(
        provider=provider_name,
        model=model_name,
        input_token_bound=input_bound,
        max_output_tokens=settings.llm_max_tokens,
    )

    # ---- Phase 2: atomic claim, committed BEFORE any network call ----
    # Snapshot every value later phases need (plain data, no ORM state):
    # the rollback below expires all loaded objects, and later attribute
    # access must never implicitly reopen a transaction mid-flow.
    agent_name = agent.name
    agent_role = agent.role
    agent_resp = list(agent.responsibilities or [])
    biz_name = business.name
    proj_title, proj_goal = project.title, project.goal or ""
    task_title, task_notes = task.title, task.summary or ""
    pre_status, pre_attempts = task.status, task.attempts
    agent_id = agent.id
    await session.rollback()
    attempt_key = f"{business_id}:{task_id}:a{pre_attempts}"
    claim = _Claim(
        task_id=task_id,
        pre_status=pre_status,
        pre_attempts=pre_attempts,
        project_id=project_id,
        attempt_key=attempt_key,
    )
    # Claim + budget reservation commit together, BEFORE any network call.
    # A lost budget race rolls the claim back with it (no dangling `working`).
    # Only plain IDs survive the transaction: ORM instances are expired on
    # commit/rollback and must never be touched across boundaries.
    reservation_id: str | None = None
    reservation_key: str | None = None
    ownership: TaskOwnership | None = None
    claimed = False
    lease_owner = f"executor:{uuid.uuid4().hex}"
    try:
        async with session.begin():
            ownership = await _tasks.claim_owned(
                session,
                business_id,
                task_id,
                from_statuses=_CLAIMABLE,
                to_status=TaskStatus.WORKING.value,
                lease_owner=lease_owner,
                lease_ttl_seconds=settings.execution_lease_ttl_s,
                lease_wall_seconds=settings.execution_lease_wall_s,
            )
            claimed = ownership is not None
            if claimed:
                # The exact tuple every later persistence path must present.
                claim.ownership = ownership
                # Re-read under the same project lock used by `reserve`.
                # A PATCH that landed after Phase 0 must be reflected in the
                # amount/mode decision, otherwise a zero hold could bypass a
                # newly activated budget (Stage 18 C3).
                locked_project = (
                    await session.execute(
                        select(Project)
                        .where(
                            Project.business_id == business_id,
                            Project.id == project_id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if locked_project is None:
                    raise _BudgetRaceLost()
                reserve_amount, reserve_mode = _project_reservation_policy(
                    locked_project, bound, settings
                )
                reservation = await _budget_reservations.reserve(
                    session,
                    business_id,
                    project_id=project_id,
                    task_id=task_id,
                    attempt=pre_attempts,
                    attempt_key=attempt_key,
                    amount=reserve_amount,
                    mode=reserve_mode,
                    execution_generation=ownership.execution_generation,
                )
                if reservation is None:
                    raise _BudgetRaceLost()
                reservation_id = reservation.id
                reservation_key = reservation.idempotency_key
    except _BudgetPolicyRefused as exc:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=pre_status,
            attempts=pre_attempts,
            error_code=exc.code,
            error_message=exc.message,
        )
    except _BudgetRaceLost:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=pre_status,
            attempts=pre_attempts,
            error_code="budget_exceeded",
            error_message=(
                "Concurrent execution exhausted the project AI budget; reservation refused."
            ),
        )
    if not claimed:
        # Lost a race (or state changed under us): re-read, never assume.
        current = await _tasks.get(session, business_id, task_id)
        status = current.status if current else None
        if status == TaskStatus.COMPLETED.value:
            return ExecutionResult(outcome="already_completed", task_id=task_id, status=status)
        if status == TaskStatus.WORKING.value:
            return ExecutionResult(
                outcome="already_running",
                task_id=task_id,
                status=status,
                error_code="conflict",
                error_message="Task was claimed concurrently.",
            )
        return ExecutionResult(
            outcome="unknown",
            task_id=task_id,
            status=status,
            error_code="internal",
            error_message="Claim lost and state unclear; manual review required.",
        )

    # ---- Phase 3: LLM call вЂ” NO transaction open here ----
    llm_request = LlmRequest(
        messages=[
            LlmMessage(
                role="system",
                content=build_system_prompt(agent_role, agent_resp),
            ),
            LlmMessage(
                role="user",
                content=build_user_prompt(
                    biz_name,
                    proj_title,
                    proj_goal,
                    task_title,
                    task_notes,
                ),
            ),
        ],
        model=model_name,
        max_tokens=settings.llm_max_tokens,
        temperature=None,
        structured=StructuredOutput(mode="json_schema", schema=dict(RESULT_SCHEMA)),
        timeout_s=request.timeout_s or 30.0,
        idempotency_key=reservation_key or attempt_key,
    )
    if ownership is None:  # defensive: `claimed` is only True with a returned row
        raise RuntimeError("claim succeeded without ownership data")
    heartbeat = LeaseHeartbeat(
        session_factory=heartbeat_factory,
        ownership=ownership,
        ttl_seconds=settings.execution_lease_ttl_s,
        interval_seconds=settings.execution_heartbeat_interval_s,
    )
    heartbeat.start()
    try:
        try:
            response = await _complete_with_ownership(provider, llm_request, heartbeat)
        finally:
            # No heartbeat may run while status/settlement is persisted.
            await heartbeat.stop()
        if heartbeat.ownership_lost:
            await _settle_stale_reservation(
                session,
                business_id,
                claim,
                reservation_id=reservation_id,
                settle="ambiguous",
            )
            return _ownership_lost_result(task_id, TaskStatus.WORKING.value)
    except _OwnershipLost:
        await _settle_stale_reservation(
            session,
            business_id,
            claim,
            reservation_id=reservation_id,
            settle="ambiguous",
        )
        return _ownership_lost_result(task_id, TaskStatus.WORKING.value)
    except asyncio.CancelledError:
        if heartbeat.ownership_lost:
            # The row is no longer ours: the task must not be written. The
            # provider may already have received the request, so settle only
            # our own reservation as held, then propagate the cancellation.
            await _settle_stale_reservation(
                session,
                business_id,
                claim,
                reservation_id=reservation_id,
                settle="ambiguous",
            )
            raise
        await _on_cancelled(session, business_id, claim, reservation_id)
    except LlmError as exc:
        if heartbeat.ownership_lost:
            # Provider evidence still classifies the outcome: a bad response
            # (or any explicitly ambiguous error) means the provider may have
            # billed; a non-ambiguous pre-send error is safe to release.
            stale_settle = (
                "ambiguous"
                if isinstance(exc, BadResponseError) or getattr(exc, "ambiguous", False)
                else "release"
            )
            await _settle_stale_reservation(
                session,
                business_id,
                claim,
                reservation_id=reservation_id,
                settle=stale_settle,
            )
            return _ownership_lost_result(task_id, TaskStatus.WORKING.value)
        if isinstance(exc, _NONSTARTERS):
            # Misconfiguration surfacing mid-flight (factory normally fails
            # fast): nothing was sent, so release the reservation, compensate
            # our claim, and propagate — the task must not stay `working`.
            try:
                await _compensate_claim(
                    session,
                    business_id,
                    claim,
                    reservation_id=reservation_id,
                    settle="release",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("cancel compensation failed", extra={"context": {"task_id": task_id}})
            raise
        if _cancel_requested():
            # A cancel arrived but surfaced as a provider error: treat as
            # cancellation, not failure. Attempts are not consumed by this.
            await _on_cancelled(session, business_id, claim, reservation_id)
        # Ambiguous (post-send, possibly billed) → hold; otherwise the provider
        # definitively failed (pre-send network error, or an error status that
        # produced no generation) → safe to release.
        estimate: tuple[int, int | None] | None = None
        if isinstance(exc, BadResponseError) and reservation_id is not None:
            # Post-send malformed response (Stage 16.1): the provider answered,
            # so the call may have been billed and must NEVER be released. Price
            # it `estimated` from the tariff bound, or keep the hold `ambiguous`
            # when it cannot be priced (handled inside the settlement helper).
            estimate = (input_bound, settings.llm_max_tokens)
            settle = "ambiguous"
        else:
            settle = "ambiguous" if getattr(exc, "ambiguous", False) else "release"
        return await _record_failure(
            session,
            business_id,
            project_id,
            task_id,
            agent_name,
            claim,
            exc,
            reservation_id=reservation_id,
            settle=settle,
            estimate=estimate,
            reservation_key=reservation_key,
            provider_name=provider_name,
            model_name=model_name,
            agent_id=agent_id,
        )

    # Adapter already parsed + schema-validated `data`; re-check cheaply here
    # so a provider swap that skips validation cannot store unchecked JSON.
    data = response.data if isinstance(response.data, dict) else None
    summary = str((data or {}).get("summary", response.text))[: prompts.MAX_SUMMARY_CHARS]
    envelope = fit_envelope(
        build_envelope(
            task_id=task_id,
            model=response.model or llm_request.model,
            summary=summary,
            output=data,
            usage=_usage_dict(response.usage),
            attempts=response.attempts,
        )
    )
    envelope_text = json.dumps(envelope, ensure_ascii=False)
    assert len(envelope_text) <= MAX_ENVELOPE_CHARS, "envelope overflow after fit"
    # Activity-feed cost shows the CONFIRMED cost only; estimated/unknown are
    # recorded in the ledger but never presented as settled money.
    event_cost = None
    if reservation_id is not None:
        quote = price_response(provider_name, model_name, response)
        event_cost = quote.cost if quote.status == "confirmed" else None

    # ---- Phase 4: persist (single txn; one aggregate per flush inside repos) ----
    # The authoritative ownership check is the FIRST write: a conditional
    # UPDATE that both fences the transition and applies it. It is never a
    # read-then-update, so a stale worker cannot overwrite a newer execution.
    stale = False
    try:
        async with session.begin():
            fenced = await _tasks.fence_owned(
                session,
                business_id,
                task_id,
                execution_generation=ownership.execution_generation,
                lease_owner=ownership.lease_owner,
                values={
                    "status": TaskStatus.COMPLETED.value,
                    "summary": summary,
                    "progress": 100,
                },
            )
            if not fenced:
                # Zero rows: no result, no event, no ledger, no spend. The
                # provider DID answer, so the old execution's own hold is
                # settled `ambiguous` after this txn (never the task row).
                stale = True
            else:
                await _append_event(
                    session,
                    business_id,
                    project_id,
                    agent_name,
                    "result",
                    envelope_text,
                    event_cost,
                )
                # Ledger row + reservation consume + task-cost recompute, atomic.
                # A successful task must never commit without a valid
                # settlement: `skipped` is escalated to a rejection so the
                # whole persist transaction rolls back and compensation runs.
                settlement = await settle_success(
                    session,
                    business_id,
                    reservation_id=reservation_id,
                    reservation_key=reservation_key or attempt_key,
                    task_id=task_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    call_kind=UsageCallKind.TASK_EXECUTION.value,
                    attempt=pre_attempts,
                    attempt_key=attempt_key,
                    execution_generation=ownership.execution_generation,
                    provider=provider_name,
                    model=model_name,
                    response=response,
                    input_bound_tokens=input_bound,
                    max_output_tokens=settings.llm_max_tokens,
                )
                if settlement.outcome == "skipped":
                    raise SettlementRejected(
                        "settlement_skipped",
                        (
                            f"successful task settlement was skipped "
                            f"({settlement.reason}); refusing to complete"
                        ),
                        wrote=True,
                    )
                await recalculate_project_spend(session, business_id, project_id)
    except asyncio.CancelledError:
        # Our txn rolled back atomically → nothing persisted → compensate.
        await _on_cancelled(session, business_id, claim, reservation_id)
    except Exception as persist_exc:
        if _cancel_requested():
            # Cancellation surfaced as a foreign error (e.g. connection torn
            # down mid-commit): route to the cancel path, not failure accounting.
            await _on_cancelled(session, business_id, claim, reservation_id)
        # Distinguish: rollback happened (atomic) → try to also revert our
        # claim so the task stays retryable; never promise what we can't prove.
        return await _handle_persist_error(
            session,
            business_id,
            claim,
            persist_exc,
            reservation_id=reservation_id,
        )
    if stale:
        # The provider answered but the task is no longer ours: settle ONLY our
        # exact-generation reservation as ambiguous and leave everything else.
        await _settle_stale_reservation(
            session,
            business_id,
            claim,
            reservation_id=reservation_id,
            settle="ambiguous",
        )
        return _ownership_lost_result(task_id)
    final = await _tasks.get(session, business_id, task_id)
    return ExecutionResult(
        outcome="completed",
        task_id=task_id,
        status=final.status if final else None,
        attempts=final.attempts if final else None,
        llm_attempts=response.attempts,
        usage=_usage_dict(response.usage),
    )


async def _handle_persist_error(
    session: AsyncSession,
    business_id: str,
    claim: _Claim,
    exc: BaseException,
    *,
    reservation_id: str | None = None,
) -> ExecutionResult:
    """Three-way distinction after a failed persist transaction.

    - The failed txn itself rolled back atomically (guaranteed by the DB).
    - If compensation (revert to pre-claim status) succeeds, re-raise the
      original error: loud, retryable, truthful.
    - If compensation fails or the row is no longer ours, return `unknown`
      and touch nothing further. Never claim a rollback that didn't happen.

    The provider DID answer here, so the call may have been billed: the
    reservation is settled `ambiguous` (held), never released.
    """
    try:
        compensated = await _compensate_claim(
            session, business_id, claim, reservation_id=reservation_id, settle="ambiguous"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning(
            "persist-failure compensation failed",
            exc_info=True,
            extra={"context": {"task_id": claim.task_id}},
        )
        return ExecutionResult(
            outcome="unknown",
            task_id=claim.task_id,
            status=TaskStatus.WORKING.value,
            error_code="compensation_failed",
            error_message=(
                f"Persist failed ({exc}); compensation also failed. Manual review required."
            ),
        )
    if compensated:
        raise exc
    # The txn rolled back, and the compensation fence proved the row is no
    # longer ours: report ownership loss rather than a generic persist error.
    return _ownership_lost_result(claim.task_id)


async def _record_failure(
    session: AsyncSession,
    business_id: str,
    project_id: str,
    task_id: str,
    agent_name: str,
    claim: _Claim,
    exc: LlmError,
    *,
    reservation_id: str | None = None,
    settle: str = "release",
    estimate: tuple[int, int | None] | None = None,
    reservation_key: str | None = None,
    provider_name: str = "unknown",
    model_name: str = "mock",
    agent_id: str | None = None,
) -> ExecutionResult:
    """Persist a failed execution: status failed + attempts+1, atomically.

    The ownership fence is the first and authoritative write. A stale worker
    (superseded generation/owner, or an expired/deadline-passed lease) matches
    zero rows and writes no task state — no failure status, no attempts
    increment, no event, no ledger, no spend. It still settles ONLY its own
    exact-generation reservation by provider classification (`ambiguous` unless
    the request provably never reached the provider).

    The reservation is settled in the SAME txn as the failure record:
    `ambiguous` when the provider call may have been billed, `release` when it
    provably was not. A held (ambiguous) amount still counts against budget.
    `estimate` (Stage 16.1) records a post-send malformed response as an
    `estimated` ledger row (tariff bound) or, when unpriceable, keeps the hold
    ambiguous — never released, never zero.
    """
    ownership = claim.ownership
    if ownership is None:
        return _ownership_lost_result(task_id)
    stale = False
    try:
        async with session.begin():
            fenced = await _tasks.fence_owned(
                session,
                business_id,
                task_id,
                execution_generation=ownership.execution_generation,
                lease_owner=ownership.lease_owner,
                values={
                    "status": TaskStatus.FAILED.value,
                    "attempts": Task.attempts + 1,
                },
            )
            if not fenced:
                stale = True
            else:
                text = (
                    f"Task {task_id} failed ({exc.code}): {exc}. "
                    f"Attempt {claim.pre_attempts + 1}/{MAX_ATTEMPTS}."
                )
                await _append_event(session, business_id, project_id, agent_name, "system", text)
                if reservation_id is not None and estimate is not None:
                    # A valid or skipped settlement keeps the task FAILED (the
                    # approved post-send contract). A rejection propagates and
                    # rolls back through the existing compensation path.
                    settlement = await settle_estimated_failure(
                        session,
                        business_id,
                        reservation_id=reservation_id,
                        reservation_key=reservation_key,
                        task_id=task_id,
                        project_id=project_id,
                        agent_id=agent_id,
                        call_kind=UsageCallKind.TASK_EXECUTION.value,
                        attempt=claim.pre_attempts,
                        attempt_key=claim.attempt_key,
                        execution_generation=ownership.execution_generation,
                        provider=provider_name,
                        model=model_name,
                        input_bound_tokens=estimate[0],
                        max_output_tokens=estimate[1],
                    )
                    if settlement.outcome != "skipped":
                        await recalculate_project_spend(session, business_id, project_id)
                elif reservation_id is not None and settle == "ambiguous":
                    await mark_ambiguous(
                        session,
                        business_id,
                        reservation_id,
                        exc.code,
                        task_id=task_id,
                        attempt_key=claim.attempt_key,
                        execution_generation=ownership.execution_generation,
                    )
                elif reservation_id is not None:
                    await release_reservation(
                        session,
                        business_id,
                        reservation_id,
                        exc.code,
                        task_id=task_id,
                        attempt_key=claim.attempt_key,
                        execution_generation=ownership.execution_generation,
                    )
    except asyncio.CancelledError:
        await _on_cancelled(session, business_id, claim, reservation_id)
    except Exception as persist_exc:
        if _cancel_requested():
            await _on_cancelled(session, business_id, claim, reservation_id)
        return await _handle_persist_error(
            session, business_id, claim, persist_exc, reservation_id=reservation_id
        )
    if stale:
        # Never write the task after ownership loss; settle only our own
        # reservation using the provider evidence already computed by the caller.
        await _settle_stale_reservation(
            session,
            business_id,
            claim,
            reservation_id=reservation_id,
            settle=settle,
        )
        return _ownership_lost_result(task_id)
    final = await _tasks.get(session, business_id, task_id)
    return ExecutionResult(
        outcome="failed",
        task_id=task_id,
        status=final.status if final else None,
        attempts=final.attempts if final else None,
        error_code=exc.code,
        error_message=str(exc),
    )
