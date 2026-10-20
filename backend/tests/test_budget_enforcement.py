"""Stage 16 slice 4: budget reservation + enforcement in the real executor."""

import asyncio
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution.executor import ExecutionRequest, ExecutionResult, execute_task
from app.execution.prompts import RESULT_SCHEMA, build_system_prompt, build_user_prompt
from app.llm.mock import MockProvider
from app.llm.types import ProviderError, TransientProviderError
from app.persistence.engine import session_factory
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import ProjectRepository
from app.usage import pricing
from app.usage.tariffs import Tariff
from tests.factories import BIZ, make_task, make_world

MODEL = "mock"
TARIFF = Tariff(
    provider="mock",
    model=MODEL,
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)
MAX_OUT = 16


def _settings(**over: object) -> Settings:
    data: dict = {"llm_max_tokens": MAX_OUT, "usage_unpriced_reserve_amount": "0.25"}
    data.update(over)
    return Settings(**data)


def _request(settings: Settings | None = None, task_id: str = "t_1") -> ExecutionRequest:
    return ExecutionRequest(
        business_id=BIZ,
        project_id="proj_1",
        task_id=task_id,
        settings=settings or _settings(),
    )


async def _run(
    session: AsyncSession, provider: MockProvider, request: ExecutionRequest
) -> ExecutionResult:
    await session.commit()
    return await execute_task(session, provider, request)


def _bound() -> Decimal:
    input_bound = pricing.estimate_input_token_bound(
        build_system_prompt("Tester", []),
        build_user_prompt("Test Business", "P", "", "T", ""),
        json.dumps(dict(RESULT_SCHEMA)),
    )
    quote = pricing.upper_bound_cost(
        provider="mock", model=MODEL, input_token_bound=input_bound, max_output_tokens=MAX_OUT
    )
    assert quote is not None and quote.cost is not None
    return quote.cost


async def _reservations(session: AsyncSession) -> list[BudgetReservation]:
    await session.rollback()
    res = await session.execute(select(BudgetReservation).order_by(BudgetReservation.run_seq))
    rows = list(res.scalars().all())
    session.expunge_all()
    return rows


async def _usages(session: AsyncSession) -> list[UsageRecord]:
    await session.rollback()
    res = await session.execute(select(UsageRecord))
    rows = list(res.scalars().all())
    session.expunge_all()
    return rows


async def test_unpriced_budgeted_project_fails_closed(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("5.00")})
    provider = MockProvider(texts=['{"summary": "x"}'])

    result = await _run(session, provider, _request())

    assert result.outcome == "refused" and result.error_code == "budget_unpriced"
    assert provider.calls == []
    assert (await make_task_status(session)) == "not_started"
    assert await _reservations(session) == []
    assert await _usages(session) == []


async def make_task_status(session: AsyncSession) -> str:
    from app.persistence.repositories.concrete import TaskRepository

    await session.rollback()
    task = await TaskRepository().get(session, BIZ, "t_1")
    assert task is not None
    return task.status


async def test_priced_success_consumes_reservation_and_accrues(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    provider = MockProvider(texts=['{"summary": "x"}'])

    result = await _run(session, provider, _request())

    assert result.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "confirmed" and usages[0].cost_source == "tariff"
    assert usages[0].cost_amount == Decimal("0.10")
    usage_id = usages[0].id
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    assert reservations[0].mode == "guaranteed"
    assert reservations[0].usage_record_id == usage_id
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0.10")


async def test_budget_too_small_for_upper_bound_refuses_before_send(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("0.50")})
    provider = MockProvider(texts=['{"summary": "x"}'])

    result = await _run(session, provider, _request())

    assert result.outcome == "refused" and result.error_code == "budget_exceeded"
    assert provider.calls == []
    assert await make_task_status(session) == "not_started"
    assert await _reservations(session) == []


async def test_allow_unpriced_opt_in_is_best_effort(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("5.00"), "allow_unpriced_ai": True},
    )
    provider = MockProvider(texts=['{"summary": "x"}'])

    result = await _run(session, provider, _request())

    assert result.outcome == "completed"
    reservations = await _reservations(session)
    assert len(reservations) == 1
    assert reservations[0].mode == "best_effort"
    assert reservations[0].status == "consumed"
    assert reservations[0].amount == Decimal("0.25")
    usages = await _usages(session)
    assert len(usages) == 1 and usages[0].usage_status == "unknown"
    assert usages[0].cost_amount is None
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")


async def test_ambiguous_failure_holds_budget(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": _bound()})
    lost = TransientProviderError("connection lost after send")
    lost.ambiguous = True
    provider = MockProvider(errors=[lost])

    result = await _run(session, provider, _request())

    assert result.outcome == "failed"
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"
    assert await _usages(session) == []
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")

    # The held amount still blocks: a fresh attempt cannot fit the budget.
    second = await _run(session, MockProvider(texts=['{"summary": "y"}']), _request())

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert len(await _reservations(session)) == 1


async def test_definitive_failure_releases_and_frees_budget(session: AsyncSession) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": _bound()})
    provider = MockProvider(errors=[ProviderError("definitive")])

    first = await _run(session, provider, _request())

    assert first.outcome == "failed"
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "released"

    second = await _run(session, MockProvider(texts=['{"summary": "y"}']), _request())

    assert second.outcome == "completed"


async def test_concurrent_reservations_respect_budget(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": _bound()})
    await session.commit()

    factory = session_factory(test_engine)

    async def run(task_id: str) -> ExecutionResult:
        async with factory() as sess:
            return await execute_task(
                sess, MockProvider(texts=['{"summary": "x"}']), _request(task_id=task_id)
            )

    results = await asyncio.gather(run("t_1"), run("t_2"))

    outcomes = sorted(r.outcome for r in results)
    assert outcomes == ["completed", "refused"]
    consumed = [r for r in await _reservations(session) if r.status == "consumed"]
    assert len(consumed) == 1
