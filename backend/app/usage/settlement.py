"""Reservation settlement + ledger write (Stage 16 / 16.1).

Called from the executor and from the Team Lead metering layer inside their
persist/failure transactions. Each helper uses a compare-and-set on the
reservation, so double-settlement (duplicate persist, recovery) is a no-op
rather than a second charge. The caller owns the transaction boundary;
`record_success` additionally recomputes the ledger-derived task cost, and the
caller recomputes the project spend when the row is project-scoped.

Reservations are addressed by plain `reservation_id` (never a live ORM
instance): settlement often runs AFTER the originating transaction rolled back,
which expires ORM state and would trigger a lazy load outside the greenlet.

Stage 16.1 additions:
- `reservation_id`/`task_id` are optional: a caller without a reservation still
  appends a bare ledger row. Team Lead business-scoped calls now always create
  a reservation (zero-amount when the business has no configured budget), so
  their ledger key is reservation-derived.
- `record_estimated_failure` implements the post-send malformed-response policy:
  estimated from a defensible tariff bound when possible, otherwise the hold is
  kept `ambiguous`; it is NEVER released and NEVER recorded as zero.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.types import LlmResponse, LlmUsage
from app.persistence.enums import (
    ReservationStatus,
    UsageCostSource,
    UsageStatus,
)
from app.persistence.models.usage import UsageRecord
from app.persistence.repositories.spend import recalculate_task_cost
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from app.usage import pricing

log = logging.getLogger(__name__)

_usage = UsageRepository()
_reservations = BudgetReservationRepository()

_SUCCESS_REPLAY_STATUSES = frozenset(
    {
        UsageStatus.CONFIRMED.value,
        UsageStatus.ESTIMATED.value,
        UsageStatus.UNKNOWN.value,
    }
)
_FAILURE_REPLAY_STATUSES = frozenset({UsageStatus.ESTIMATED.value})


@dataclass(frozen=True)
class SettlementResult:
    """Outcome of one strict settlement call.

    `reservation_settled` is True only when THIS call applied the reservation
    transition (the strict equivalent of the legacy `won` flag). A validated
    replay and a skip both leave it False.
    """

    outcome: str
    record: UsageRecord | None
    reservation_id: str | None
    reservation_status: str
    reservation_settled: bool
    usage_status: str | None
    reason: str


class SettlementRejected(Exception):
    """A settlement could not be applied.

    `wrote` is True when a ledger row was already appended (or the reservation
    CAS may have partially applied) before the rejection. The strict API never
    swallows this case: the caller's transaction MUST roll back. A `wrote=False`
    rejection is pre-write and leaves the caller's transaction untouched.
    """

    def __init__(self, code: str, message: str, *, wrote: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.wrote = wrote


def _skipped(reservation_id: str | None, status: str, reason: str) -> SettlementResult:
    return SettlementResult(
        outcome="skipped",
        record=None,
        reservation_id=reservation_id,
        reservation_status=status,
        reservation_settled=False,
        usage_status=None,
        reason=reason,
    )


def _has_usable_reported_usage(usage: LlmUsage) -> bool:
    """Return whether `reported=True` carries trustworthy token evidence.

    The adapter validates malformed usage, but this second check protects the
    settlement boundary from other providers or future adapter regressions.
    A genuine all-zero report stays valid: zero tokens are a legitimate report,
    unlike an empty/partial object that the adapter has already marked
    unreported. Provider cost, when present, remains authoritative.
    """
    if not usage.reported:
        return False
    if usage.cost_estimate is not None:
        return True
    if usage.prompt_tokens < 0 or usage.completion_tokens < 0 or usage.total_tokens < 0:
        return False
    return usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def price_response(provider: str, model: str, response: LlmResponse) -> pricing.CostQuote:
    usage = response.usage
    if usage.reported and not _has_usable_reported_usage(usage):
        # Do not turn a contradictory reported usage object into confirmed
        # spend. settlement re-prices this as a conservative estimate when a
        # bound is available, or keeps the hold ambiguous otherwise.
        return pricing.CostQuote("unknown", None, "unknown", None, "USD")
    return pricing.price_usage(
        provider=provider,
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        usage_reported=usage.reported,
        provider_cost=usage.cost_estimate,
    )


def _conservative_success_usage(
    provider: str,
    model: str,
    response: LlmResponse,
    *,
    input_bound_tokens: int | None,
    max_output_tokens: int | None,
) -> tuple[LlmUsage, pricing.CostQuote] | None:
    """Price a successful call whose usage evidence is missing/unusable.

    The adapter's zero defaults are NOT evidence that the call was free. When
    the caller supplied the same token bound used for the reservation, record
    that bound as `estimated` and settle the hold. Without a usable tariff and
    positive output cap, return None so the caller keeps the hold ambiguous.
    """
    if _has_usable_reported_usage(response.usage):
        return None
    if input_bound_tokens is None or max_output_tokens is None:
        return None
    prompt_tokens = max(
        0,
        int(input_bound_tokens),
        int(response.usage.prompt_tokens or 0),
    )
    completion_tokens = max(
        0,
        int(max_output_tokens),
        int(response.usage.completion_tokens or 0),
    )
    quote = pricing.upper_bound_cost(
        provider=provider,
        model=model,
        input_token_bound=prompt_tokens,
        max_output_tokens=completion_tokens,
    )
    if quote is None:
        return None
    usage = LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        reported=False,
    )
    return usage, quote


async def _append_row(
    session: AsyncSession,
    business_id: str,
    *,
    key: str,
    attempt_key: str,
    attempt: int,
    project_id: str | None,
    task_id: str | None,
    agent_id: str | None,
    call_kind: str | None,
    provider: str,
    model: str,
    provider_request_id: str | None,
    usage: LlmUsage,
    quote: pricing.CostQuote,
    occurred_at: datetime | None,
) -> Any:
    return await _usage.append(
        session,
        business_id,
        {
            "idempotency_key": f"usage:{key}",
            "attempt_key": attempt_key,
            "attempt": attempt,
            "project_id": project_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "call_kind": call_kind,
            "provider": provider,
            "model": model,
            "provider_request_id": provider_request_id,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": usage.cached_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_amount": quote.cost,
            "currency": quote.currency,
            "cost_source": quote.cost_source,
            "usage_status": quote.status,
            "tariff_version": quote.tariff_version,
            "occurred_at": occurred_at or datetime.now(UTC),
        },
    )


def _validate_scope(
    reservation: Any,
    *,
    project_id: str | None,
    task_id: str | None,
    attempt: int,
    attempt_key: str,
    execution_generation: int | None,
    reservation_key: str | None,
) -> None:
    """Reject a settlement whose caller identity does not match the hold.

    Every check is read-only and happens BEFORE any ledger append or CAS, so a
    rejection here leaves the caller's transaction untouched (`wrote=False`).
    The NULL branches are exact: a NULL generation/attempt key is its own scope,
    never a wildcard.
    """

    def conflict(field: str, expected: Any, actual: Any) -> None:
        raise SettlementRejected(
            "scope_mismatch",
            f"reservation {field} {actual!r} does not match settlement {field} {expected!r}",
        )

    if reservation.project_id != project_id:
        conflict("project_id", project_id, reservation.project_id)
    if reservation.task_id != task_id:
        conflict("task_id", task_id, reservation.task_id)
    if reservation.attempt_key != attempt_key:
        conflict("attempt_key", attempt_key, reservation.attempt_key)
    # Executor generations and Reservation.attempt are written from the same
    # claim. Legacy Team Lead callers pass the generation index as `attempt`
    # while the reservation keeps its default 0, so the attempt equality is
    # enforced only for generation-scoped callers.
    if execution_generation is not None and reservation.attempt != attempt:
        conflict("attempt", attempt, reservation.attempt)
    if reservation.execution_generation != execution_generation:
        conflict(
            "execution_generation",
            execution_generation,
            reservation.execution_generation,
        )
    if reservation_key is not None and reservation.idempotency_key != reservation_key:
        conflict(
            "idempotency_key",
            reservation_key,
            reservation.idempotency_key,
        )


async def _replay_record(
    session: AsyncSession,
    business_id: str,
    reservation: Any,
    *,
    project_id: str | None,
    task_id: str | None,
    attempt: int,
    attempt_key: str,
    provider: str,
    model: str,
    allowed_statuses: frozenset[str],
) -> UsageRecord:
    """Validate an existing settlement without re-pricing it.

    Pure structural validation: identity, ledger linkage, provider/model,
    currency and the persisted cost/status invariant. The tariff registry is
    never consulted, so a historical replay stays valid after tariff changes.
    No append and no reservation CAS happen here.
    """
    canonical = f"usage:{reservation.idempotency_key}"
    if reservation.usage_record_id is None:
        raise SettlementRejected(
            "replay_linkage_missing",
            "consumed reservation has no usage_record_id linkage",
        )
    record = await _usage.get_by_key(session, business_id, canonical)
    if record is None:
        raise SettlementRejected(
            "replay_ledger_missing",
            f"no ledger row found for consumed reservation key {canonical!r}",
        )
    if record.id != reservation.usage_record_id:
        raise SettlementRejected(
            "replay_linkage_mismatch",
            (
                f"reservation links usage row {reservation.usage_record_id!r} "
                f"but canonical key {canonical!r} resolves to {record.id!r}"
            ),
        )

    def conflict(field: str, expected: Any, actual: Any) -> None:
        raise SettlementRejected(
            "replay_identity_conflict",
            f"replayed ledger {field} {actual!r} does not match {expected!r}",
        )

    if record.business_id != business_id:
        conflict("business_id", business_id, record.business_id)
    if record.project_id != project_id:
        conflict("project_id", project_id, record.project_id)
    if record.task_id != task_id:
        conflict("task_id", task_id, record.task_id)
    if record.attempt_key != attempt_key:
        conflict("attempt_key", attempt_key, record.attempt_key)
    if record.attempt != attempt:
        conflict("attempt", attempt, record.attempt)
    if record.idempotency_key != canonical:
        conflict("idempotency_key", canonical, record.idempotency_key)
    if record.provider != provider:
        conflict("provider", provider, record.provider)
    if record.model != model:
        conflict("model", model, record.model)
    if record.currency != reservation.currency:
        conflict("currency", reservation.currency, record.currency)

    status = record.usage_status
    if status not in allowed_statuses:
        conflict("usage_status", sorted(allowed_statuses), status)
    if status == UsageStatus.UNKNOWN.value:
        if record.cost_amount is not None:
            conflict("cost_amount", None, record.cost_amount)
        if record.cost_source != UsageCostSource.UNKNOWN.value:
            conflict("cost_source", UsageCostSource.UNKNOWN.value, record.cost_source)
    elif status == UsageStatus.CONFIRMED.value:
        if record.cost_amount is None:
            conflict("cost_amount", "not NULL", None)
        if record.cost_source not in (
            UsageCostSource.PROVIDER.value,
            UsageCostSource.TARIFF.value,
        ):
            conflict("cost_source", "provider|tariff", record.cost_source)
    else:  # estimated
        if record.cost_amount is None:
            conflict("cost_amount", "not NULL", None)
        if record.cost_source != UsageCostSource.TARIFF.value:
            conflict("cost_source", UsageCostSource.TARIFF.value, record.cost_source)
    return record


def _price_success(
    provider: str,
    model: str,
    response: LlmResponse,
    *,
    input_bound_tokens: int | None,
    max_output_tokens: int | None,
    has_reservation: bool,
) -> tuple[LlmUsage, pricing.CostQuote, bool, str]:
    """Price a successful response and decide whether the hold must stay held."""
    quote = price_response(provider, model, response)
    usage = response.usage
    if usage.cost_estimate is None and not _has_usable_reported_usage(usage):
        conservative = _conservative_success_usage(
            provider,
            model,
            response,
            input_bound_tokens=input_bound_tokens,
            max_output_tokens=max_output_tokens,
        )
        if conservative is None:
            return (
                usage,
                pricing.CostQuote("unknown", None, "unknown", None, "USD"),
                has_reservation,
                "post_send_unpriced",
            )
        usage, quote = conservative
        # A zero estimate must not consume a nonzero hold: keep the hold
        # counted as unresolved exposure instead.
        if has_reservation and quote.cost is not None and quote.cost == 0:
            return usage, quote, True, "zero_cost_bound"
        return usage, quote, False, "conservative_estimate"
    if quote.status == UsageStatus.CONFIRMED.value:
        return usage, quote, False, "confirmed"
    if quote.status == UsageStatus.ESTIMATED.value:
        return usage, quote, False, "estimated"
    return usage, quote, False, "unpriced_reported_usage"


def _consumed_outcome(quote: pricing.CostQuote) -> str:
    if quote.status == UsageStatus.CONFIRMED.value:
        return "applied_confirmed"
    if quote.status == UsageStatus.ESTIMATED.value:
        return "applied_estimated"
    return "applied_unknown"


async def settle_success(
    session: AsyncSession,
    business_id: str,
    *,
    reservation_id: str | None = None,
    reservation_key: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    call_kind: str | None = None,
    attempt: int = 0,
    attempt_key: str,
    execution_generation: int | None = None,
    provider: str,
    model: str,
    response: LlmResponse,
    input_bound_tokens: int | None = None,
    max_output_tokens: int | None = None,
    occurred_at: datetime | None = None,
) -> SettlementResult:
    """Append the canonical ledger row and settle the reservation strictly.

    The caller owns the transaction. Every rejection before the ledger append
    (`scope`, key occupancy, currency) raises `SettlementRejected(wrote=False)`
    and performs no write; a lost CAS after the append raises
    `SettlementRejected(wrote=True)` so the caller must roll back.

    A `consumed` reservation is validated as an exact replay (no append, no CAS,
    no pricing/tariff lookup). Missing or other terminal reservations are
    `skipped`. An active hold is settled as `consumed` or kept `ambiguous`
    exactly as the legacy contract did.
    """
    if reservation_id is None:
        return _skipped(None, "missing", "reservation_missing")
    reservation = await _reservations.get_for_settlement(session, business_id, reservation_id)
    if reservation is None:
        return _skipped(reservation_id, "missing", "reservation_missing")

    _validate_scope(
        reservation,
        project_id=project_id,
        task_id=task_id,
        attempt=attempt,
        attempt_key=attempt_key,
        execution_generation=execution_generation,
        reservation_key=reservation_key,
    )

    if reservation.status == ReservationStatus.CONSUMED.value:
        record = await _replay_record(
            session,
            business_id,
            reservation,
            project_id=project_id,
            task_id=task_id,
            attempt=attempt,
            attempt_key=attempt_key,
            provider=provider,
            model=model,
            allowed_statuses=_SUCCESS_REPLAY_STATUSES,
        )
        return SettlementResult(
            outcome="replayed",
            record=record,
            reservation_id=reservation.id,
            reservation_status=reservation.status,
            reservation_settled=False,
            usage_status=record.usage_status,
            reason="exact_replay",
        )

    if reservation.status != ReservationStatus.ACTIVE.value:
        return _skipped(reservation.id, reservation.status, "reservation_terminal")

    usage, quote, keep_hold, reason = _price_success(
        provider,
        model,
        response,
        input_bound_tokens=input_bound_tokens,
        max_output_tokens=max_output_tokens,
        has_reservation=True,
    )
    if quote.currency != reservation.currency:
        raise SettlementRejected(
            "currency_mismatch",
            f"priced currency {quote.currency!r} does not match "
            f"reservation {reservation.currency!r}",
        )
    canonical = f"usage:{reservation.idempotency_key}"
    if await _usage.get_by_key(session, business_id, canonical) is not None:
        raise SettlementRejected(
            "key_conflict",
            f"ledger key {canonical!r} is already occupied while its reservation is active",
        )

    record = await _append_row(
        session,
        business_id,
        key=reservation.idempotency_key,
        attempt_key=attempt_key,
        attempt=attempt,
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        call_kind=call_kind,
        provider=provider,
        model=model,
        provider_request_id=response.provider_request_id,
        usage=usage,
        quote=quote,
        occurred_at=occurred_at,
    )
    if keep_hold:
        # Stored reason is the historical `post_send_unpriced`; the result
        # reason distinguishes a zero-cost conservative bound for callers.
        won = await mark_ambiguous(
            session,
            business_id,
            reservation.id,
            "post_send_unpriced",
            task_id=task_id,
            attempt_key=attempt_key,
            execution_generation=execution_generation,
        )
        if not won:
            raise SettlementRejected(
                "cas_lost",
                "reservation changed after the ledger append; transaction must roll back",
                wrote=True,
            )
        status = ReservationStatus.AMBIGUOUS.value
        outcome = "applied_ambiguous"
    else:
        won = await _reservations.set_status(
            session,
            business_id,
            reservation.id,
            from_statuses=(ReservationStatus.ACTIVE.value,),
            to_status=ReservationStatus.CONSUMED.value,
            usage_record_id=record.id,
            task_id=task_id,
            attempt_key=attempt_key,
            execution_generation=execution_generation,
        )
        if not won:
            raise SettlementRejected(
                "cas_lost",
                "reservation changed after the ledger append; transaction must roll back",
                wrote=True,
            )
        status = ReservationStatus.CONSUMED.value
        outcome = _consumed_outcome(quote)

    # Ledger-derived task cost; idempotent (SUM, never increment).
    if task_id is not None:
        await recalculate_task_cost(session, business_id, task_id)
    return SettlementResult(
        outcome=outcome,
        record=record,
        reservation_id=reservation.id,
        reservation_status=status,
        reservation_settled=True,
        usage_status=quote.status,
        reason=reason,
    )


def _log_legacy_rejection(name: str, exc: SettlementRejected, reservation_id: str) -> None:
    log.warning(
        "legacy settlement rejected before any write",
        extra={
            "context": {
                "caller": name,
                "reservation_id": reservation_id,
                "code": exc.code,
                "message": exc.message,
            }
        },
    )


async def record_success(
    session: AsyncSession,
    business_id: str,
    *,
    reservation_id: str | None = None,
    reservation_key: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    call_kind: str | None = None,
    attempt: int = 0,
    attempt_key: str,
    execution_generation: int | None = None,
    provider: str,
    model: str,
    response: LlmResponse,
    input_bound_tokens: int | None = None,
    max_output_tokens: int | None = None,
    occurred_at: datetime | None = None,
) -> tuple[Any, bool]:
    """Legacy tuple adapter over `settle_success`.

    Pre-write rejections keep the historical non-throwing `(None, False)`
    contract. A rejection AFTER a write is never swallowed: it propagates so the
    caller's transaction rolls back instead of committing a partial ledger row.
    A caller without a reservation keeps the historical bare-row behavior.
    """
    if reservation_id is None:
        usage, quote, _keep_hold, _reason = _price_success(
            provider,
            model,
            response,
            input_bound_tokens=input_bound_tokens,
            max_output_tokens=max_output_tokens,
            has_reservation=False,
        )
        record = await _append_row(
            session,
            business_id,
            key=reservation_key or attempt_key,
            attempt_key=attempt_key,
            attempt=attempt,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            call_kind=call_kind,
            provider=provider,
            model=model,
            provider_request_id=response.provider_request_id,
            usage=usage,
            quote=quote,
            occurred_at=occurred_at,
        )
        if task_id is not None:
            await recalculate_task_cost(session, business_id, task_id)
        return record, False

    try:
        result = await settle_success(
            session,
            business_id,
            reservation_id=reservation_id,
            reservation_key=reservation_key,
            task_id=task_id,
            project_id=project_id,
            agent_id=agent_id,
            call_kind=call_kind,
            attempt=attempt,
            attempt_key=attempt_key,
            execution_generation=execution_generation,
            provider=provider,
            model=model,
            response=response,
            input_bound_tokens=input_bound_tokens,
            max_output_tokens=max_output_tokens,
            occurred_at=occurred_at,
        )
    except SettlementRejected as exc:
        if exc.wrote:
            raise
        _log_legacy_rejection("record_success", exc, reservation_id)
        return None, False
    return result.record, result.reservation_settled


async def settle_estimated_failure(
    session: AsyncSession,
    business_id: str,
    *,
    reservation_id: str | None = None,
    reservation_key: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    call_kind: str | None = None,
    attempt: int = 0,
    attempt_key: str,
    execution_generation: int | None = None,
    provider: str,
    model: str,
    input_bound_tokens: int,
    max_output_tokens: int | None,
    occurred_at: datetime | None = None,
) -> SettlementResult:
    """Strictly record a POST-SEND failure whose body was unusable.

    The provider answered, so the call may have been billed and is never
    released as if free. A priceable bound appends an `estimated` row and
    consumes the hold (`applied_estimated`). An unpriceable bound keeps the hold
    `ambiguous` with no ledger row (`applied_ambiguous`). A `consumed` hold is
    validated as an exact replay without re-pricing. Missing/other terminal
    holds are `skipped`.

    The caller owns the transaction; a post-append CAS loss raises
    `SettlementRejected(wrote=True)` and must roll the caller back.
    """
    if reservation_id is None:
        return _skipped(None, "missing", "reservation_missing")
    reservation = await _reservations.get_for_settlement(session, business_id, reservation_id)
    if reservation is None:
        return _skipped(reservation_id, "missing", "reservation_missing")

    _validate_scope(
        reservation,
        project_id=project_id,
        task_id=task_id,
        attempt=attempt,
        attempt_key=attempt_key,
        execution_generation=execution_generation,
        reservation_key=reservation_key,
    )

    if reservation.status == ReservationStatus.CONSUMED.value:
        record = await _replay_record(
            session,
            business_id,
            reservation,
            project_id=project_id,
            task_id=task_id,
            attempt=attempt,
            attempt_key=attempt_key,
            provider=provider,
            model=model,
            allowed_statuses=_FAILURE_REPLAY_STATUSES,
        )
        return SettlementResult(
            outcome="replayed",
            record=record,
            reservation_id=reservation.id,
            reservation_status=reservation.status,
            reservation_settled=False,
            usage_status=record.usage_status,
            reason="exact_replay",
        )

    if reservation.status != ReservationStatus.ACTIVE.value:
        return _skipped(reservation.id, reservation.status, "reservation_terminal")

    prompt_tokens = max(0, int(input_bound_tokens))
    completion_tokens = max(0, int(max_output_tokens or 0))
    usage = LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        reported=False,
    )
    # Only a tariff PLUS a positive output cap yields a defensible post-send
    # estimate. Estimating input alone would silently drop the output exposure.
    quote = pricing.upper_bound_cost(
        provider=provider,
        model=model,
        input_token_bound=prompt_tokens,
        max_output_tokens=completion_tokens,
    )
    if quote is None:
        quote = pricing.CostQuote("unknown", None, "unknown", None, "USD")

    if quote.status == UsageStatus.UNKNOWN.value:
        # Cannot price it and a hold exists: fail closed, keep exposure counted.
        won = await mark_ambiguous(
            session,
            business_id,
            reservation.id,
            "post_send_unpriced",
            task_id=task_id,
            attempt_key=attempt_key,
            execution_generation=execution_generation,
        )
        if not won:
            raise SettlementRejected(
                "cas_lost",
                "reservation changed while marking an unpriceable failure ambiguous",
            )
        return SettlementResult(
            outcome="applied_ambiguous",
            record=None,
            reservation_id=reservation.id,
            reservation_status=ReservationStatus.AMBIGUOUS.value,
            reservation_settled=True,
            usage_status=None,
            reason="post_send_unpriced",
        )

    if quote.currency != reservation.currency:
        raise SettlementRejected(
            "currency_mismatch",
            f"priced currency {quote.currency!r} does not match "
            f"reservation {reservation.currency!r}",
        )
    canonical = f"usage:{reservation.idempotency_key}"
    if await _usage.get_by_key(session, business_id, canonical) is not None:
        raise SettlementRejected(
            "key_conflict",
            f"ledger key {canonical!r} is already occupied while its reservation is active",
        )

    record = await _append_row(
        session,
        business_id,
        key=reservation.idempotency_key,
        attempt_key=attempt_key,
        attempt=attempt,
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        call_kind=call_kind,
        provider=provider,
        model=model,
        provider_request_id=None,
        usage=usage,
        quote=quote,
        occurred_at=occurred_at,
    )
    won = await _reservations.set_status(
        session,
        business_id,
        reservation.id,
        from_statuses=(ReservationStatus.ACTIVE.value,),
        to_status=ReservationStatus.CONSUMED.value,
        usage_record_id=record.id,
        task_id=task_id,
        attempt_key=attempt_key,
        execution_generation=execution_generation,
    )
    if not won:
        raise SettlementRejected(
            "cas_lost",
            "reservation changed after the ledger append; transaction must roll back",
            wrote=True,
        )
    if task_id is not None:
        await recalculate_task_cost(session, business_id, task_id)
    return SettlementResult(
        outcome="applied_estimated",
        record=record,
        reservation_id=reservation.id,
        reservation_status=ReservationStatus.CONSUMED.value,
        reservation_settled=True,
        usage_status=quote.status,
        reason="estimated",
    )


async def record_estimated_failure(
    session: AsyncSession,
    business_id: str,
    *,
    reservation_id: str | None = None,
    reservation_key: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    call_kind: str | None = None,
    attempt: int = 0,
    attempt_key: str,
    execution_generation: int | None = None,
    provider: str,
    model: str,
    input_bound_tokens: int,
    max_output_tokens: int | None,
    occurred_at: datetime | None = None,
) -> tuple[Any | None, bool, str]:
    """Legacy tuple adapter over `settle_estimated_failure`.

    Pre-write rejections keep the historical non-throwing
    `(None, False, "skipped")` contract. A rejection AFTER a write propagates so
    the caller's transaction rolls back instead of committing a partial ledger
    row. A reservation-less caller keeps the historical bare-row behavior.
    """
    if reservation_id is None:
        prompt_tokens = max(0, int(input_bound_tokens))
        completion_tokens = max(0, int(max_output_tokens or 0))
        usage = LlmUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            reported=False,
        )
        quote = pricing.upper_bound_cost(
            provider=provider,
            model=model,
            input_token_bound=prompt_tokens,
            max_output_tokens=completion_tokens,
        )
        if quote is None:
            quote = pricing.CostQuote("unknown", None, "unknown", None, "USD")
        record = await _append_row(
            session,
            business_id,
            key=reservation_key or attempt_key,
            attempt_key=attempt_key,
            attempt=attempt,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            call_kind=call_kind,
            provider=provider,
            model=model,
            provider_request_id=None,
            usage=usage,
            quote=quote,
            occurred_at=occurred_at,
        )
        if task_id is not None:
            await recalculate_task_cost(session, business_id, task_id)
        return record, False, quote.status

    try:
        result = await settle_estimated_failure(
            session,
            business_id,
            reservation_id=reservation_id,
            reservation_key=reservation_key,
            task_id=task_id,
            project_id=project_id,
            agent_id=agent_id,
            call_kind=call_kind,
            attempt=attempt,
            attempt_key=attempt_key,
            execution_generation=execution_generation,
            provider=provider,
            model=model,
            input_bound_tokens=input_bound_tokens,
            max_output_tokens=max_output_tokens,
            occurred_at=occurred_at,
        )
    except SettlementRejected as exc:
        if exc.wrote:
            raise
        _log_legacy_rejection("record_estimated_failure", exc, reservation_id)
        return None, False, "skipped"

    if result.outcome == "skipped":
        return None, False, "skipped"
    if result.outcome == "replayed":
        status = result.record.usage_status if result.record is not None else "skipped"
        return result.record, False, status
    if result.outcome == "applied_ambiguous":
        return None, result.reservation_settled, "ambiguous"
    return result.record, result.reservation_settled, result.usage_status or "skipped"


async def release_reservation(
    session: AsyncSession,
    business_id: str,
    reservation_id: str | None,
    reason: str,
    *,
    task_id: str | None = None,
    attempt_key: str | None = None,
    execution_generation: int | None = None,
) -> bool:
    """Proven non-execution (pre-send failure / definitive provider error).

    `execution_generation` adds the ownership fence: only the exact attempt's
    reservation can be released, and a NULL generation never matches a supplied
    integer. Omitting it keeps legacy/Team Lead behavior.
    """
    if reservation_id is None:
        return False
    return await _reservations.set_status(
        session,
        business_id,
        reservation_id,
        from_statuses=("active",),
        to_status="released",
        reason=reason,
        task_id=task_id,
        attempt_key=attempt_key,
        execution_generation=execution_generation,
    )


async def mark_ambiguous(
    session: AsyncSession,
    business_id: str,
    reservation_id: str | None,
    reason: str,
    *,
    task_id: str | None = None,
    attempt_key: str | None = None,
    execution_generation: int | None = None,
) -> bool:
    """Outcome unknown — the provider may have billed. Held, never released.

    `execution_generation` scopes the transition to the exact attempt's
    reservation (NULL never matches an integer); omitting it keeps the legacy
    and Team Lead behavior.
    """
    if reservation_id is None:
        return False
    return await _reservations.set_status(
        session,
        business_id,
        reservation_id,
        from_statuses=("active",),
        to_status="ambiguous",
        reason=reason,
        task_id=task_id,
        attempt_key=attempt_key,
        execution_generation=execution_generation,
    )
