"""Stage 16.1 slice 3: Team Lead metering (reserve → call → settle)."""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.mock import MockProvider
from app.llm.types import (
    BadResponseError,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmUsage,
    ProviderError,
    ProviderTimeoutError,
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
    TargetMissingError,
    TeamLeadMeter,
)
from app.usage.settlement import record_success
from app.usage.tariffs import Tariff
from tests.conftest import login_as, seed_operator
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


async def test_business_scoped_call_without_budget_reserves_zero_amount(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """No business budget: still reserve, but with amount 0 (no money)."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)

    response = await _meter(factory).complete(
        MockProvider(texts=["hello"]), _request(), generation=0
    )

    assert response.text == "hello"
    usages, holds = await _rows(session)
    assert len(usages) == 1
    row = usages[0]
    assert row.project_id is None and row.task_id is None
    assert row.call_kind == "brief_questions"
    assert row.usage_status == "confirmed" and row.cost_source == "tariff"
    assert row.cost_amount == Decimal("0.10")
    assert len(holds) == 1
    hold = holds[0]
    assert hold.project_id is None and hold.task_id is None
    assert hold.amount == Decimal("0")
    assert hold.mode == "best_effort"
    assert hold.status == "consumed"
    assert hold.usage_record_id == row.id


async def test_business_scoped_retry_uses_distinct_run_seq_and_usage_key(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Two real calls with one attempt key keep distinct reservation identities."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory).complete(MockProvider(texts=["first"]), _request(), generation=0)
    await _meter(factory).complete(MockProvider(texts=["second"]), _request(), generation=0)

    usages, holds = await _rows(session)
    assert len(usages) == 2 and len(holds) == 2
    assert sorted(hold.run_seq for hold in holds) == [1, 2]
    assert len({hold.idempotency_key for hold in holds}) == 2
    assert len({usage.idempotency_key for usage in usages}) == 2
    for hold in holds:
        assert hold.amount == Decimal("0")
        assert hold.mode == "best_effort"
        assert hold.status == "consumed"


async def test_business_scoped_ambiguous_failure_keeps_zero_hold(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """A post-send 5xx must keep the zero-amount attempt visible, not lose it."""
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})

    provider = OpenAICompatProvider(
        api_key="test-only",
        default_model="mock",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError):
        await _meter(factory).complete(provider, _request(), generation=0)

    assert calls == 1
    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1
    hold = holds[0]
    assert hold.amount == Decimal("0")
    assert hold.mode == "best_effort"
    assert hold.status == "ambiguous"
    assert hold.reason == "provider_error"


async def test_business_scoped_cancellation_keeps_zero_hold(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Cancellation is ambiguous: the zero-amount attempt must stay visible."""
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["slow"], latency_s=30.0)
    entered = asyncio.Event()
    real_complete = provider.complete

    async def gated(request):  # type: ignore[no-untyped-def]
        entered.set()
        return await real_complete(request)

    provider.complete = gated  # type: ignore[method-assign]

    task = asyncio.ensure_future(_meter(factory).complete(provider, _request(), generation=0))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    usages, holds = await _rows(session)
    assert usages == []
    assert len(holds) == 1
    assert holds[0].amount == Decimal("0")
    assert holds[0].status == "ambiguous"
    assert holds[0].reason == "cancelled"


async def test_business_scoped_repeated_settlement_is_idempotent(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Re-settling one reservation cannot add a row or consume it twice."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory).complete(MockProvider(texts=["hello"]), _request(), generation=0)
    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    original_row, hold = usages[0], holds[0]

    replay = LlmResponse(
        text="replayed",
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True),
        model="mock",
        attempts=1,
    )
    async with factory() as settle_session:
        async with settle_session.begin():
            record, won = await record_success(
                settle_session,
                BIZ,
                reservation_id=hold.id,
                reservation_key=hold.idempotency_key,
                task_id=None,
                project_id=None,
                call_kind="brief_questions",
                attempt=0,
                attempt_key=hold.attempt_key,
                provider="mock",
                model="mock",
                response=replay,
            )

    assert won is False
    assert record.id == original_row.id
    usages_after, holds_after = await _rows(session)
    assert len(usages_after) == 1 and len(holds_after) == 1
    assert holds_after[0].status == "consumed"


async def test_business_zero_reservation_reports_no_monetary_exposure(
    session: AsyncSession, test_engine: AsyncEngine, api_client: httpx.AsyncClient
) -> None:
    """Usage reporting keeps zero-amount holds out of monetary reservation sums."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)
    await _meter(factory).complete(MockProvider(texts=["hello"]), _request(), generation=0)

    await seed_operator(session)
    csrf = await login_as(api_client)
    headers = {"X-CSRF-Token": csrf}
    res = await api_client.get(f"/api/v1/businesses/{BIZ}/usage", headers=headers)

    assert res.status_code == 200, res.text
    summary = res.json()["summary"]
    assert summary["confirmedCost"] == "0.10"
    reservations = summary["reservations"]
    assert Decimal(reservations["active"]) == Decimal("0")
    assert Decimal(reservations["ambiguous"]) == Decimal("0")
    assert Decimal(reservations["consumed"]) == Decimal("0")


async def test_missing_project_target_fails_before_provider(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["should not run"])

    with pytest.raises(TargetMissingError):
        await _meter(factory, project_id="proj_missing").complete(
            provider, _request(), generation=0
        )

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


async def test_missing_business_target_fails_before_provider(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    factory = session_factory(test_engine)
    provider = MockProvider(texts=["should not run"])
    meter = TeamLeadMeter(
        factory,
        SETTINGS,
        business_id="biz_missing",
        brief_id="b1",
        call_kind="brief_questions",
    )

    with pytest.raises(TargetMissingError):
        await meter.complete(provider, _request(), generation=0)

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert usages == [] and holds == []


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


async def test_usd_tariff_reservation_and_ledger_currency_are_compatible(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """A normalized USD tariff prices a USD reservation and writes USD exactly."""
    pricing.register_tariff(
        Tariff(
            provider="mock",
            model="mock",
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
            currency=" usd ",
        )
    )
    resolved = pricing.resolve_tariff("mock", "mock")
    assert resolved is not None and resolved.currency == "USD"

    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
        MockProvider(texts=["ok"]), _request(), generation=0
    )

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert holds[0].currency == "USD"
    assert usages[0].currency == "USD"
    assert holds[0].usage_record_id == usages[0].id


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


async def test_post_send_bad_response_without_cap_keeps_hold_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
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
            _request(max_tokens=None),
            generation=0,
        )

    usages, holds = await _rows(session)
    # Input-only pricing would silently drop the uncapped output exposure.
    assert usages == []
    assert len(holds) == 1
    assert holds[0].status == "ambiguous"
    assert holds[0].reason == "post_send_unpriced"


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
    assert [(row.project_id, row.call_kind) for row in usages] == [("proj_1", "brief_finalize")]


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

    expected_error = ProviderError if failure == "server_error" else ProviderTimeoutError
    with pytest.raises(expected_error):
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


async def test_total_deadline_timeout_keeps_business_hold_ambiguous(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """An expired local budget is an unknown provider outcome, not a free call."""
    await make_world(session)
    await session.commit()
    factory = session_factory(test_engine)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    provider = OpenAICompatProvider(
        api_key="test-only",
        default_model="mock",
        timeout_s=5.0,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    # The per-request cap (0.1s) takes precedence; one attempt = the budget.
    request = _request().model_copy(update={"timeout_s": 0.1})
    with pytest.raises(ProviderTimeoutError) as caught:
        await _meter(factory).complete(provider, request, generation=0)

    assert caught.value.ambiguous is True
    usages, holds = await _rows(session)
    assert usages == []  # no ledger row for an unconfirmed outcome
    assert len(holds) == 1
    hold = holds[0]
    assert hold.project_id is None and hold.task_id is None
    assert hold.status == "ambiguous"
    assert hold.reason == "provider_timeout"


async def test_unusable_usage_is_estimated_and_blocks_next_teamlead_reservation(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """The Team Lead path shares the adapter/settlement bypass and must close it."""

    pricing.register_tariff(
        Tariff(
            provider="openai_compat",
            model="mock",
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
        )
    )
    await make_world(session)
    input_bound = pricing.estimate_input_token_bound("hello", message_count=1)
    quote = pricing.upper_bound_cost(
        provider="openai_compat",
        model="mock",
        input_token_bound=input_bound,
        max_output_tokens=64,
    )
    assert quote is not None and quote.cost is not None
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": quote.cost})
    await session.commit()
    factory = session_factory(test_engine)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": "empty-usage-1",
                "model": "mock",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-only",
        default_model="mock",
        transport=httpx.MockTransport(handler),
    )
    await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
        provider, _request(max_tokens=64), generation=0
    )

    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert usages[0].usage_status == "estimated"
    assert usages[0].cost_amount == quote.cost
    assert holds[0].status == "consumed"
    assert holds[0].usage_record_id == usages[0].id
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")

    with pytest.raises(BudgetExhaustedError):
        await _meter(factory, call_kind="brief_finalize", project_id="proj_1").complete(
            provider, _request(max_tokens=64), generation=1
        )

    assert calls == 1
    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1


async def test_teamlead_reserve_and_settlement_do_not_deadlock(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent same-attempt reserve and settlement must not deadlock.

    Settlement must take the Project row lock BEFORE the Reservation lock,
    matching reserve's Project -> Reservation order. The reserve side is paused
    while holding the Project lock; the settlement side is proven to be waiting
    on that Project lock (pg_locks) before the reserve is released. With the old
    Reservation -> Project order the two transactions would form a real
    PostgreSQL deadlock cycle (see the negative-control test below).
    """
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    attempt_key = "tl:b1:brief_finalize:g0"
    input_bound = pricing.estimate_input_token_bound("hello", message_count=1)

    async with factory() as setup:
        async with setup.begin():
            r1 = await BudgetReservationRepository().reserve(
                setup,
                BIZ,
                project_id="proj_1",
                attempt_key=attempt_key,
                amount=Decimal("0.50"),
            )
            assert r1 is not None
            r1_id = r1.id
            r1_key = r1.idempotency_key
            r1_snapshot = {
                "id": r1.id,
                "project_id": r1.project_id,
                "task_id": r1.task_id,
                "attempt_key": r1.attempt_key,
                "amount": r1.amount,
                "run_seq": r1.run_seq,
                "idempotency_key": r1.idempotency_key,
            }

    real_supersede = BudgetReservationRepository._supersede_attempt
    real_project_lock = ProjectRepository.lock_for_update
    b_holds_project = asyncio.Event()
    a_attempting_project_lock = asyncio.Event()
    release_b = asyncio.Event()
    a_pid: dict[str, int] = {}

    async def paused_supersede(self, sess, business_id, key):
        if key == attempt_key:
            b_holds_project.set()
            await release_b.wait()
        return await real_supersede(self, sess, business_id, key)

    async def recording_project_lock(self, sess, business_id, project_id):
        pid = int((await sess.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        a_pid["pid"] = pid
        a_attempting_project_lock.set()
        return await real_project_lock(self, sess, business_id, project_id)

    monkeypatch.setattr(BudgetReservationRepository, "_supersede_attempt", paused_supersede)
    monkeypatch.setattr(ProjectRepository, "lock_for_update", recording_project_lock)

    meter = _meter(factory, call_kind="brief_finalize", project_id="proj_1")

    async def reserve_side():
        return await meter._reserve("mock", "mock", input_bound, 128, attempt_key)

    async def settle_side() -> None:
        response = LlmResponse(
            text="ok",
            usage=LlmUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                reported=True,
            ),
            model="mock",
            attempts=1,
        )
        await meter._settle_success(
            r1_id,
            r1_key,
            attempt_key,
            "mock",
            "mock",
            input_bound,
            128,
            response,
            0,
        )

    reserve_task = asyncio.ensure_future(reserve_side())
    settle_task: asyncio.Future | None = None
    try:
        await asyncio.wait_for(b_holds_project.wait(), 15.0)
        settle_task = asyncio.ensure_future(settle_side())
        await asyncio.wait_for(a_attempting_project_lock.wait(), 15.0)

        # Prove the settlement backend is actually blocked on a lock while
        # executing the Project row-lock query (the Project row is held by the
        # paused reserve transaction) before releasing it.
        async with factory() as observer:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10.0
            while True:
                state = (
                    await observer.execute(
                        text(
                            "SELECT a.wait_event_type, a.query,"
                            " (SELECT count(*) FROM pg_locks l"
                            "  WHERE l.pid = a.pid AND NOT l.granted) AS waiting"
                            " FROM pg_stat_activity a WHERE a.pid = :pid"
                        ),
                        {"pid": a_pid["pid"]},
                    )
                ).one()
                wait_type, query, waiting = state
                if (
                    wait_type == "Lock"
                    and "from projects" in (query or "").lower()
                    and int(waiting) > 0
                ):
                    break
                if loop.time() >= deadline:
                    pytest.fail("settlement never blocked on the project lock")
                await asyncio.sleep(0.01)

        release_b.set()
        await asyncio.wait_for(asyncio.gather(reserve_task, settle_task), 15.0)
    finally:
        release_b.set()
        for task in (reserve_task, settle_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (reserve_task, settle_task) if task is not None),
            return_exceptions=True,
        )

    await session.rollback()
    holds = list((await session.execute(select(BudgetReservation))).scalars().all())
    usages = list((await session.execute(select(UsageRecord))).scalars().all())
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    session.expunge_all()

    assert len(holds) == 2
    by_status = {hold.status: hold for hold in holds}
    assert set(by_status) == {"ambiguous", "active"}

    superseded = by_status["ambiguous"]
    assert superseded.id == r1_id
    assert superseded.reason == "superseded_attempt"
    assert superseded.status != "consumed"
    assert superseded.project_id == r1_snapshot["project_id"]
    assert superseded.task_id == r1_snapshot["task_id"]
    assert superseded.attempt_key == r1_snapshot["attempt_key"]
    assert superseded.amount == r1_snapshot["amount"]
    assert superseded.run_seq == r1_snapshot["run_seq"]
    assert superseded.idempotency_key == r1_snapshot["idempotency_key"]

    new_hold = by_status["active"]
    assert new_hold.attempt_key == attempt_key
    assert new_hold.execution_generation is None
    assert new_hold.task_id is None
    assert new_hold.idempotency_key == f"reserve:{attempt_key}:2"

    assert usages == []
    assert [row for row in usages if row.usage_status == "confirmed"] == []
    assert project is not None and project.ai_spent == Decimal("0")

    # Both transactions released the Project row lock.
    async with factory() as probe:
        async with probe.begin():
            locked = (
                await probe.execute(
                    text("SELECT id FROM projects WHERE id = 'proj_1' FOR UPDATE NOWAIT")
                )
            ).scalar_one()
            assert locked == "proj_1"


async def test_old_teamlead_lock_order_would_deadlock(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Negative control: the pre-fix Reservation -> Project order deadlocks.

    This intentionally reproduces the old acquisition order with real
    repository SQL and requires PostgreSQL to detect the cycle (SQLSTATE
    40P01). It proves the corrected test above is regression-sensitive rather
    than merely observing a lucky schedule.
    """
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)
    attempt_key = "tl:b1:brief_finalize:g0"

    async with factory() as setup:
        async with setup.begin():
            r1 = await BudgetReservationRepository().reserve(
                setup,
                BIZ,
                project_id="proj_1",
                attempt_key=attempt_key,
                amount=Decimal("0.50"),
            )
            assert r1 is not None
            r1_id = r1.id

    a_locked_reservation = asyncio.Event()
    b_locked_project = asyncio.Event()
    allow_a = asyncio.Event()
    allow_b = asyncio.Event()

    async def old_settlement_order() -> str:
        async with factory() as sess:
            async with sess.begin():
                await BudgetReservationRepository().get_for_settlement(sess, BIZ, r1_id)
                a_locked_reservation.set()
                await allow_a.wait()
                await ProjectRepository().lock_for_update(sess, BIZ, "proj_1")
                return "settlement"

    async def old_reserve_order() -> str:
        async with factory() as sess:
            async with sess.begin():
                await ProjectRepository().lock_for_update(sess, BIZ, "proj_1")
                b_locked_project.set()
                await allow_b.wait()
                await BudgetReservationRepository()._supersede_attempt(sess, BIZ, attempt_key)
                return "reserve"

    a_task = asyncio.ensure_future(old_settlement_order())
    b_task = asyncio.ensure_future(old_reserve_order())
    try:
        await asyncio.wait_for(
            asyncio.gather(a_locked_reservation.wait(), b_locked_project.wait()),
            15.0,
        )
        allow_a.set()
        allow_b.set()
        results = await asyncio.wait_for(
            asyncio.gather(a_task, b_task, return_exceptions=True), 15.0
        )
    finally:
        allow_a.set()
        allow_b.set()
        for task in (a_task, b_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(a_task, b_task, return_exceptions=True)

    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(errors) == 1, results
    error = errors[0]
    assert isinstance(error, DBAPIError)
    assert getattr(error.orig, "sqlstate", None) == "40P01"
