"""Manual reconciliation of ambiguous task-execution reservations.

Two operator actions are exposed:

- confirm that the provider actually charged a specific amount (`charged`);
- confirm that the provider did not charge the attempt (`no_charge`), which
  releases the hold without writing a usage row or inventing a zero cost.

Transaction and lock discipline (binding):

1. non-locking read of the reservation, used ONLY to identify its task;
2. lock the task row (canonical lock order: Task → Reservation → Project);
3. lock the exact ambiguous reservation by full scope (business, reservation,
   project, task, attempt key, exact generation including the NULL branch);
4. for a charge, append one confirmed provider ledger row and CAS
   `ambiguous → reconciled`; for a no-charge, validate the attempt's ledger
   evidence and CAS `ambiguous → released`; either way insert one replay-safe
   audit row, recompute task cost, then project spend;
5. append one system activity event.

The caller owns the transaction. Any failure rolls back every step, so there is
never a ledger row or reservation transition without its audit (or vice versa).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.enums import (
    ActivityKind,
    ReservationStatus,
    UsageCallKind,
    UsageCostSource,
    UsageReconciliationOutcome,
    UsageStatus,
)
from app.persistence.models.usage import BudgetReservation, UsageReconciliation, UsageRecord
from app.persistence.repositories.base import MAX_MONEY, quantize_money
from app.persistence.repositories.concrete import ActivityRepository, TaskRepository
from app.persistence.repositories.spend import (
    recalculate_project_spend,
    recalculate_task_cost,
)
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageReconciliationRepository,
    UsageRepository,
)

_usage = UsageRepository()
_reservations = BudgetReservationRepository()
_reconciliations = UsageReconciliationRepository()
_tasks = TaskRepository()
_activity = ActivityRepository()

# The `no_charge` audit literal is deliberately not part of `UsageCostSource`:
# it is not a ledger cost source and `usage_records.cost_source` must never
# accept it.
_NO_CHARGE_COST_SOURCE = "none"


class ReconciliationNotFound(Exception):
    """Reservation is missing, foreign, taskless, or outside task execution."""


class ReconciliationConflict(Exception):
    """The exact scope no longer qualifies (already reconciled or ambiguous-only)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ReconciliationInvalid(Exception):
    """Operator input is semantically invalid (422)."""


@dataclass(frozen=True)
class ChargeCommand:
    """Validated operator input for one confirmed provider charge."""

    task_id: str
    attempt_key: str
    execution_generation: int | None
    provider: str
    model: str
    amount: Decimal
    currency: str | None = None
    provider_request_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class ConfirmedCharge:
    """Committed result of a confirmed provider charge."""

    reservation: BudgetReservation
    usage_record: UsageRecord
    reconciliation: UsageReconciliation
    task_cost: Decimal
    project_spend: Decimal


@dataclass(frozen=True)
class NoChargeCommand:
    """Validated operator input for one attested no-charge decision."""

    task_id: str
    attempt_key: str
    execution_generation: int | None
    evidence: str
    attestation: bool
    currency: str | None = None


@dataclass(frozen=True)
class ConfirmedNoCharge:
    """Committed result of a confirmed no-charge resolution."""

    reservation: BudgetReservation
    reconciliation: UsageReconciliation
    task_cost: Decimal
    project_spend: Decimal


async def list_ambiguous(
    session: AsyncSession,
    business_id: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BudgetReservation], int]:
    rows = await _reservations.list_ambiguous_task_execution(
        session,
        business_id,
        project_id=project_id,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )
    total = await _reservations.count_ambiguous_task_execution(
        session,
        business_id,
        project_id=project_id,
        task_id=task_id,
    )
    return rows, total


async def get_ambiguous(
    session: AsyncSession, business_id: str, reservation_id: str
) -> tuple[BudgetReservation, list[UsageRecord]] | None:
    reservation = await _reservations.get_ambiguous_task_execution(
        session, business_id, reservation_id
    )
    if reservation is None or reservation.task_id is None:
        return None
    usage_rows = await _usage.list_for_attempt(
        session,
        business_id,
        task_id=reservation.task_id,
        attempt_key=reservation.attempt_key,
    )
    return reservation, usage_rows


def _assert_scope(reservation: BudgetReservation, command: ChargeCommand | NoChargeCommand) -> None:
    if (
        reservation.task_id != command.task_id
        or reservation.attempt_key != command.attempt_key
        or reservation.execution_generation != command.execution_generation
    ):
        raise ReconciliationConflict(
            "reservation_scope_mismatch",
            "Reservation does not match the supplied task, attempt and execution generation.",
        )


def _conflict_for(reservation: BudgetReservation | None) -> Exception:
    if reservation is None:
        return ReconciliationNotFound()
    if reservation.status == ReservationStatus.RECONCILED.value:
        return ReconciliationConflict(
            "already_reconciled",
            "This reservation has already been reconciled; no second charge was written.",
        )
    if (
        reservation.status == ReservationStatus.RELEASED.value
        and reservation.reason == "manual_no_charge"
    ):
        return ReconciliationConflict(
            "already_no_charge",
            "This reservation has already been resolved as no charge.",
        )
    return ReconciliationConflict(
        "reservation_not_ambiguous",
        "Only an ambiguous reservation can be reconciled.",
    )


async def _ledger_key(
    session: AsyncSession,
    business_id: str,
    reservation: BudgetReservation,
) -> str:
    """Choose a deterministic ledger key without duplicating a real charge.

    The canonical per-attempt key is preferred. It may already be occupied by
    evidence that is not a charge:

    - an `unknown` row (the provider answered without a usable price); or
    - the historical zero-cost `estimated`/`tariff` row produced by the
      F-I1 zero-bound path, scoped exactly to this reservation.

    Those rows are preserved unchanged; the confirmed correction then uses the
    linked reconciliation key. Any other occupied key is refused rather than
    risking a second confirmed cost.
    """
    canonical = f"usage:{reservation.idempotency_key}"
    existing = await _usage.get_by_key(session, business_id, canonical)
    if existing is None:
        return canonical
    unknown_evidence = (
        existing.usage_status == UsageStatus.UNKNOWN.value
        and existing.cost_amount is None
        and existing.task_id == reservation.task_id
        and existing.attempt_key == reservation.attempt_key
    )
    zero_cost_estimate = (
        existing.usage_status == UsageStatus.ESTIMATED.value
        and existing.cost_amount is not None
        and Decimal(existing.cost_amount) == 0
        and existing.cost_source == UsageCostSource.TARIFF.value
        and existing.currency == reservation.currency
        and existing.task_id == reservation.task_id
        and existing.project_id == reservation.project_id
        and existing.attempt_key == reservation.attempt_key
        and existing.attempt == reservation.attempt
    )
    if unknown_evidence or zero_cost_estimate:
        alternate = f"usage:reconcile:{reservation.id}:charge"
        if await _usage.get_by_key(session, business_id, alternate) is not None:
            raise ReconciliationConflict(
                "ledger_conflict",
                "A reconciliation ledger entry already exists for this reservation.",
            )
        return alternate
    raise ReconciliationConflict(
        "ledger_conflict",
        "The attempt already has a costed ledger entry; refusing to create a second charge.",
    )


async def confirm_provider_charge(
    session: AsyncSession,
    business_id: str,
    reservation_id: str,
    command: ChargeCommand,
    *,
    actor_user_id: str,
    actor_email: str,
) -> ConfirmedCharge:
    """Atomically confirm one provider charge for an ambiguous hold.

    The caller MUST own the transaction. Locks are acquired in the canonical
    order Task → Reservation; the final project-spend recompute then locks the
    project. A repeated or racing call cannot produce a second ledger entry or
    charge: the reservation is locked, the CAS is authoritative, and both the
    reservation and ledger uniqueness constraints are backstops.
    """
    # Step 1: non-locking lookup ONLY to identify the task. Never lock the
    # reservation before its task.
    preview = await _reservations.get_by_id(session, business_id, reservation_id)
    if preview is None or preview.task_id is None or preview.project_id is None:
        raise ReconciliationNotFound()
    _assert_scope(preview, command)

    # Step 2: canonical order — task first.
    task = await _tasks.lock_for_update(session, business_id, preview.task_id)
    if task is None or task.project_id != preview.project_id:
        raise ReconciliationNotFound()

    # Step 3: lock the exact reservation and revalidate every scoped field under
    # the lock. `populate_existing` above makes this the authoritative state.
    reservation = await _reservations.lock_ambiguous_task_execution(
        session,
        business_id,
        reservation_id,
        task_id=command.task_id,
        project_id=preview.project_id,
        attempt_key=command.attempt_key,
        execution_generation=command.execution_generation,
    )
    if reservation is None:
        current = await _reservations.get_by_id(session, business_id, reservation_id)
        raise _conflict_for(current)
    _assert_scope(reservation, command)

    task_id = reservation.task_id
    project_id = reservation.project_id
    if task_id is None or project_id is None:  # defensive; query excludes these
        raise ReconciliationNotFound()

    # Step 4: validate and normalize operator input.
    try:
        amount = quantize_money(command.amount, max_value=MAX_MONEY)
    except ValueError as exc:
        raise ReconciliationInvalid(str(exc)) from exc
    if amount <= 0:
        raise ReconciliationInvalid("amount must be greater than zero")
    provider = command.provider.strip()
    model = command.model.strip()
    if not provider or not model:
        raise ReconciliationInvalid("provider and model must be non-empty")
    currency = (command.currency or reservation.currency).strip().upper()
    if currency != reservation.currency:
        raise ReconciliationInvalid(
            f"currency must match the reservation currency ({reservation.currency!r})"
        )

    # Attribute the ledger row to the task owner only when this is still the
    # task's current generation. A stale generation's actual agent is unknown,
    # and inventing one would misattribute the charge.
    agent_id: str | None = None
    if (
        reservation.execution_generation is not None
        and task.execution_generation == reservation.execution_generation
    ):
        agent_id = task.owner_agent_id

    # Step 5: append exactly one confirmed provider ledger row.
    key = await _ledger_key(session, business_id, reservation)
    occurred_at = datetime.now(UTC)
    record = await _usage.append(
        session,
        business_id,
        {
            "idempotency_key": key,
            "attempt_key": reservation.attempt_key,
            "attempt": reservation.attempt,
            "project_id": project_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "call_kind": UsageCallKind.TASK_EXECUTION.value,
            "provider": provider,
            "model": model,
            "provider_request_id": command.provider_request_id,
            "prompt_tokens": max(0, int(command.prompt_tokens)),
            "completion_tokens": max(0, int(command.completion_tokens)),
            "total_tokens": max(0, int(command.total_tokens)),
            "cached_tokens": command.cached_tokens,
            "reasoning_tokens": command.reasoning_tokens,
            "cost_amount": amount,
            "currency": currency,
            "cost_source": UsageCostSource.PROVIDER.value,
            "usage_status": UsageStatus.CONFIRMED.value,
            "tariff_version": None,
            "occurred_at": occurred_at,
        },
    )
    if (
        record.cost_amount != amount
        or record.cost_source != UsageCostSource.PROVIDER.value
        or record.usage_status != UsageStatus.CONFIRMED.value
        or record.task_id != task_id
        or record.project_id != project_id
    ):
        raise ReconciliationConflict(
            "ledger_conflict",
            "Existing ledger entry does not match the confirmed provider charge.",
        )

    # Step 6: authoritative CAS. A lost CAS rolls the whole transaction back.
    applied = await _reservations.resolve_ambiguous(
        session,
        business_id,
        reservation_id,
        task_id=task_id,
        project_id=project_id,
        attempt_key=reservation.attempt_key,
        execution_generation=reservation.execution_generation,
        usage_record_id=record.id,
    )
    if not applied:
        current = await _reservations.get_by_id(session, business_id, reservation_id)
        raise _conflict_for(current)

    # Step 7: append-only audit row (unique reservation + ledger row).
    try:
        reconciliation = await _reconciliations.create(
            session,
            business_id,
            {
                "project_id": project_id,
                "task_id": task_id,
                "reservation_id": reservation.id,
                "usage_record_id": record.id,
                "attempt": reservation.attempt,
                "attempt_key": reservation.attempt_key,
                "execution_generation": reservation.execution_generation,
                "outcome": UsageReconciliationOutcome.CHARGED.value,
                "amount": amount,
                "currency": currency,
                "cost_source": UsageCostSource.PROVIDER.value,
                "provider": provider,
                "model": model,
                "provider_request_id": command.provider_request_id,
                "prompt_tokens": max(0, int(command.prompt_tokens)),
                "completion_tokens": max(0, int(command.completion_tokens)),
                "total_tokens": max(0, int(command.total_tokens)),
                "cached_tokens": command.cached_tokens,
                "reasoning_tokens": command.reasoning_tokens,
                "evidence": command.evidence,
                "attested": True,
                "actor_user_id": actor_user_id,
                "actor_email": actor_email,
            },
        )
    except IntegrityError as exc:
        raise ReconciliationConflict(
            "already_reconciled",
            "This reservation has already been reconciled; no second charge was written.",
        ) from exc

    # Step 8: ledger-derived aggregates, in the same transaction. The task row
    # is already locked; project spend locks the project last.
    task_cost = await recalculate_task_cost(session, business_id, task_id)
    project_spend = await recalculate_project_spend(session, business_id, project_id)

    await _activity.append(
        session,
        business_id,
        {
            "project_id": project_id,
            "agent_name": actor_email,
            "kind": ActivityKind.SYSTEM.value,
            "text": (
                f"Ambiguous reservation {reservation.id} reconciled by {actor_email}: "
                f"confirmed provider charge {amount} {currency} "
                f"({provider}/{model}, attempt {reservation.attempt_key}, "
                f"generation {reservation.execution_generation!r})."
            ),
            "cost": amount,
        },
    )

    return ConfirmedCharge(
        reservation=reservation,
        usage_record=record,
        reconciliation=reconciliation,
        task_cost=task_cost,
        project_spend=project_spend,
    )


async def _assert_no_charge_evidence(
    session: AsyncSession,
    business_id: str,
    reservation: BudgetReservation,
) -> None:
    """Refuse no-charge when the attempt already has a real charge.

    Only evidence that proves the attempt was not billed is accepted:

    - an `unknown` row with NULL cost (provider answered without a price); or
    - a historical zero-cost `estimated`/`tariff` row in the exact reservation
      scope (the F-I1 zero-bound path).

    Confirmed rows, positive/NULL-cost estimated rows, unknown rows carrying a
    cost, and rows outside the exact task/project/attempt scope are conflicts.
    """
    if reservation.task_id is None:
        raise ReconciliationNotFound()
    rows = await _usage.list_for_attempt(
        session,
        business_id,
        task_id=reservation.task_id,
        attempt_key=reservation.attempt_key,
    )
    for row in rows:
        if (
            row.project_id != reservation.project_id
            or row.task_id != reservation.task_id
            or row.attempt_key != reservation.attempt_key
            or row.attempt != reservation.attempt
        ):
            raise ReconciliationConflict(
                "ledger_evidence_conflict",
                "Attempt ledger evidence is outside the reservation scope; refusing no-charge.",
            )
        if row.usage_status == UsageStatus.UNKNOWN.value and row.cost_amount is None:
            continue
        if (
            row.usage_status == UsageStatus.ESTIMATED.value
            and row.cost_amount is not None
            and Decimal(row.cost_amount) == 0
            and row.cost_source == UsageCostSource.TARIFF.value
            and row.currency == reservation.currency
        ):
            continue
        raise ReconciliationConflict(
            "ledger_evidence_conflict",
            "The attempt has costed ledger evidence; confirm the actual charge instead.",
        )


async def confirm_no_charge(
    session: AsyncSession,
    business_id: str,
    reservation_id: str,
    command: NoChargeCommand,
    *,
    actor_user_id: str,
    actor_email: str,
) -> ConfirmedNoCharge:
    """Atomically resolve one ambiguous hold as an attested no-charge.

    The caller MUST own the transaction. The hold is only released; no provider
    call is made, no `UsageRecord` is inserted, and no artificial zero-cost
    confirmed row is created. The reservation CAS removes its amount from the
    pending budget exposure, while historical UNKNOWN/estimated ledger rows
    remain append-only evidence. Locks follow Task → Reservation → Project.
    """
    if command.attestation is not True:
        raise ReconciliationInvalid("attestation must be true")
    evidence = command.evidence.strip()
    if not evidence:
        raise ReconciliationInvalid("evidence must be non-empty")
    if len(evidence) > 2000:
        raise ReconciliationInvalid("evidence must be at most 2000 characters")

    # Step 1: non-locking lookup ONLY to identify the task.
    preview = await _reservations.get_by_id(session, business_id, reservation_id)
    if preview is None or preview.task_id is None or preview.project_id is None:
        raise ReconciliationNotFound()
    _assert_scope(preview, command)

    # Step 2: canonical order — task first.
    task = await _tasks.lock_for_update(session, business_id, preview.task_id)
    if task is None or task.project_id != preview.project_id:
        raise ReconciliationNotFound()

    # Step 3: lock the exact reservation and revalidate every scoped field.
    reservation = await _reservations.lock_ambiguous_task_execution(
        session,
        business_id,
        reservation_id,
        task_id=command.task_id,
        project_id=preview.project_id,
        attempt_key=command.attempt_key,
        execution_generation=command.execution_generation,
    )
    if reservation is None:
        current = await _reservations.get_by_id(session, business_id, reservation_id)
        raise _conflict_for(current)
    _assert_scope(reservation, command)

    task_id = reservation.task_id
    project_id = reservation.project_id
    if task_id is None or project_id is None:  # defensive; query excludes these
        raise ReconciliationNotFound()

    currency = (command.currency or reservation.currency).strip().upper()
    if currency != reservation.currency:
        raise ReconciliationInvalid(
            f"currency must match the reservation currency ({reservation.currency!r})"
        )

    # Step 4: the attempt must not already contain a real charge.
    await _assert_no_charge_evidence(session, business_id, reservation)

    # Step 5: authoritative CAS. A lost CAS rolls the whole transaction back.
    applied = await _reservations.resolve_ambiguous_no_charge(
        session,
        business_id,
        reservation_id,
        task_id=task_id,
        project_id=project_id,
        attempt_key=reservation.attempt_key,
        execution_generation=reservation.execution_generation,
    )
    if not applied:
        current = await _reservations.get_by_id(session, business_id, reservation_id)
        raise _conflict_for(current)

    # Step 6: append-only audit row with no ledger link and no invented cost.
    try:
        reconciliation = await _reconciliations.create(
            session,
            business_id,
            {
                "project_id": project_id,
                "task_id": task_id,
                "reservation_id": reservation.id,
                "usage_record_id": None,
                "attempt": reservation.attempt,
                "attempt_key": reservation.attempt_key,
                "execution_generation": reservation.execution_generation,
                "outcome": UsageReconciliationOutcome.NO_CHARGE.value,
                "amount": Decimal("0.00"),
                "currency": reservation.currency,
                "cost_source": _NO_CHARGE_COST_SOURCE,
                "provider": "",
                "model": "",
                "provider_request_id": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "evidence": evidence,
                "attested": True,
                "actor_user_id": actor_user_id,
                "actor_email": actor_email,
            },
        )
    except IntegrityError as exc:
        raise ReconciliationConflict(
            "already_no_charge",
            "This reservation has already been resolved as no charge.",
        ) from exc

    # Step 7: ledger-derived aggregates. The task row is already locked; the
    # project-spend recompute locks the project last. A released hold is no
    # longer included in pending, and no ledger row was added, so headroom is
    # restored by exactly the hold amount.
    task_cost = await recalculate_task_cost(session, business_id, task_id)
    project_spend = await recalculate_project_spend(session, business_id, project_id)

    await _activity.append(
        session,
        business_id,
        {
            "project_id": project_id,
            "agent_name": actor_email,
            "kind": ActivityKind.SYSTEM.value,
            "text": (
                f"Ambiguous reservation {reservation.id} resolved as no charge by "
                f"{actor_email}: {evidence} (attempt {reservation.attempt_key}, "
                f"generation {reservation.execution_generation!r})."
            ),
            "cost": None,
        },
    )

    return ConfirmedNoCharge(
        reservation=reservation,
        reconciliation=reconciliation,
        task_cost=task_cost,
        project_spend=project_spend,
    )
