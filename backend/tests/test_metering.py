"""Stage 16.1 slice 3: Team Lead metering (reserve → call → settle)."""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.mock import MockProvider
from app.llm.types import (
    BadResponseError,
    LlmMessage,
    LlmRequest,
    ProviderError,
    TransientProviderError,
)
from app.persistence.engine import session_factory
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import BusinessRepository, ProjectRepository
from app.persistence.repositories.usage import BudgetReservationRepository
from app.usage import pricing
from app.usage.metering import (
    BudgetExhaustedError,
    BudgetUnpricedError,
    TeamLeadMeter,
)
from app.usage.tariffs import Tariff
from tests.factories import BIZ, make_world

TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)
SETTINGS = Settings(llm_max_tokens=16, usage_unpriced_reserve_amount="0.25")


def _request(max_tokens: int | None = 128) -> LlmRequest:
    # Project-scoped calls need a real output cap: the reservation may only
    # price a bound the provider request actually enforces (Stage 18 S1).
    return LlmRequest(
        messages=[LlmMessage(role="user", content="hello")],
        model="mock",
        max_tokens=max_tokens,
    )


def _meter(
    factory, *, call_kind: str = "brief_questions", project_id: str | None = None
) -> TeamLeadMeter:
    return TeamLeadMeter(
        factory,
        SETTINGS,
        business_id=BIZ,
        brief_id="b1",
        call_kind=call_kind,
        project_id=project_id,
    )


async def _rows(session: AsyncSession) -> tuple[list[UsageRecord], list[BudgetReservation]]:
    await session.rollback()
    usages = list((await session.execute(select(UsageRecord))).scalars().all())
    holds = list((await session.execute(select(BudgetReservation))).scalars().all())
    session.expunge_all()
    return usages, holds


async def test_business_scoped_call_appends_bare_confirmed_row(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)

    response = await _meter(factory).complete(
        MockProvider(texts=["hello"]), _request(), generation=0
    )

    assert response.text == "hello"
    usages, holds = await _rows(session)
    assert holds == []
    assert len(usages) == 1
    row = usages[0]
    assert row.project_id is None and row.task_id is None
    assert row.call_kind == "brief_questions"
    assert row.usage_status == "confirmed" and row.cost_source == "tariff"
    assert row.cost_amount == Decimal("0.10")


async def test_project_scoped_call_reserves_then_settles(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
        MockProvider(texts=["ok"]), _request(), generation=0
    )

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert holds[0].status == "consumed"
    assert holds[0].task_id is None and holds[0].project_id == "proj_1"
    assert holds[0].attempt_key == "tl:b1:brief_finalize:g0"
    assert usages[0].project_id == "proj_1" and usages[0].task_id is None
    assert usages[0].call_kind == "brief_finalize"
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0.10")


async def test_post_send_bad_response_is_estimated_not_released(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    with pytest.raises(BadResponseError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            MockProvider(errors=[BadResponseError("malformed")]),
            _request(),
            generation=0,
        )

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert usages[0].usage_status == "estimated"
    assert usages[0].cost_source == "tariff"
    assert usages[0].cost_amount is not None and usages[0].cost_amount > 0
    assert usages[0].call_kind == "brief_finalize"
    assert holds[0].status == "consumed"
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")


async def test_post_send_unpriced_keeps_hold_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("5.00"), "allow_unpriced_ai": True},
    )
    await session.commit()
    factory = session_factory(test_engine)

    with pytest.raises(BadResponseError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            MockProvider(errors=[BadResponseError("malformed")]),
            _request(),
            generation=0,
        )

    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1
    assert holds[0].status == "ambiguous"
    assert holds[0].reason == "post_send_unpriced"


async def test_pre_send_definitive_error_releases(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    with pytest.raises(ProviderError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            MockProvider(errors=[ProviderError("down")]), _request(), generation=0
        )

    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1 and holds[0].status == "released"


async def test_pre_send_ambiguous_error_holds(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    lost = TransientProviderError("connection lost")
    lost.ambiguous = True

    with pytest.raises(TransientProviderError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            MockProvider(errors=[lost]), _request(), generation=0
        )

    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1 and holds[0].status == "ambiguous"


async def test_unpriced_budgeted_project_refuses_before_send(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("5.00")})
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["never"])

    with pytest.raises(BudgetUnpricedError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(), generation=0
        )

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_uncapped_budgeted_project_refuses_before_send(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """A tariff alone is not enough: no cap means no provable budget bound."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["never"])

    with pytest.raises(BudgetUnpricedError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(max_tokens=None), generation=0
        )

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_exhausted_budget_refuses_before_send(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("0.01"), "allow_unpriced_ai": True},
    )
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["never"])

    with pytest.raises(BudgetExhaustedError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(), generation=0
        )

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_cancellation_holds_reservation(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["slow"], latency_s=30.0)
    entered = asyncio.Event()
    real_complete = provider.complete

    async def gated(request):  # type: ignore[no-untyped-def]
        entered.set()
        return await real_complete(request)

    provider.complete = gated  # type: ignore[method-assign]

    task = asyncio.ensure_future(
        _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(), generation=0
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1 and holds[0].status == "ambiguous"
    assert holds[0].reason == "cancelled"


# ------------------------------------------- business-scoped budget (S2) ---


async def _set_business_budget(session: AsyncSession, **patch: object) -> None:
    await BusinessRepository().update(session, BIZ, BIZ, patch)


async def test_business_scoped_call_reserves_then_settles(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("100.00"))
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory).complete(MockProvider(texts=["hello"]), _request(), generation=0)

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    hold = holds[0]
    assert hold.project_id is None and hold.task_id is None
    assert hold.status == "consumed" and hold.mode == "guaranteed"
    assert hold.attempt_key == "tl:b1:brief_questions:g0"
    assert usages[0].project_id is None and usages[0].task_id is None
    assert usages[0].call_kind == "brief_questions"
    assert usages[0].usage_status == "confirmed"
    assert usages[0].cost_amount == Decimal("0.10")


async def test_business_scoped_unpriced_budget_refuses_before_send(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("5.00"))
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["never"])

    with pytest.raises(BudgetUnpricedError):
        await _meter(factory).complete(provider, _request(), generation=0)

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_business_scoped_uncapped_budget_refuses_before_send(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("100.00"))
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["never"])

    with pytest.raises(BudgetUnpricedError):
        await _meter(factory).complete(provider, _request(max_tokens=None), generation=0)

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_business_budget_counts_project_spend(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
        MockProvider(texts=["ok"]), _request(), generation=0
    )
    await _set_business_budget(session, ai_budget=Decimal("0.19"))
    await session.commit()

    provider = MockProvider(texts=["never"])
    with pytest.raises(BudgetExhaustedError):
        await _meter(factory).complete(provider, _request(), generation=0)

    assert provider.calls == []
    usages, _holds = await _rows(session)
    assert [(row.project_id, row.call_kind) for row in usages] == [
        ("proj_1", "brief_finalize")
    ]


async def test_business_budget_counts_pending_reservations(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("0.20"))
    hold = await BudgetReservationRepository().reserve_business(
        session,
        BIZ,
        attempt_key="manual:hold",
        amount=Decimal("0.15"),
    )
    assert hold is not None
    await session.commit()
    factory = session_factory(test_engine)

    provider = MockProvider(texts=["never"])
    with pytest.raises(BudgetExhaustedError):
        await _meter(factory).complete(provider, _request(), generation=0)

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1 and holds[0].status == "active"


async def test_business_reservation_uses_locked_budget_not_stale_orm(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("100.00"))
    await session.commit()
    factory = session_factory(test_engine)

    async with factory() as stale_session:
        await stale_session.begin()
        stale = await BusinessRepository().get(stale_session, BIZ, BIZ)
        assert stale is not None and stale.ai_budget == Decimal("100.00")

        async with factory() as updater:
            async with updater.begin():
                row = await BusinessRepository().get(updater, BIZ, BIZ)
                assert row is not None
                row.ai_budget = Decimal("0.50")

        # The identity map still holds 100.00; the locked reservation read must
        # refresh and see the committed 0.50 instead.
        reservation = await BudgetReservationRepository().reserve_business(
            stale_session,
            BIZ,
            attempt_key="stale-budget",
            amount=Decimal("0.75"),
        )
        await stale_session.commit()

    assert reservation is None


async def test_concurrent_business_reservations_respect_budget(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("1.00"))
    await session.commit()
    factory = session_factory(test_engine)

    async def reserve(key: str) -> bool:
        async with factory() as sess:
            async with sess.begin():
                row = await BudgetReservationRepository().reserve_business(
                    sess,
                    BIZ,
                    attempt_key=key,
                    amount=Decimal("0.60"),
                )
                return row is not None

    results = await asyncio.gather(reserve("concurrent-a"), reserve("concurrent-b"))
    assert sorted(results) == [False, True]

    await session.rollback()
    rows = list(
        (
            await session.execute(
                select(BudgetReservation).where(BudgetReservation.business_id == BIZ)
            )
        )
        .scalars()
        .all()
    )
    session.expunge_all()
    assert sum((row.amount for row in rows), Decimal("0")) <= Decimal("1.00")


async def test_business_budget_reserves_unicode_input_bound(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await _set_business_budget(session, ai_budget=Decimal("100.00"))
    await session.commit()
    factory = session_factory(test_engine)
    content = "Привет, 你好"
    request = LlmRequest(
        messages=[LlmMessage(role="user", content=content)],
        model="mock",
        max_tokens=16,
    )
    input_bound = pricing.estimate_input_token_bound(content, message_count=1)
    quote = pricing.upper_bound_cost(
        provider="mock",
        model="mock",
        input_token_bound=input_bound,
        max_output_tokens=16,
    )
    assert quote is not None and quote.cost is not None
    assert input_bound > len(content)

    await _meter(factory).complete(MockProvider(texts=["ok"]), request, generation=0)

    _usages, holds = await _rows(session)
    assert len(holds) == 1 and holds[0].amount == quote.cost


async def test_business_allow_unpriced_uses_best_effort_hold(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await _set_business_budget(
        session,
        ai_budget=Decimal("5.00"),
        allow_unpriced_ai=True,
    )
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory).complete(MockProvider(texts=["hello"]), _request(), generation=0)

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert holds[0].status == "consumed" and holds[0].mode == "best_effort"
    assert holds[0].amount == Decimal("0.25")
    assert usages[0].usage_status == "unknown" and usages[0].cost_amount is None


@pytest.mark.parametrize("failure", ["server_error", "post_send_timeout"])
async def test_ambiguous_provider_failure_keeps_hold_without_ledger_row(
    session: AsyncSession, test_engine: AsyncEngine, failure: str
) -> None:
    """A 5xx or post-send timeout is never retried or released as free."""
    pricing.register_tariff(
        Tariff(
            provider="openai_compat",
            model="mock",
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
        )
    )
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "post_send_timeout":
            raise httpx.ReadTimeout("provider response timed out", request=request)
        return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})

    provider = OpenAICompatProvider(
        api_key="test-only",
        default_model="mock",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(), generation=0
        )

    assert calls == 1
    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1
    assert holds[0].status == "ambiguous"
    expected_reason = "provider_error" if failure == "server_error" else "provider_timeout"
    assert holds[0].reason == expected_reason
