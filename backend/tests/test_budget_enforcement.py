"""Stage 16 slice 4: budget reservation + enforcement in the real executor."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution import executor as executor_module
from app.execution.executor import ExecutionRequest, ExecutionResult, execute_task
from app.execution.prompts import RESULT_SCHEMA, build_system_prompt, build_user_prompt
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.mock import MockProvider
from app.llm.types import LlmUsage, ProviderError, TransientProviderError
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


def _bound_for(provider: str, model: str) -> Decimal:
    input_bound = pricing.estimate_input_token_bound(
        build_system_prompt("Tester", []),
        build_user_prompt("Test Business", "P", "", "T", ""),
        json.dumps(dict(RESULT_SCHEMA)),
        message_count=2,
    )
    quote = pricing.upper_bound_cost(
        provider=provider, model=model, input_token_bound=input_bound, max_output_tokens=MAX_OUT
    )
    assert quote is not None and quote.cost is not None
    return quote.cost


def _bound() -> Decimal:
    return _bound_for("mock", MODEL)


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


async def test_missing_usage_is_estimated_and_blocks_next_reservation(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    bound = _bound()
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": bound})

    first_provider = MockProvider(texts=['{"summary": "x"}'], usage=LlmUsage())
    first = await _run(session, first_provider, _request(task_id="t_1"))

    assert first.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "estimated"
    assert usages[0].cost_amount == bound
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")

    # The provider cannot be used repeatedly for free by omitting usage.
    second_provider = MockProvider(texts=['{"summary": "y"}'], usage=LlmUsage())
    second = await _run(session, second_provider, _request(task_id="t_2"))

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert second_provider.calls == []


async def test_estimated_hold_is_not_double_counted_after_settlement(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    bound = _bound()
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": bound * 2})

    first = await _run(
        session,
        MockProvider(texts=['{"summary": "x"}'], usage=LlmUsage()),
        _request(task_id="t_1"),
    )
    assert first.outcome == "completed"

    # One estimated ledger row + one new bound must fit. If the consumed hold
    # were also counted, this second reservation would exceed the ceiling.
    second = await _run(
        session,
        MockProvider(texts=['{"summary": "y"}'], usage=LlmUsage()),
        _request(task_id="t_2"),
    )
    assert second.outcome == "completed"

    usages = await _usages(session)
    assert len(usages) == 2 and all(row.usage_status == "estimated" for row in usages)
    reservations = await _reservations(session)
    assert len(reservations) == 2 and all(r.status == "consumed" for r in reservations)


async def test_unknown_task_hold_remains_budget_exposure(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("0.40"), "allow_unpriced_ai": True},
    )
    first_provider = MockProvider(texts=['{"summary": "x"}'])
    first_provider._default_model = "unknown-task-model"
    first = await _run(session, first_provider, _request(task_id="t_1"))

    assert first.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1 and usages[0].usage_status == "unknown"
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    assert reservations[0].amount == Decimal("0.25")

    second_provider = MockProvider(texts=['{"summary": "y"}'])
    second_provider._default_model = "unknown-task-model"
    second = await _run(session, second_provider, _request(task_id="t_2"))

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert second_provider.calls == []


async def test_missing_usage_without_bound_keeps_hold_ambiguous(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("0.25"), "allow_unpriced_ai": True},
    )
    settings = _settings(llm_max_tokens=None)
    first_provider = MockProvider(texts=['{"summary": "x"}'], usage=LlmUsage())
    first_provider._default_model = "missing-unpriced-model"
    first = await _run(session, first_provider, _request(settings, task_id="t_1"))

    assert first.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1 and usages[0].usage_status == "unknown"
    assert usages[0].cost_amount is None
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "ambiguous"

    second_provider = MockProvider(texts=['{"summary": "y"}'], usage=LlmUsage())
    second_provider._default_model = "missing-unpriced-model"
    second = await _run(session, second_provider, _request(settings, task_id="t_2"))

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert second_provider.calls == []


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"total_tokens": 100},
        {"prompt_tokens": 10},
        {"completion_tokens": 5},
        {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
        {"prompt_tokens": "not-a-number", "completion_tokens": 1},
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99},
    ],
    ids=[
        "empty",
        "total-only",
        "prompt-only",
        "completion-only",
        "negative",
        "malformed-number",
        "inconsistent-total",
    ],
)
async def test_unusable_adapter_usage_is_estimated_and_blocks_next_reservation(
    session: AsyncSession, usage: dict
) -> None:
    """Unusable usage cannot become a confirmed zero and then be reused free."""

    pricing.register_tariff(
        Tariff(
            provider="openai_compat",
            model="malformed",
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
        )
    )
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    bound = _bound_for("openai_compat", "malformed")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": bound})
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": "malformed-1",
                "model": "malformed",
                "choices": [{"message": {"role": "assistant", "content": '{"summary": "x"}'}}],
                "usage": usage,
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-key-not-real",
        default_model="malformed",
        transport=httpx.MockTransport(handler),
    )
    first = await _run(session, provider, _request(task_id="t_1"))

    assert first.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "estimated"
    assert usages[0].cost_amount == bound
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    assert reservations[0].usage_record_id == usages[0].id
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")

    # The same unverified usage cannot be replayed to get a second free call.
    second = await _run(session, provider, _request(task_id="t_2"))

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert calls == 1
    assert len(await _usages(session)) == 1
    assert len(await _reservations(session)) == 1


async def test_complete_adapter_usage_is_confirmed(session: AsyncSession) -> None:
    """Valid provider evidence still settles as confirmed actual spend."""

    pricing.register_tariff(
        Tariff(
            provider="openai_compat",
            model="valid",
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
        )
    )
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("5.00")})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "valid-1",
                "model": "valid",
                "choices": [{"message": {"role": "assistant", "content": '{"summary": "x"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-key-not-real",
        default_model="valid",
        transport=httpx.MockTransport(handler),
    )
    result = await _run(session, provider, _request())

    assert result.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "confirmed"
    assert usages[0].cost_amount == Decimal("0.10")
    assert (usages[0].prompt_tokens, usages[0].completion_tokens) == (10, 5)
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0.10")


async def test_reported_total_only_usage_is_defended_at_settlement(
    session: AsyncSession,
) -> None:
    """A provider bypassing adapter validation cannot settle a total-only $0."""

    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    bound = _bound()
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": bound})
    total_only = LlmUsage(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=100,
        reported=True,
    )

    first = await _run(
        session,
        MockProvider(texts=['{"summary": "x"}'], usage=total_only),
        _request(task_id="t_1"),
    )

    assert first.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "estimated"
    assert usages[0].cost_amount == bound
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"

    second_provider = MockProvider(texts=['{"summary": "y"}'], usage=total_only)
    second = await _run(session, second_provider, _request(task_id="t_2"))

    assert second.outcome == "refused" and second.error_code == "budget_exceeded"
    assert second_provider.calls == []


async def test_reported_all_zero_usage_remains_confirmed(
    session: AsyncSession,
) -> None:
    """A genuine all-zero report is valid evidence, not malformed usage."""

    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    bound = _bound()
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": bound})
    zero = LlmUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, reported=True)

    result = await _run(
        session,
        MockProvider(texts=['{"summary": "x"}'], usage=zero),
        _request(),
    )

    assert result.outcome == "completed"
    usages = await _usages(session)
    assert len(usages) == 1
    assert usages[0].usage_status == "confirmed"
    assert usages[0].cost_amount == Decimal("0")
    reservations = await _reservations(session)
    assert len(reservations) == 1 and reservations[0].status == "consumed"


async def test_unbudgeted_unbounded_executor_hold_is_best_effort(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])
    provider._default_model = "zero-budget-model"

    result = await _run(session, provider, _request(_settings(llm_max_tokens=None)))

    assert result.outcome == "completed"
    reservations = await _reservations(session)
    assert len(reservations) == 1
    assert reservations[0].amount == Decimal("0")
    assert reservations[0].mode == "best_effort"


async def test_budget_patch_between_snapshot_and_reserve_cannot_use_stale_zero_hold(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    factory = session_factory(test_engine)
    original_claim = executor_module._tasks.claim_task

    async def claim_after_budget_patch(*args, **kwargs):
        # Simulate a committed project PATCH landing after Phase 0's snapshot
        # but before the executor's locked reservation decision.
        async with factory() as patch_session:
            async with patch_session.begin():
                project = await ProjectRepository().get(patch_session, BIZ, "proj_1")
                assert project is not None
                project.ai_budget = Decimal("5.00")
                await patch_session.flush()
        return await original_claim(*args, **kwargs)

    monkeypatch.setattr(executor_module._tasks, "claim_task", claim_after_budget_patch)
    provider = MockProvider(texts=['{"summary": "x"}'])
    provider._default_model = "race-unpriced-model"

    result = await _run(session, provider, _request())

    assert result.outcome == "refused" and result.error_code == "budget_unpriced"
    assert provider.calls == []
    assert await make_task_status(session) == "not_started"
    assert await _reservations(session) == []


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
