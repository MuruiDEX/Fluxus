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
- `reservation_id`/`task_id` are optional: business-scoped Team Lead calls have
  no project to reserve against and append a bare ledger row.
- `record_estimated_failure` implements the post-send malformed-response policy:
  estimated from a defensible tariff bound when possible, otherwise the hold is
  kept `ambiguous`; it is NEVER released and NEVER recorded as zero.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.types import LlmResponse, LlmUsage
from app.persistence.repositories.spend import recalculate_task_cost
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from app.usage import pricing

_usage = UsageRepository()
_reservations = BudgetReservationRepository()


def price_response(provider: str, model: str, response: LlmResponse) -> pricing.CostQuote:
    usage = response.usage
    return pricing.price_usage(
        provider=provider,
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        usage_reported=usage.reported,
        provider_cost=usage.cost_estimate,
    )


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
    provider: str,
    model: str,
    response: LlmResponse,
    occurred_at: datetime | None = None,
) -> tuple[Any, bool]:
    """Append the canonical ledger row and settle the reservation to consumed.

    `reservation_id` is optional (Stage 16.1): business-scoped Team Lead calls
    with no configured business budget append a bare ledger row. `task_id` is
    optional; task-less rows skip the task-cost recompute (the caller
    recomputes project spend when the row is project-scoped).
    """
    quote = price_response(provider, model, response)
    key = reservation_key or attempt_key
    record = await _append_row(
        session,
        business_id,
        key=key,
        attempt_key=attempt_key,
        attempt=attempt,
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        call_kind=call_kind,
        provider=provider,
        model=model,
        provider_request_id=response.provider_request_id,
        usage=response.usage,
        quote=quote,
        occurred_at=occurred_at,
    )
    won = False
    if reservation_id is not None:
        won = await _reservations.set_status(
            session,
            business_id,
            reservation_id,
            from_statuses=("active",),
            to_status="consumed",
            usage_record_id=record.id,
        )
    # Ledger-derived task cost; idempotent (SUM, never increment).
    if task_id is not None:
        await recalculate_task_cost(session, business_id, task_id)
    return record, won


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
    provider: str,
    model: str,
    input_bound_tokens: int,
    max_output_tokens: int | None,
    occurred_at: datetime | None = None,
) -> tuple[Any | None, bool, str]:
    """Record a POST-SEND failure whose body was unusable (Stage 16.1 policy).

    The provider answered, so the call may have been billed and must never be
    released as if free. Cost is:
    - `estimated` from the versioned tariff applied to a defensible token bound
      (the caller's input-token upper bound + capped output), when a tariff is
      known — never `confirmed`;
    - otherwise the hold is kept `ambiguous` when a reservation exists (the
      financial outcome cannot be established), or an `unknown` row is appended
      for a reservation-less call (no configured business budget) so the
      attempt is not lost.

    Returns `(record_or_None, reservation_settled, status)`.
    """
    usage = LlmUsage(
        prompt_tokens=max(0, int(input_bound_tokens)),
        completion_tokens=max(0, int(max_output_tokens or 0)),
        total_tokens=max(0, int(input_bound_tokens)) + max(0, int(max_output_tokens or 0)),
        reported=False,
    )
    quote = pricing.price_usage(
        provider=provider,
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=None,
        usage_reported=False,
        provider_cost=None,
    )
    key = reservation_key or attempt_key
    if quote.status == "unknown" and reservation_id is not None:
        # Cannot price it and a hold exists: fail closed, keep exposure counted.
        won = await mark_ambiguous(session, business_id, reservation_id, "post_send_unpriced")
        return None, won, "ambiguous"
    record = await _append_row(
        session,
        business_id,
        key=key,
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
    won = False
    if reservation_id is not None:
        won = await _reservations.set_status(
            session,
            business_id,
            reservation_id,
            from_statuses=("active",),
            to_status="consumed",
            usage_record_id=record.id,
        )
    if task_id is not None:
        await recalculate_task_cost(session, business_id, task_id)
    return record, won, quote.status


async def release_reservation(
    session: AsyncSession, business_id: str, reservation_id: str | None, reason: str
) -> bool:
    """Proven non-execution (pre-send failure / definitive provider error)."""
    if reservation_id is None:
        return False
    return await _reservations.set_status(
        session,
        business_id,
        reservation_id,
        from_statuses=("active",),
        to_status="released",
        reason=reason,
    )


async def mark_ambiguous(
    session: AsyncSession, business_id: str, reservation_id: str | None, reason: str
) -> bool:
    """Outcome unknown — the provider may have billed. Held, never released."""
    if reservation_id is None:
        return False
    return await _reservations.set_status(
        session,
        business_id,
        reservation_id,
        from_statuses=("active",),
        to_status="ambiguous",
        reason=reason,
    )
