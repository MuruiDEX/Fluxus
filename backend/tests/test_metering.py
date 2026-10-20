"""Stage 16.1 slice 3: Team Lead metering (reserve → call → settle)."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
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
from app.persistence.repositories.concrete import ProjectRepository
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


def _request() -> LlmRequest:
    return LlmRequest(messages=[LlmMessage(role="user", content="hello")], model="mock")


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
