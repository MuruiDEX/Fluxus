"""Capability Engine integration tests — real Postgres, MockProvider (no network).

Scenarios are the ones a production reviewer cares about: authorization,
argument validation, real approval/task side effects, first-attempt write
policy, the bounded loop, aggregated usage, and failure accounting that never
hides a possibly-billed earlier turn.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import app.capabilities.engine as capability_engine
from app.capabilities import (
    CapabilityCall,
    CapabilityContext,
    CapabilityExecutor,
    CapabilityOwnershipLost,
    resolve_grants,
)
from app.capabilities.builtin import HANDLERS
from app.core.config import Settings
from app.execution.executor import ExecutionRequest, ExecutionResult, execute_task
from app.llm.mock import MockProvider
from app.llm.types import LlmRequest, LlmResponse, ProviderError
from app.persistence.engine import session_factory
from app.persistence.models.task import Task
from app.persistence.models.usage import BudgetReservation
from app.persistence.repositories.concrete import ActivityRepository
from app.persistence.repositories.usage import UsageRepository
from app.usage import pricing
from app.usage.tariffs import Tariff
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_agent, make_task, make_world
from tests.factories import agents as agents_repo
from tests.factories import approvals as approvals_repo
from tests.factories import brain as brain_repo
from tests.factories import projects as projects_repo
from tests.factories import tasks as tasks_repo

_activity = ActivityRepository()
_usage = UsageRepository()
MODEL = "mock"
MAX_OUT = 16


def _settings(**over: object) -> Settings:
    data: dict = {"llm_max_tokens": MAX_OUT}
    data.update(over)
    return Settings(**data)


def _req(task_id: str = "t_1", settings: Settings | None = None, **over) -> ExecutionRequest:
    data: dict = {
        "business_id": BIZ,
        "project_id": "proj_1",
        "task_id": task_id,
        "settings": settings or _settings(),
    }
    data.update(over)
    return ExecutionRequest(**data)


def _tariff() -> None:
    pricing.register_tariff(
        Tariff(
            provider="mock",
            model=MODEL,
            input_per_1k=Decimal("5.00"),
            output_per_1k=Decimal("10.00"),
        )
    )


async def _execute(session: AsyncSession, provider, req: ExecutionRequest) -> ExecutionResult:
    await session.commit()
    return await execute_task(session, provider, req)


async def _result_envelope(session: AsyncSession, project_id: str = "proj_1") -> dict:
    events = await _activity.list(session, BIZ, project_id=project_id)
    results = [event for event in events if event.kind == "result"]
    assert len(results) == 1
    return json.loads(results[0].text)


def _first_request(requests: list[str]) -> str:
    return json.dumps(
        {
            "summary": "Gathering context before drafting.",
            "tool_requests": requests,
        }
    )


async def _reservation_rows(session: AsyncSession) -> list[BudgetReservation]:
    rows = await session.execute(
        select(BudgetReservation)
        .where(BudgetReservation.business_id == BIZ)
        .order_by(BudgetReservation.id)
    )
    return list(rows.scalars().all())


async def _grant(
    session: AsyncSession,
    *,
    tools: list[str] | None = None,
    permissions: dict | None = None,
    agent_id: str = "ag_1",
) -> None:
    agent = await agents_repo.get(session, BIZ, agent_id)
    assert agent is not None
    agent.tools = tools or []
    agent.permissions = permissions or {}
    await session.flush()


def _final(summary: str = "Final draft ready.") -> str:
    return json.dumps({"summary": summary, "findings": ["one", "two"]})


async def test_read_capability_executes_audits_and_aggregates_usage(
    session: AsyncSession,
) -> None:
    _tariff()
    await make_world(session)
    await _grant(session, tools=["Planner"])
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "project.tasks",
                        "arguments": {"limit": 5},
                        "request_id": "r1",
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert result.llm_attempts == 2  # two provider calls, aggregated
    assert result.usage["total_tokens"] == 30  # MockProvider reports 15 per call
    assert len(provider.calls) == 2
    assert provider.calls[0].idempotency_key.endswith(":c1")
    assert provider.calls[1].idempotency_key.endswith(":c2")

    envelope = await _result_envelope(session)
    capabilities = envelope["capabilities"]
    assert capabilities["enabled"] is True
    assert capabilities["llmCalls"] == 2
    assert capabilities["limitReached"] is False
    assert capabilities["calls"] == [
        {
            "capability": "project.tasks",
            "status": "ok",
            "requestId": "r1",
        }
    ]
    # Aggregated usage (10+10 prompt, 5+5 completion) is priced as one confirmed
    # ledger row because every mock call reported usage.
    rows = await _usage.list_for_business(session, BIZ, task_id="t_1")
    assert len(rows) == 1
    assert rows[0].usage_status == "confirmed"
    assert rows[0].prompt_tokens == 20 and rows[0].completion_tokens == 10

    audit = [event.text for event in await _activity.list(session, BIZ) if event.kind == "system"]
    assert any("Capability project.tasks → ok" in text for text in audit)


async def test_ungranted_capability_is_denied_and_creates_nothing(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["Web research"])
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "approvals.request",
                        "arguments": {"title": "Spend 5000 USD"},
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []
    envelope = await _result_envelope(session)
    assert envelope["capabilities"]["calls"][0]["error"] == "not_authorized"


async def test_argument_validation_rejects_unknown_fields_without_side_effects(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["approvals"])
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "approvals.request",
                        "arguments": {
                            "title": "Approve campaign",
                            "status": "approved",  # protected / unknown field
                        },
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []
    envelope = await _result_envelope(session)
    assert envelope["capabilities"]["calls"][0]["error"] == "invalid_arguments"


async def test_approval_capability_creates_a_real_pending_human_gate(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["request approval"])
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "approvals.request",
                        "arguments": {
                            "title": "Publish the launch page",
                            "what": "Publish the page after review",
                            "expected_result": "Public landing page",
                            "cost": "0",
                        },
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    approvals = await approvals_repo.list_for_project(session, BIZ, "proj_1")
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.status == "pending"
    assert approval.requested_by_agent_id == "ag_1"
    assert approval.gates_task_id == "t_1"
    assert approval.title == "Publish the launch page"
    envelope = await _result_envelope(session)
    assert envelope["capabilities"]["calls"][0]["status"] == "ok"


async def test_delegation_creates_gated_child_task_with_dependency(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["delegate"])
    await make_agent(session, agent_id="ag_2", role="Designer", status="available")
    project = await projects_repo.get(session, BIZ, "proj_1")
    assert project is not None
    project.team_ids = ["ag_1", "ag_2"]
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "task.delegate",
                        "arguments": {
                            "title": "Design launch visuals",
                            "owner_agent_id": "ag_2",
                            "notes": "Three social variants.",
                            "eta_minutes": 120,
                        },
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    approvals = await approvals_repo.list_for_project(session, BIZ, "proj_1")
    assert len(approvals) == 1 and approvals[0].status == "pending"
    child_gate = approvals[0].gates_task_id
    assert child_gate is not None and child_gate != "t_1"
    child = await tasks_repo.get(session, BIZ, child_gate)
    assert child is not None
    assert child.owner_agent_id == "ag_2"
    assert child.status == "not_started"
    assert child.approval_gate_id == approvals[0].id
    deps = await tasks_repo.dependency_map(session, [child.id])
    assert deps[child.id] == ["t_1"]


async def test_write_capabilities_are_blocked_on_retries(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["approvals"])
    await make_task(session, status="failed", attempts=1)
    provider = MockProvider(
        texts=[
            _first_request([{"capability": "approvals.request", "arguments": {"title": "Again"}}]),
            _final(),
        ]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []
    envelope = await _result_envelope(session)
    assert envelope["capabilities"]["calls"][0]["error"] == "first_attempt_only"


async def test_loop_is_bounded_and_last_turn_requests_are_not_executed(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await _grant(session, tools=["Planner"])
    await make_task(session, status="not_started")
    request = _first_request([{"capability": "project.tasks", "arguments": {"limit": 1}}])
    provider = MockProvider(texts=[request, request, request])
    settings = _settings(execution_max_llm_calls=2)

    result = await _execute(session, provider, _req(settings=settings))

    assert result.outcome == "completed"
    assert len(provider.calls) == 2
    envelope = await _result_envelope(session)
    capabilities = envelope["capabilities"]
    assert capabilities["limitReached"] is True
    assert len(capabilities["calls"]) == 1  # only the first turn hands off work


async def test_failure_after_a_successful_turn_records_estimated_charge(
    session: AsyncSession,
) -> None:
    _tariff()
    await make_world(session)
    await _grant(session, tools=["Planner"])
    await make_task(session, status="not_started")

    class _FailsAfterFirst(MockProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            if self.calls:  # second provider call fails after a billed first one
                raise ProviderError("upstream boom")
            return await super().complete(request)

    provider = _FailsAfterFirst(
        texts=[_first_request([{"capability": "project.tasks", "arguments": {}}])]
    )

    result = await _execute(session, provider, _req())

    assert result.outcome == "failed"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "failed" and task.attempts == 1
    rows = await _usage.list_for_business(session, BIZ, task_id="t_1")
    assert len(rows) == 1
    assert rows[0].usage_status == "estimated"
    assert rows[0].prompt_tokens > 0 and rows[0].completion_tokens > 0
    reservations = await _reservation_rows(session)
    assert reservations and reservations[0].status in ("consumed", "ambiguous")


async def test_capability_mode_reserves_for_the_whole_bounded_loop(
    session: AsyncSession,
) -> None:
    _tariff()
    await make_world(session)
    await _grant(session, tools=["Planner"])
    await make_task(session, status="not_started")
    settings = _settings(execution_max_llm_calls=3)
    provider = MockProvider(texts=[_final()])

    result = await _execute(session, provider, _req(settings=settings))

    assert result.outcome == "completed"
    reservations = await _reservation_rows(session)
    assert len(reservations) == 1
    reservation = reservations[0]
    assert reservation.mode == "guaranteed"
    # The hold covers every permitted call, not just the one that ran.
    single = pricing.upper_bound_cost(
        provider="mock",
        model=MODEL,
        input_token_bound=1,
        max_output_tokens=MAX_OUT,
    )
    assert single is not None and single.cost is not None
    assert reservation.amount > single.cost


async def test_capabilities_endpoint_reports_resolved_grants_only(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await _grant(
        session,
        tools=["Planner", "Business Brain"],
        permissions={"write": ["Assign tasks"], "denied": ["Assign tasks"]},
    )
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)

    res = await api_client.get(
        f"/api/v1/businesses/{BIZ}/agents/ag_1/capabilities",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True
    assert body["maxLlmCalls"] == 4
    ids = {item["id"] for item in body["capabilities"]}
    assert ids == {"project.tasks", "knowledge.search"}
    assert all(item["risk"] == "read" for item in body["capabilities"])

    empty = await api_client.get(
        f"/api/v1/businesses/{BIZ}/agents/missing/capabilities",
        headers={"X-CSRF-Token": csrf},
    )
    assert empty.status_code == 404


async def test_read_handlers_are_project_scoped_and_bounded(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working", summary="Seed notes")
    await brain_repo.create(
        session,
        BIZ,
        {
            "id": "br_1",
            "category": "knowledge",
            "title": "Launch plan",
            "body": "Krakow launch notes",
        },
    )
    await session.commit()
    ctx = CapabilityContext(
        session=session,
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        task_title="T",
        agent_id="ag_1",
        agent_name="A",
        attempt=0,
        execution_generation=1,
        lease_owner="worker",
        settings=_settings(),
    )
    async with session.begin():
        context = await HANDLERS["business.context"](ctx, {})
        result = await HANDLERS["task.result"](ctx, {"task_id": "t_1"})
        knowledge = await HANDLERS["knowledge.search"](ctx, {"query": "launch", "limit": 2})

    assert context.ok is True
    assert context.data is not None
    assert context.data["business"]["name"] == "Test Business"
    assert context.data["project"]["title"] == "P"
    assert context.data["taskCounts"] == {"working": 1}
    assert result.ok is True and result.data is not None
    assert result.data["summary"] == "Seed notes"
    assert knowledge.ok is True and knowledge.data is not None
    assert [item["id"] for item in knowledge.data["results"]] == ["br_1"]


async def test_side_effect_requires_a_live_task_lease(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="working")  # owned by nobody: no live lease
    await session.commit()
    executor = CapabilityExecutor(resolve_grants(["approvals.request"], {}), _settings())
    ctx = CapabilityContext(
        session=session,
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        task_title="T",
        agent_id="ag_1",
        agent_name="A",
        attempt=0,
        execution_generation=1,
        lease_owner="stale-worker",
        settings=_settings(),
    )

    with pytest.raises(CapabilityOwnershipLost):
        await executor.execute(
            ctx,
            CapabilityCall(
                capability="approvals.request",
                arguments={"title": "Spend money"},
                request_id="r1",
            ),
        )

    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []


async def test_handler_timeout_is_audited_and_never_partially_commits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    await session.commit()

    async def _owned(*args: object, **kwargs: object) -> bool:
        return True

    async def _slow(ctx: CapabilityContext, arguments: dict) -> object:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    monkeypatch.setattr(capability_engine._tasks, "owned_now", _owned)
    monkeypatch.setitem(capability_engine.HANDLERS, "business.context", _slow)
    executor = CapabilityExecutor(resolve_grants(["business.context"], {}), _settings())
    ctx = CapabilityContext(
        session=session,
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        task_title="T",
        agent_id="ag_1",
        agent_name="A",
        attempt=0,
        execution_generation=1,
        lease_owner="worker",
        settings=_settings(execution_capability_timeout_s=0.05),
    )

    result = await executor.execute(
        ctx,
        CapabilityCall(capability="business.context", arguments={}, request_id="r1"),
    )

    assert result.ok is False and result.error_code == "timeout"
    events = await _activity.list(session, BIZ)
    assert any("Capability business.context → error:timeout" in event.text for event in events)


async def test_write_never_commits_after_the_lease_expires_mid_handler(
    session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership is re-proven at the commit boundary, not only before the handler.

    The lease is live when the call starts; another transaction then expires it
    while the handler runs (exactly what a recovery/takeover would do). The
    side effect must be rolled back: a capability write may never commit under
    ownership that is gone by commit time.
    """
    await make_world(session)
    task = await make_task(session, status="working")
    task.execution_generation = 1
    task.lease_owner = "worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=120)
    task.lease_deadline_at = datetime.now(UTC) + timedelta(seconds=240)
    await session.commit()

    real_request_approval = HANDLERS["approvals.request"]

    async def _expire_then_write(ctx: CapabilityContext, arguments: dict) -> object:
        # Separate transaction, committed before the handler returns: the lease
        # is provably expired at the commit boundary while the pre-check saw a
        # live one.
        factory = session_factory(test_engine)
        async with factory() as other:
            async with other.begin():
                target = await other.get(Task, "t_1")
                assert target is not None
                target.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return await real_request_approval(ctx, arguments)

    monkeypatch.setitem(HANDLERS, "approvals.request", _expire_then_write)
    executor = CapabilityExecutor(resolve_grants(["approvals"], {}), _settings())
    ctx = CapabilityContext(
        session=session,
        business_id=BIZ,
        project_id="proj_1",
        task_id="t_1",
        task_title="T",
        agent_id="ag_1",
        agent_name="A",
        attempt=0,
        execution_generation=1,
        lease_owner="worker",
        settings=_settings(),
    )

    with pytest.raises(CapabilityOwnershipLost):
        await executor.execute(
            ctx,
            CapabilityCall(
                capability="approvals.request",
                arguments={"title": "Spend money"},
                request_id="r1",
            ),
        )

    await session.rollback()
    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []


async def test_disabled_engine_keeps_the_legacy_single_call(session: AsyncSession) -> None:
    await make_world(session)
    await _grant(session, tools=["Planner"])
    await make_task(session, status="not_started")
    provider = MockProvider(
        texts=[
            _first_request(
                [
                    {
                        "capability": "approvals.request",
                        "arguments": {"title": "Spend money"},
                    }
                ]
            ),
            _final(),
        ]
    )

    result = await _execute(
        session,
        provider,
        _req(settings=_settings(execution_capabilities_enabled=False)),
    )

    assert result.outcome == "completed"
    assert len(provider.calls) == 1  # no second turn, no capability execution
    envelope = await _result_envelope(session)
    assert "capabilities" not in envelope
    assert await approvals_repo.list_for_project(session, BIZ, "proj_1") == []
