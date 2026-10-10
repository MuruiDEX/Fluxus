"""Executor integration tests вЂ” real Postgres, MockProvider (no network)."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.execution.executor import (
    ExecutionRequest,
    ExecutionResult,
    execute_task,
)
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.base import LlmProvider
from app.llm.mock import MockProvider
from app.persistence.engine import session_factory
from app.persistence.models.task import TaskDependency
from app.persistence.repositories.concrete import (
    ActivityRepository,
    ProjectRepository,
)
from app.persistence.repositories.spend import recalculate_project_spend
from tests.factories import (
    BIZ,
    BIZ_OTHER,
    make_approval,
    make_task,
    make_world,
    spend_of_project,
)
from tests.factories import (
    approvals as approvals_repo,
)
from tests.factories import (
    tasks as tasks_repo,
)


def _req(task_id: str = "t_1", **over) -> ExecutionRequest:
    data: dict = {"business_id": BIZ, "project_id": "proj_1", "task_id": task_id}
    data.update(over)
    return ExecutionRequest(**data)


async def _execute(
    session: AsyncSession, provider: LlmProvider, req: ExecutionRequest
) -> ExecutionResult:
    # Seal setup writes: execute_task requires a session without an open
    # transaction (its claim must be a top-level commit, never a savepoint).
    # Isolation is preserved вЂ” the fixture truncates all tables per test.
    await session.commit()
    return await execute_task(session, provider, req)


async def _result_event(session: AsyncSession):
    events = await ActivityRepository().list(session, BIZ, project_id="proj_1")
    results = [e for e in events if e.kind == "result"]
    assert len(results) == 1
    return json.loads(results[0].text)


async def test_success_end_to_end(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started", progress=0)
    provider = MockProvider(texts=['{"summary": "Researched 12 rivals.", "findings": ["a", "b"]}'])

    result = await _execute(session, provider, _req())

    assert result.outcome == "completed"
    assert result.error_code is None
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "completed" and task.progress == 100
    assert task.summary == "Researched 12 rivals."
    assert task.attempts == 0  # success consumes no attempt
    envelope = await _result_event(session)
    assert envelope["type"] == "task_llm_result" and envelope["task_id"] == "t_1"
    assert envelope["output"] == {"summary": "Researched 12 rivals.", "findings": ["a", "b"]}
    assert envelope["usage"]["total_tokens"] == 15
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0")


async def test_invalid_structured_output_fails_without_persisting_result(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=["not json at all {{{"])

    result = await _execute(session, provider, _req())

    assert result.outcome == "failed" and result.error_code == "bad_response"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.status == "failed" and task.attempts == 1


async def test_provider_error_and_timeout(session: AsyncSession) -> None:
    from app.llm.types import ProviderError, ProviderTimeoutError

    await make_world(session)
    await make_task(session, task_id="t_e", status="not_started")
    await make_task(session, task_id="t_t", status="not_started")

    err_provider = MockProvider(errors=[ProviderError("boom")])
    r1 = await _execute(session, err_provider, _req("t_e"))
    assert r1.outcome == "failed" and r1.error_code == "provider_error"

    err_timeout = MockProvider(errors=[ProviderTimeoutError("slow")])
    r2 = await _execute(session, err_timeout, _req("t_t"))
    assert r2.outcome == "failed" and r2.error_code == "provider_timeout"
    assert (await tasks_repo.get(session, BIZ, "t_t")).attempts == 1


async def test_cancellation_reverts_claim_and_propagates(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(latency_s=30.0)

    await session.commit()
    task = asyncio.ensure_future(execute_task(session, provider, _req()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    current = await tasks_repo.get(session, BIZ, "t_1")
    assert current is not None and current.status == "not_started"
    assert current.attempts == 0  # cancelled run consumed nothing
    assert provider.calls  # the LLM call did start (proves mid-flight cancel)


async def test_missing_and_cross_business_context(session: AsyncSession) -> None:
    await make_world(session)
    provider = MockProvider()

    missing = await _execute(session, provider, _req("nope"))
    assert missing.outcome == "refused" and missing.error_code == "not_found"

    await make_task(session, task_id="t_x", status="not_started")
    foreign = await _execute(session, provider, _req("t_x", business_id=BIZ_OTHER))
    assert foreign.outcome == "refused" and foreign.error_code == "not_found"
    assert provider.calls == []  # no LLM call without valid scoped context


async def test_rerun_idempotency_and_concurrency(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "done"}'])

    first = await _execute(session, provider, _req())
    assert first.outcome == "completed"
    second = await _execute(session, provider, _req())
    assert second.outcome == "already_completed"
    assert len(provider.calls) == 1

    await make_task(session, task_id="t_c", status="not_started")
    slow_a = MockProvider(texts=['{"summary": "a"}'], latency_s=0.5)
    slow_b = MockProvider(texts=['{"summary": "b"}'], latency_s=0.5)
    # Commit setup so concurrent sessions observe the same committed state.
    # (Isolation is preserved: the fixture truncates all tables before each test.)
    await session.commit()

    # Separate sessions for true concurrency (one session cannot interleave).
    async def run_fresh(engine: AsyncEngine, provider: MockProvider) -> ExecutionResult:
        async with session_factory(engine)() as s2:
            return await execute_task(s2, provider, _req("t_c"))

    results = list(
        await asyncio.gather(run_fresh(test_engine, slow_a), run_fresh(test_engine, slow_b))
    )
    outcomes = sorted(r.outcome for r in results)
    assert outcomes == ["already_running", "completed"]
    assert len(slow_a.calls) + len(slow_b.calls) == 1


async def test_attempt_budget_and_final_refusal(session: AsyncSession) -> None:
    from app.llm.types import ProviderError

    await make_world(session)
    await make_task(session, status="not_started")
    for i in range(3):
        provider = MockProvider(errors=[ProviderError(f"boom-{i}")])
        result = await _execute(session, provider, _req())
        assert result.outcome == "failed"
    task = await tasks_repo.get(session, BIZ, "t_1")
    assert task is not None and task.attempts == 3

    final = await _execute(session, MockProvider(texts=['{"summary": "x"}']), _req())
    assert final.outcome == "refused" and "exhausted" in (final.error_message or "")


async def test_precheck_gate_budget_and_deps_consume_nothing(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, task_id="t_g", status="not_started")
    await make_approval(session, approval_id="ap_g")

    await approvals_repo.update(session, BIZ, "ap_g", {"gates_task_id": "t_g"})
    await tasks_repo.update(session, BIZ, "t_g", {"approval_gate_id": "ap_g"})
    provider = MockProvider(texts=['{"summary": "x"}'])

    gated = await _execute(session, provider, _req("t_g"))
    assert gated.outcome == "refused" and gated.error_code == "approval_required"
    current = await tasks_repo.get(session, BIZ, "t_g")
    assert current is not None and current.status == "not_started" and current.attempts == 0
    assert provider.calls == []

    await approvals_repo.update(session, BIZ, "ap_g", {"status": "approved"})
    # Dependency on an incomplete task blocks too.
    await make_task(session, task_id="t_dep", status="working")
    session.add(TaskDependency(task_id="t_g", depends_on_id="t_dep"))
    await session.flush()
    blocked = await _execute(session, provider, _req("t_g"))
    assert blocked.outcome == "refused" and blocked.error_code == "conflict"

    # Exhausted budget blocks (spend reaches budget via the derived path only).
    await tasks_repo.update(session, BIZ, "t_dep", {"status": "completed", "progress": 100})
    await make_task(session, task_id="t_cost", status="completed", cost_accrued=Decimal("10"))
    await recalculate_project_spend(session, BIZ, "proj_1")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": 5})
    over = await _execute(session, provider, _req("t_g"))
    assert over.outcome == "refused" and over.error_code == "budget_exceeded"
    # Zero budget means "no limit".
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": 0})
    ok = await _execute(session, provider, _req("t_g"))
    assert ok.outcome == "completed"


async def test_persist_failure_compensates_or_reports_unknown(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])

    async def boom(*args, **kwargs):
        raise RuntimeError("ledger down")

    # Patch where it is looked up (executor module), not where defined.
    monkeypatch.setattr("app.execution.executor.recalculate_project_spend", boom)
    with pytest.raises(RuntimeError, match="ledger down"):
        await _execute(session, provider, _req())
    # Persist txn rolled back AND claim compensated: task retryable, spend clean.
    current = await tasks_repo.get(session, BIZ, "t_1")
    assert current is not None and current.status == "not_started"
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0")


async def test_no_double_count_across_runs(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, task_id="t_1", status="not_started")
    await make_task(session, task_id="t_2", status="not_started")
    provider = MockProvider(texts=['{"summary": "one"}', '{"summary": "two"}'])
    # Two sequential runs share one scripted provider; both must complete.
    assert (await _execute(session, provider, _req("t_1"))).outcome == "completed"
    assert (await _execute(session, provider, _req("t_2"))).outcome == "completed"
    assert await spend_of_project(session, BIZ, "proj_1") == Decimal("0")
    for task_id in ("t_1", "t_2"):
        task = await tasks_repo.get(session, BIZ, task_id)
        assert task is not None and task.cost_accrued == Decimal("0")


async def test_no_secret_leak_in_persisted_artifacts(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    canary = "sk-CANARY-9f8e7d6c5b4a"
    await make_world(session)
    await make_task(session, status="not_started")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = OpenAICompatProvider(api_key=canary, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        result = await _execute(session, provider, _req())
    assert result.outcome == "failed" and result.error_code == "provider_auth_error"

    events = await ActivityRepository().list(session, BIZ)
    blob = " ".join([e.text for e in events] + [r.getMessage() for r in caplog.records])
    assert canary not in blob


async def test_unknown_persists_row_unchanged(
    session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compensation_failed must leave the claimed row exactly as it was."""
    await make_world(session)
    await make_task(session, status="not_started")
    provider = MockProvider(texts=['{"summary": "x"}'])

    async def boom_update(*args, **kwargs):
        raise RuntimeError("store down")

    # Persist txn fails at the task update; compensation then fails the same
    # way, so the executor must report unknown without writing anything.
    monkeypatch.setattr("app.persistence.repositories.base.BaseRepository.update", boom_update)
    result = await _execute(session, provider, _req())
    assert result.outcome == "unknown"
    assert result.error_code == "compensation_failed"

    async with session_factory(test_engine)() as fresh:
        task = await tasks_repo.get(fresh, BIZ, "t_1")
        assert task is not None
        assert task.status == "working" and task.attempts == 0
        events = await ActivityRepository().list(fresh, BIZ, project_id="proj_1")
        assert not [e for e in events if e.kind == "result"]
        assert await spend_of_project(fresh, BIZ, "proj_1") == Decimal("0")


async def test_spend_invariant_after_cancel(
    session: AsyncSession, test_engine: AsyncEngine
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    provider = MockProvider(latency_s=30.0)

    task = asyncio.ensure_future(execute_task(session, provider, _req()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with session_factory(test_engine)() as fresh:
        current = await tasks_repo.get(fresh, BIZ, "t_1")
        assert current is not None and current.status == "not_started"
        assert await spend_of_project(fresh, BIZ, "proj_1") == Decimal("0")
