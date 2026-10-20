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

Cancellation: `asyncio.CancelledError` is never converted or retried. After a
confirmed-safe compensation the original CancelledError is re-raised so
structured concurrency (TaskGroup / wait_for) keeps working; the cleanup is
observable in the database, not in a swallowed result.
"""

import asyncio
import json
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.execution import prompts
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
    StructuredOutput,
)
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
    TaskRepository,
)
from app.persistence.repositories.spend import recalculate_project_spend
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage import pricing
from app.usage.settlement import (
    mark_ambiguous,
    price_response,
    record_estimated_failure,
    record_success,
    release_reservation,
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


@dataclass
class _Claim:
    task_id: str
    pre_status: str
    pre_attempts: int
    # Stable per-attempt identity derived BEFORE the network call and stored on
    # the reservation, so a crash+recovery re-derives the same key (no second
    # ledger row for the same attempt).
    attempt_key: str = ""


class _BudgetRaceLost(Exception):
    """Reservation lost the budget race; the surrounding txn must roll back."""


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


async def _compensate_claim(
    session: AsyncSession,
    business_id: str,
    claim: _Claim,
    *,
    reservation_id: str | None = None,
    settle: str | None = None,
) -> bool:
    """Revert OUR claim only. Returns True iff the revert was applied.

    Ownership proof: the row must still be `working` with the exact attempts
    count from claim time. Any deviation means someone else (a newer run, an
    operator, a completed persist) touched it — hands off, report unknown.
    The reservation is settled in the SAME transaction: `ambiguous` when the
    outcome may have billed, `release` only for a proven pre-send no-op.
    """
    async with session.begin():
        current = await _tasks.get(session, business_id, claim.task_id)
        if (
            current is None
            or current.status != TaskStatus.WORKING.value
            or current.attempts != claim.pre_attempts
        ):
            # Ownership unclear: if the call may have been billed, fail closed
            # and HOLD the reservation instead of dropping it.
            if reservation_id is not None and settle == "ambiguous":
                await mark_ambiguous(session, business_id, reservation_id, "uncertain_ownership")
            elif reservation_id is not None and settle == "release":
                await release_reservation(session, business_id, reservation_id, "pre_send")
            return False
        await _tasks.update(session, business_id, claim.task_id, {"status": claim.pre_status})
        await _append_event(
            session,
            business_id,
            current.project_id,
            "executor",
            "system",
            f"Task {claim.task_id} reverted to {claim.pre_status} after cancelled run.",
        )
        if reservation_id is not None and settle == "ambiguous":
            await mark_ambiguous(session, business_id, reservation_id, "cancelled")
        elif reservation_id is not None and settle == "release":
            await release_reservation(session, business_id, reservation_id, "pre_send")
    return True


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
    ai_budget = project.ai_budget if project.ai_budget is not None else 0
    if ai_budget > 0 and (project.ai_spent or 0) >= ai_budget:
        return await _refuse(
            "budget_exceeded",
            f"Project AI budget exhausted ({project.ai_spent}/{ai_budget}).",
        )
    # Guaranteed mode requires a PROVABLE cost upper bound: a known tariff AND a
    # capped output. Without one, strict projects fail closed (`budget_unpriced`
    # at the API); an explicit per-project opt-in downgrades to best-effort.
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
    )
    bound = pricing.upper_bound_cost(
        provider=provider_name,
        model=model_name,
        input_token_bound=input_bound,
        max_output_tokens=settings.llm_max_tokens,
    )
    if bound is not None:
        reserve_amount = bound.cost
        reserve_mode = "guaranteed"
    elif ai_budget > 0 and not project.allow_unpriced_ai:
        return await _refuse(
            "budget_unpriced",
            "Project has an AI budget but this model has no priceable tariff; "
            "set a tariff or enable allow_unpriced_ai.",
        )
    elif project.allow_unpriced_ai and ai_budget > 0:
        # best_effort: internal reserve only, NOT a proven maximum.
        reserve_amount = quantize_money(settings.usage_unpriced_reserve_amount)
        reserve_mode = "best_effort"
    else:
        reserve_amount = None
        reserve_mode = "guaranteed"

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
        attempt_key=attempt_key,
    )
    # Claim + budget reservation commit together, BEFORE any network call.
    # A lost budget race rolls the claim back with it (no dangling `working`).
    # Only plain IDs survive the transaction: ORM instances are expired on
    # commit/rollback and must never be touched across boundaries.
    reservation_id: str | None = None
    reservation_key: str | None = None
    try:
        async with session.begin():
            claimed = await _tasks.claim_task(
                session,
                business_id,
                task_id,
                from_statuses=_CLAIMABLE,
                to_status=TaskStatus.WORKING.value,
            )
            if claimed:
                reservation = await _budget_reservations.reserve(
                    session,
                    business_id,
                    project_id=project_id,
                    task_id=task_id,
                    attempt=pre_attempts,
                    attempt_key=attempt_key,
                    amount=reserve_amount if reserve_amount is not None else Decimal("0"),
                    mode=reserve_mode,
                )
                if reservation is None:
                    raise _BudgetRaceLost()
                reservation_id = reservation.id
                reservation_key = reservation.idempotency_key
    except _BudgetRaceLost:
        return ExecutionResult(
            outcome="refused",
            task_id=task_id,
            status=pre_status,
            attempts=pre_attempts,
            error_code="budget_exceeded",
            error_message=(
                "Concurrent execution exhausted the project AI budget; "
                "reservation refused."
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
    try:
        response = await provider.complete(llm_request)
    except asyncio.CancelledError:
        await _on_cancelled(session, business_id, claim, reservation_id)
    except LlmError as exc:
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
    try:
        async with session.begin():
            current = await _tasks.get(session, business_id, task_id)
            if current is None or current.status != TaskStatus.WORKING.value:
                # Not ours anymore (should be impossible after our claim, but
                # never assume): hold the reservation (may be billed) and report.
                await mark_ambiguous(session, business_id, reservation_id, "ownership_lost")
                return ExecutionResult(
                    outcome="unknown",
                    task_id=task_id,
                    status=current.status if current else None,
                    error_code="internal",
                    error_message="Task left our ownership before persist; manual review required.",
                )
            await _tasks.update(
                session,
                business_id,
                task_id,
                {"status": TaskStatus.COMPLETED.value, "summary": summary, "progress": 100},
            )
            await _append_event(
                session,
                business_id,
                project_id,
                agent_name,
                "result",
                envelope_text,
                event_cost,
            )
            if reservation_id is not None:
                # Ledger row + reservation consume + task-cost recompute, atomic.
                await record_success(
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
                    provider=provider_name,
                    model=model_name,
                    response=response,
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
    return ExecutionResult(
        outcome="unknown",
        task_id=claim.task_id,
        status=TaskStatus.WORKING.value,
        error_code="compensation_failed",
        error_message=f"Persist failed ({exc}); task no longer ours. Manual review required.",
    )


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

    The reservation is settled in the SAME txn as the failure record:
    `ambiguous` when the provider call may have been billed, `release` when it
    provably was not. A held (ambiguous) amount still counts against budget.
    `estimate` (Stage 16.1) records a post-send malformed response as an
    `estimated` ledger row (tariff bound) or, when unpriceable, keeps the hold
    ambiguous — never released, never zero.
    """
    try:
        async with session.begin():
            current = await _tasks.get(session, business_id, task_id)
            if current is None or current.status != TaskStatus.WORKING.value:
                if reservation_id is not None and (settle == "ambiguous" or estimate is not None):
                    await mark_ambiguous(
                        session, business_id, reservation_id, "ownership_lost"
                    )
                elif reservation_id is not None:
                    await release_reservation(
                        session, business_id, reservation_id, "ownership_lost"
                    )
                return ExecutionResult(
                    outcome="unknown",
                    task_id=task_id,
                    status=current.status if current else None,
                    error_code="internal",
                    error_message=(
                        "Task left our ownership before failure persist; manual review required."
                    ),
                )
            # Atomic increment at the DB level: concurrent finishers impossible
            # (single holder by claim), but the increment itself never reads stale.
            await session.execute(
                sa_update(Task)
                .where(Task.business_id == business_id, Task.id == task_id)
                .values(status=TaskStatus.FAILED.value, attempts=Task.attempts + 1)
            )
            await session.flush()
            text = (
                f"Task {task_id} failed ({exc.code}): {exc}. "
                f"Attempt {claim.pre_attempts + 1}/{MAX_ATTEMPTS}."
            )
            await _append_event(session, business_id, project_id, agent_name, "system", text)
            if reservation_id is not None and estimate is not None:
                await record_estimated_failure(
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
                    provider=provider_name,
                    model=model_name,
                    input_bound_tokens=estimate[0],
                    max_output_tokens=estimate[1],
                )
                await recalculate_project_spend(session, business_id, project_id)
            elif reservation_id is not None and settle == "ambiguous":
                await mark_ambiguous(session, business_id, reservation_id, exc.code)
            elif reservation_id is not None:
                await release_reservation(session, business_id, reservation_id, exc.code)
    except asyncio.CancelledError:
        await _on_cancelled(session, business_id, claim, reservation_id)
    except Exception as persist_exc:
        if _cancel_requested():
            await _on_cancelled(session, business_id, claim, reservation_id)
        return await _handle_persist_error(
            session, business_id, claim, persist_exc, reservation_id=reservation_id
        )
    final = await _tasks.get(session, business_id, task_id)
    return ExecutionResult(
        outcome="failed",
        task_id=task_id,
        status=final.status if final else None,
        attempts=final.attempts if final else None,
        error_code=exc.code,
        error_message=str(exc),
    )
