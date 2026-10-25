"""Stage 18 S1: Team Lead output caps are real, and the budget prices them.

Invariant under test: the output limit sent to the provider is exactly the
limit the budget reservation is priced from. An uncapped request has no
provable upper bound, so it must fail closed BEFORE the provider is contacted
instead of reserving an assumed amount (the Stage 16.1 `guaranteed` mode may
only describe a bound that is really enforced).

Covers: the four Team Lead request builders, both supported provider paths,
the reservation bound, fail-closed behaviour without a cap (meter level and
API level), and the guarantee that no production Team Lead call bypasses
metering.
"""

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.execution.executor import ExecutionRequest, execute_task
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.base import create_provider
from app.llm.mock import MockProvider
from app.llm.types import LlmMessage, LlmRequest
from app.main import create_app
from app.persistence.engine import session_factory
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.concrete import BusinessRepository, ProjectRepository
from app.persistence.repositories.usage import UsageRepository
from app.teamlead.finalize import generate_finalization
from app.teamlead.planning import generate_plan
from app.teamlead.questions import generate_questions
from app.teamlead.summary import generate_summary
from app.usage import pricing
from app.usage.metering import BudgetUnpricedError, TeamLeadMeter
from app.usage.tariffs import Tariff
from tests.factories import BIZ, make_task, make_world
from tests.test_briefs import (
    _STORED_QUESTIONS,
    ANSWERS,
    SUMMARY_TEXT,
    VALID_TEXT,
    _authed,
    _brief_url,
    _mocked,
)
from tests.test_finalize import _facts, _final_text, _final_url, _settle
from tests.test_planning import PLAN_TEXT, _plan_url

_usage = UsageRepository()

TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)

COMPAT_TARIFF = Tariff(
    provider="openai_compat",
    model="m",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)

_ROSTER = [{"id": "ag_1", "name": "A", "role": "Tester"}]


def _meter(factory, *, call_kind: str = "brief_finalize", project_id: str | None) -> TeamLeadMeter:
    return TeamLeadMeter(
        factory,
        Settings(),
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


def _app_client(test_engine: AsyncEngine, **over) -> httpx.AsyncClient:
    """An API client whose app runs with the given settings overrides."""
    settings = Settings(environment="test", **over)
    app = create_app(settings)
    app.state.session_factory = session_factory(test_engine)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


# ------------------------------------------------- request builders (S1.1) ---


async def test_all_teamlead_builders_send_the_configured_cap() -> None:
    """Every Team Lead request carries the configured output limit."""
    settings = Settings()
    cap = settings.team_lead_max_tokens
    assert cap is not None and cap > 0

    questions_provider = MockProvider(texts=[VALID_TEXT])
    await generate_questions(questions_provider, settings, "Northstar", "Fitness", "Grow fast")
    assert questions_provider.calls[0].max_tokens == cap

    summary_provider = MockProvider(texts=[SUMMARY_TEXT])
    await generate_summary(summary_provider, settings, "Grow fast", _STORED_QUESTIONS, ANSWERS)
    assert summary_provider.calls[0].max_tokens == cap

    plan_provider = MockProvider(texts=[PLAN_TEXT])
    await generate_plan(
        plan_provider,
        settings,
        "Grow fast",
        json.loads(SUMMARY_TEXT),
        _ROSTER,
    )
    assert plan_provider.calls[0].max_tokens == cap

    tasks = [{"id": "t_1", "title": "Design landing"}]
    finalize_provider = MockProvider(texts=[_final_text(tasks)])
    await generate_finalization(finalize_provider, settings, _facts(tasks))
    assert finalize_provider.calls[0].max_tokens == cap


# ------------------------------------------------ configuration (S1.2) ---


def test_config_allows_none_cap_but_rejects_non_positive_values() -> None:
    """`None` is the explicit fail-closed opt-out; 0/negative are invalid."""
    assert Settings().team_lead_max_tokens == 2048
    assert Settings(team_lead_max_tokens=None).team_lead_max_tokens is None
    with pytest.raises(ValidationError):
        Settings(team_lead_max_tokens=0)
    with pytest.raises(ValidationError):
        Settings(team_lead_max_tokens=-1)


def test_config_validates_executor_output_cap() -> None:
    assert Settings(llm_max_tokens=1024).llm_max_tokens == 1024
    assert Settings(llm_max_tokens=None).llm_max_tokens is None
    with pytest.raises(ValidationError):
        Settings(llm_max_tokens=0)
    with pytest.raises(ValidationError):
        Settings(llm_max_tokens=-1)


def test_config_selects_supported_output_limit_field() -> None:
    assert Settings().llm_max_tokens_param == "max_tokens"
    assert (
        Settings(llm_max_tokens_param="max_completion_tokens").llm_max_tokens_param
        == "max_completion_tokens"
    )
    with pytest.raises(ValidationError):
        Settings(llm_max_tokens_param="max_output_tokens")


# ------------------------------------------ provider compatibility (S1.1) ---


async def test_every_supported_provider_path_carries_the_cap() -> None:
    """Both supported provider configurations transmit the cap."""
    cap = Settings().team_lead_max_tokens
    assert cap is not None

    mock_provider = create_provider(Settings(environment="test", llm_provider="mock"))
    await mock_provider.complete(
        LlmRequest(messages=[LlmMessage(role="user", content="hi")], model="mock", max_tokens=cap)
    )
    assert mock_provider.calls[0].max_tokens == cap

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "model": "m",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    compat = OpenAICompatProvider(
        api_key="test-key-not-real",
        default_model="m",
        transport=httpx.MockTransport(handler),
    )
    await compat.complete(
        LlmRequest(messages=[LlmMessage(role="user", content="hi")], model="m", max_tokens=cap)
    )
    assert seen[0]["max_tokens"] == cap


async def test_task_execution_already_sends_the_same_output_parameter(
    session: AsyncSession,
) -> None:
    """The shipped execution path already requires `max_tokens` on the wire.

    Team Lead calls therefore introduce NO new provider requirement: both
    paths speak the same, already-deployed output-limit parameter.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "model": "m",
                "choices": [
                    {"message": {"role": "assistant", "content": '{"summary": "done"}'}}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-key-not-real",
        default_model="m",
        transport=httpx.MockTransport(handler),
    )
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()

    result = await execute_task(
        session,
        provider,
        ExecutionRequest(business_id=BIZ, project_id="proj_1", task_id="t_1"),
    )

    assert result.outcome == "completed", result.error_message
    assert seen[0]["max_tokens"] == Settings().llm_max_tokens


# --------------------------------------------------- reservation (S1.2) ---


async def test_reservation_upper_bound_uses_the_cap_sent_to_the_provider(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """The reserved amount is the bound of the limit that was really sent."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    provider = MockProvider(texts=["ok"])
    request = LlmRequest(
        messages=[LlmMessage(role="user", content="hello")], model="mock", max_tokens=64
    )
    await _meter(factory, project_id="proj_1").complete(provider, request, generation=0)

    assert provider.calls[0].max_tokens == 64
    usages, holds = await _rows(session)
    assert len(holds) == 1
    expected = pricing.upper_bound_cost(
        provider="mock",
        model="mock",
        input_token_bound=pricing.estimate_input_token_bound("hello", message_count=1),
        max_output_tokens=64,
    )
    assert expected is not None
    assert holds[0].amount == expected.cost
    assert holds[0].mode == "guaranteed"
    assert holds[0].status == "consumed"
    assert len(usages) == 1


async def test_reservation_matches_alternate_wire_field(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Reasoning-model field name changes the wire key, never the cap value."""
    pricing.register_tariff(COMPAT_TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "model": "m",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-key-not-real",
        default_model="m",
        max_tokens_param="max_completion_tokens",
        transport=httpx.MockTransport(handler),
    )
    request = LlmRequest(
        messages=[LlmMessage(role="user", content="hello")], model="m", max_tokens=64
    )
    await _meter(factory, project_id="proj_1").complete(provider, request, generation=0)

    assert seen[0]["max_completion_tokens"] == 64
    assert "max_tokens" not in seen[0]
    usages, holds = await _rows(session)
    assert len(holds) == 1 and len(usages) == 1
    expected = pricing.upper_bound_cost(
        provider="openai_compat",
        model="m",
        input_token_bound=pricing.estimate_input_token_bound("hello", message_count=1),
        max_output_tokens=64,
    )
    assert expected is not None
    assert holds[0].amount == expected.cost
    assert holds[0].mode == "guaranteed"


async def test_uncapped_budgeted_call_fails_closed_before_the_provider_is_called(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """No cap -> no provable bound -> refused before the provider is called."""
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    await session.commit()
    factory = session_factory(test_engine)

    provider = MockProvider(texts=["ok"])
    request = LlmRequest(
        messages=[LlmMessage(role="user", content="hello")], model="mock", max_tokens=None
    )

    with pytest.raises(BudgetUnpricedError):
        await _meter(factory, project_id="proj_1").complete(provider, request, generation=0)

    assert provider.calls == []
    usages, holds = await _rows(session)
    assert holds == []
    assert usages == []


# ------------------------------------------------------ API level (S1.2) ---


async def test_api_returns_budget_unpriced_when_the_cap_is_missing(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the cap disabled, a budgeted finalization is refused (409)."""
    pricing.register_tariff(TARIFF)
    async with _app_client(test_engine, team_lead_max_tokens=None) as client:
        headers = await _authed(session, client)

        from tests.test_planning import _make_plannable

        brief_id, _ = await _make_plannable(client, headers, monkeypatch)
        planned = await client.post(_plan_url(brief_id), headers=headers)
        assert planned.status_code == 200, planned.text
        body = planned.json()
        project_id = body["project"]["id"]
        await _settle(session, body["tasks"], body["approvals"])

        provider = MockProvider(texts=[_final_text(body["tasks"])])
        _mocked(monkeypatch, provider)
        res = await client.post(_final_url(brief_id), headers=headers)

    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "budget_unpriced"
    assert provider.calls == []
    rows = await _usage.list_for_business(session, BIZ, limit=200)
    assert all(row.call_kind != "brief_finalize" for row in rows)
    project = await ProjectRepository().get(session, BIZ, project_id)
    assert project is not None and project.ai_spent == Decimal("0")


async def test_every_production_team_lead_call_is_metered(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No production Team Lead call may bypass the metering layer."""
    pricing.register_tariff(TARIFF)
    cap = Settings().team_lead_max_tokens
    headers = await _authed(session, api_client)

    questions_provider = MockProvider(texts=[VALID_TEXT])
    _mocked(monkeypatch, questions_provider)
    created = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)
    assert created.status_code == 200, created.text
    brief_id = created.json()["id"]

    summary_provider = MockProvider(texts=[SUMMARY_TEXT])
    _mocked(monkeypatch, summary_provider)
    answered = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert answered.status_code == 200, answered.text

    plan_provider = MockProvider(texts=[PLAN_TEXT])
    _mocked(monkeypatch, plan_provider)
    planned = await api_client.post(_plan_url(brief_id), headers=headers)
    assert planned.status_code == 200, planned.text
    tasks = planned.json()["tasks"]
    await _settle(session, tasks, planned.json()["approvals"])

    finalize_provider = MockProvider(texts=[_final_text(tasks)])
    _mocked(monkeypatch, finalize_provider)
    finalized = await api_client.post(_final_url(brief_id), headers=headers)
    assert finalized.status_code == 200, finalized.text

    # Every call reached the provider carrying the cap ...
    for provider in (questions_provider, summary_provider, plan_provider, finalize_provider):
        assert provider.calls, "a Team Lead call never reached the provider"
        assert provider.calls[0].max_tokens == cap

    # ... and every call produced exactly one metered ledger row.
    rows = await _usage.list_for_business(session, BIZ, limit=200)
    kinds = sorted(row.call_kind for row in rows)
    assert kinds == ["brief_finalize", "brief_questions", "brief_summary", "plan_generation"]


async def test_api_business_budget_meters_business_scoped_call(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured business budget reserves against business-scoped calls."""
    pricing.register_tariff(TARIFF)
    headers = await _authed(session, api_client)
    await BusinessRepository().update(session, BIZ, BIZ, {"ai_budget": Decimal("100.00")})
    await session.commit()
    provider = MockProvider(texts=[VALID_TEXT])
    _mocked(monkeypatch, provider)

    created = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert created.status_code == 200, created.text
    assert len(provider.calls) == 1
    usages, holds = await _rows(session)
    assert len(usages) == 1 and len(holds) == 1
    assert holds[0].project_id is None and holds[0].task_id is None
    assert holds[0].status == "consumed" and holds[0].mode == "guaranteed"
    assert usages[0].project_id is None and usages[0].call_kind == "brief_questions"
