"""Team Lead provider metering (Stage 16.1).

Wraps each Team Lead `provider.complete` call with the SAME usage/budget
discipline as the executor:

1. reserve before the network call, in its own transaction — never holding a
   DB transaction across the provider HTTP call;
2. invoke the provider with no transaction open;
3. settle afterwards in its own committed transaction (ledger row + reservation
   CAS + project-spend recompute).

Project-scoped calls reserve against `Project.ai_budget`. Business-scoped calls
(questions/summary/plan, which have no project) reserve against
`Business.ai_budget`; a business with no configured budget keeps the Stage 16.1
bare-ledger behavior. Provider failures are classified exactly like the
executor, with Stage 16.1's post-send policy: a malformed response is priced
`estimated` from a defensible tariff bound, or the hold is kept `ambiguous`; it
is NEVER released and NEVER recorded as zero.

Stage 18 (S1): the reserved amount is derived from the output limit the
provider request ACTUALLY carries (`request.max_tokens`). No cap is ever
assumed, so an uncapped request yields no conservative budget bound and
therefore no `guaranteed` reservation.
"""

import asyncio
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.llm.base import LlmProvider
from app.llm.types import BadResponseError, LlmError, LlmRequest, LlmResponse
from app.persistence.repositories.base import quantize_money
from app.persistence.repositories.concrete import BusinessRepository, ProjectRepository
from app.persistence.repositories.spend import recalculate_project_spend
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage import pricing
from app.usage.settlement import (
    mark_ambiguous,
    record_estimated_failure,
    record_success,
    release_reservation,
)

_projects = ProjectRepository()
_businesses = BusinessRepository()
_reservations = BudgetReservationRepository()


class MeteringRefused(Exception):
    """The call was refused before it was sent (budget guardrail)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BudgetUnpricedError(MeteringRefused):
    def __init__(self) -> None:
        super().__init__(
            "budget_unpriced",
            "Project has an AI budget but this model has no priceable tariff; "
            "set a tariff or enable allow_unpriced_ai.",
        )


class BudgetExhaustedError(MeteringRefused):
    def __init__(self) -> None:
        super().__init__(
            "budget_exceeded",
            "Concurrent calls exhausted the project AI budget; reservation refused.",
        )


class TargetMissingError(MeteringRefused):
    """The scoped project/business disappeared before reservation."""

    def __init__(self, target: str) -> None:
        super().__init__(
            "target_missing",
            f"{target.capitalize()} target is missing; refusing before the provider call.",
        )


class BusinessBudgetUnpricedError(BudgetUnpricedError):
    def __init__(self) -> None:
        MeteringRefused.__init__(
            self,
            "budget_unpriced",
            "Business has an AI budget but this model has no priceable tariff; "
            "set a tariff or enable allow_unpriced_ai.",
        )


class BusinessBudgetExhaustedError(BudgetExhaustedError):
    def __init__(self) -> None:
        MeteringRefused.__init__(
            self,
            "budget_exceeded",
            "Concurrent calls exhausted the business AI budget; reservation refused.",
        )


def _input_bound(request: LlmRequest) -> int:
    parts = [m.content for m in request.messages]
    if request.structured is not None and request.structured.schema:
        parts.append(json.dumps(request.structured.schema))
    return pricing.estimate_input_token_bound(*parts, message_count=len(request.messages))


class TeamLeadMeter:
    """Per-request metering scope for one Team Lead operation."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        business_id: str,
        brief_id: str,
        call_kind: str,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.business_id = business_id
        self.brief_id = brief_id
        self.call_kind = call_kind
        self.project_id = project_id
        self.agent_id = agent_id

    async def complete(
        self, provider: LlmProvider, request: LlmRequest, *, generation: int
    ) -> LlmResponse:
        provider_name = getattr(provider, "name", "unknown")
        model_name = getattr(provider, "_default_model", None) or request.model or "mock"
        attempt_key = f"tl:{self.brief_id}:{self.call_kind}:g{generation}"
        input_bound = _input_bound(request)
        # The reservation may only price a limit the provider request ACTUALLY
        # enforces. Never assume a cap: an uncapped request has no conservative
        # bound, so it must not produce a `guaranteed` reservation (Stage 18
        # S1). `None` -> `upper_bound_cost` returns None -> the fail-closed
        # branches below apply (budget_unpriced / best_effort).
        max_output = request.max_tokens
        reservation_id, reservation_key = await self._reserve(
            provider_name, model_name, input_bound, max_output, attempt_key
        )
        try:
            response = await provider.complete(request)
        except asyncio.CancelledError:
            # Outcome unknown: fail closed, keep any hold counted, re-raise.
            await self._settle_ambiguous(reservation_id, "cancelled")
            raise
        except BadResponseError:
            # Post-send: the provider answered but the body was unusable.
            await self._settle_post_send(
                reservation_id,
                reservation_key,
                attempt_key,
                provider_name,
                model_name,
                input_bound,
                max_output,
                generation,
            )
            raise
        except LlmError as exc:
            if getattr(exc, "ambiguous", False):
                await self._settle_ambiguous(reservation_id, exc.code)
            else:
                await self._settle_released(reservation_id, exc.code)
            raise
        await self._settle_success(
            reservation_id,
            reservation_key,
            attempt_key,
            provider_name,
            model_name,
            input_bound,
            max_output,
            response,
            generation,
        )
        return response

    async def _reserve(
        self,
        provider_name: str,
        model_name: str,
        input_bound: int,
        max_output: int | None,
        attempt_key: str,
    ) -> tuple[str | None, str | None]:
        bound = pricing.upper_bound_cost(
            provider=provider_name,
            model=model_name,
            input_token_bound=input_bound,
            max_output_tokens=max_output,
        )
        async with self.factory() as session:
            async with session.begin():
                if self.project_id is None:
                    return await self._reserve_business(
                        session, bound, attempt_key
                    )
                project = await _projects.get(session, self.business_id, self.project_id)
                if project is None:
                    raise TargetMissingError("project")
                # Lock and refresh before choosing a reservation branch: a
                # concurrent PATCH must not leave us deciding from the
                # identity-map copy loaded before the lock.
                await session.refresh(project, with_for_update=True)
                ai_budget = project.ai_budget if project.ai_budget is not None else 0
                if bound is not None:
                    # Tariff + real output cap: a conservative bound exists.
                    amount = bound.cost or Decimal("0")
                    mode = "guaranteed"
                elif ai_budget > 0 and not project.allow_unpriced_ai:
                    # Budgeted project without a conservative bound -> fail
                    # closed BEFORE the provider (Stage 18 S1).
                    raise BudgetUnpricedError()
                elif project.allow_unpriced_ai and ai_budget > 0:
                    amount = quantize_money(self.settings.usage_unpriced_reserve_amount)
                    mode = "best_effort"
                else:
                    # No budget to protect and no conservative bound: nothing
                    # is guaranteed, so nothing may be labelled `guaranteed`.
                    amount = Decimal("0")
                    mode = "best_effort"
                reservation = await _reservations.reserve(
                    session,
                    self.business_id,
                    project_id=self.project_id,
                    task_id=None,
                    attempt_key=attempt_key,
                    amount=amount,
                    mode=mode,
                )
                if reservation is None:
                    raise BudgetExhaustedError()
                return reservation.id, reservation.idempotency_key

    async def _reserve_business(
        self,
        session: AsyncSession,
        bound: pricing.CostQuote | None,
        attempt_key: str,
    ) -> tuple[str | None, str | None]:
        business = await _businesses.get(session, self.business_id, self.business_id)
        if business is None:
            raise TargetMissingError("business")
        # The unlocked get above may race a concurrent budget PATCH. Refresh
        # under the row lock so the branch below uses the committed value.
        await session.refresh(business, with_for_update=True)
        ai_budget = business.ai_budget if business.ai_budget is not None else 0
        if ai_budget <= 0:
            # No business limit: preserve the existing bare-ledger behavior.
            return None, None
        if bound is not None:
            amount = bound.cost or Decimal("0")
            mode = "guaranteed"
        elif not business.allow_unpriced_ai:
            raise BusinessBudgetUnpricedError()
        else:
            amount = quantize_money(self.settings.usage_unpriced_reserve_amount)
            mode = "best_effort"
        reservation = await _reservations.reserve_business(
            session,
            self.business_id,
            attempt_key=attempt_key,
            amount=amount,
            mode=mode,
        )
        if reservation is None:
            raise BusinessBudgetExhaustedError()
        return reservation.id, reservation.idempotency_key

    async def _settle_success(
        self,
        reservation_id: str | None,
        reservation_key: str | None,
        attempt_key: str,
        provider_name: str,
        model_name: str,
        input_bound: int,
        max_output: int | None,
        response: LlmResponse,
        generation: int,
    ) -> None:
        async with self.factory() as session:
            async with session.begin():
                await record_success(
                    session,
                    self.business_id,
                    reservation_id=reservation_id,
                    reservation_key=reservation_key,
                    task_id=None,
                    project_id=self.project_id,
                    agent_id=self.agent_id,
                    call_kind=self.call_kind,
                    attempt=generation,
                    attempt_key=attempt_key,
                    provider=provider_name,
                    model=model_name,
                    response=response,
                    input_bound_tokens=input_bound,
                    max_output_tokens=max_output,
                )
                if self.project_id is not None:
                    await recalculate_project_spend(
                        session, self.business_id, self.project_id
                    )

    async def _settle_post_send(
        self,
        reservation_id: str | None,
        reservation_key: str | None,
        attempt_key: str,
        provider_name: str,
        model_name: str,
        input_bound: int,
        max_output: int | None,
        generation: int,
    ) -> None:
        async with self.factory() as session:
            async with session.begin():
                record, _won, status = await record_estimated_failure(
                    session,
                    self.business_id,
                    reservation_id=reservation_id,
                    reservation_key=reservation_key,
                    task_id=None,
                    project_id=self.project_id,
                    agent_id=self.agent_id,
                    call_kind=self.call_kind,
                    attempt=generation,
                    attempt_key=attempt_key,
                    provider=provider_name,
                    model=model_name,
                    input_bound_tokens=input_bound,
                    max_output_tokens=max_output,
                )
                if (
                    self.project_id is not None
                    and record is not None
                    and status == "confirmed"
                ):
                    await recalculate_project_spend(
                        session, self.business_id, self.project_id
                    )

    async def _settle_ambiguous(self, reservation_id: str | None, reason: str) -> None:
        if reservation_id is None:
            return
        async with self.factory() as session:
            async with session.begin():
                await mark_ambiguous(session, self.business_id, reservation_id, reason)

    async def _settle_released(self, reservation_id: str | None, reason: str) -> None:
        if reservation_id is None:
            return
        async with self.factory() as session:
            async with session.begin():
                await release_reservation(
                    session, self.business_id, reservation_id, reason
                )
