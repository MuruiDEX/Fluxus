"""Execution + task API tests (TestClient, MockProvider вЂ” no network)."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.execution.executor import ExecutionRequest, execute_task
from app.llm.mock import MockProvider
from app.persistence.engine import session_factory
from tests.conftest import OPERATOR_TOKEN, auth_headers
from tests.factories import BIZ, make_task, make_world, spend_of_project
from tests.factories import tasks as tasks_repo


def _settings(**over) -> Settings:
    data: dict = {
        "environment": "test",
        "operator_token": OPERATOR_TOKEN,
        "database_url": "unused://test-wired-below",
    }
    data.update(over)
    return Settings(**data)


def _auth() -> dict:
    return auth_headers()


async def _seed(session: AsyncSession, task_status: str = "not_started") -> None:
    await make_world(session)
    await make_task(session, status=task_status)
    await session.commit()


def _url(task_id: str = "t_1") -> str:
    return f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/{task_id}/executions"


def _req(task_id: str = "t_1") -> ExecutionRequest:
    return ExecutionRequest(business_id=BIZ, project_id="proj_1", task_id=task_id)


async def test_execute_success(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    await _seed(session)
    provider = MockProvider(texts=['{"summary": "done", "findings": ["a", "b"]}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    res = await api_client.post(_url(), json={}, headers=_auth())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "completed"
    assert body["taskId"] == "t_1" and body["status"] == "completed"
    assert body["contract"] == "v1"
    assert body["error"] is None
    assert len(provider.calls) == 1
    assert provider.calls[0].timeout_s == 30.0  # default, under the server cap


async def test_execute_clamps_timeout(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    await _seed(session)
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    res = await api_client.post(_url(), json={"timeout_s": 99999}, headers=_auth())

    assert res.status_code == 200, res.text
    assert provider.calls[0].timeout_s == 300.0  # server max wins


async def test_execute_failed_outcome_is_200_with_code(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.llm.types import ProviderError

    await _seed(session)
    provider = MockProvider(errors=[ProviderError("boom")])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    res = await api_client.post(_url(), json={}, headers=_auth())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "failed" and body["error"]["code"] == "provider_error"


async def test_execute_idempotent_repeat(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    await _seed(session)
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    first = await api_client.post(_url(), json={}, headers=_auth())
    second = await api_client.post(_url(), json={}, headers=_auth())

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["outcome"] == "already_completed"
    assert len(provider.calls) == 1  # no second LLM invocation


async def test_execute_concurrent_double_post(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    await _seed(session)
    provider = MockProvider(texts=['{"summary": "a"}', '{"summary": "b"}'], latency_s=0.5)
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    import asyncio

    async def post():
        return await api_client.post(_url(), json={}, headers=_auth())

    results = list(await asyncio.gather(post(), post()))
    codes = sorted(r.status_code for r in results)
    outcomes = sorted(r.json()["outcome"] for r in results)
    assert codes == [200, 409], [r.text for r in results]
    assert outcomes == ["already_running", "completed"]
    assert len(provider.calls) == 1  # exactly one LLM invocation total


async def test_execute_unknown_task_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed(session)
    res = await api_client.post(_url("nope"), json={}, headers=_auth())
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_read_task(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed(session)
    res = await api_client.get(
        f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1", headers=_auth()
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["businessId"] == BIZ and body["projectId"] == "proj_1"
    assert body["contract"] == "v1"
    assert "created_at" not in body and "updated_at" not in body


async def test_read_task_wrong_scope_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed(session)
    res = await api_client.get(
        "/api/v1/businesses/other-biz/projects/proj_1/tasks/t_1", headers=_auth()
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_no_token_configured_closes_endpoints(session: AsyncSession, test_engine) -> None:
    from app.main import create_app as _create

    app = _create(_settings(operator_token=None))
    app.state.session_factory = session_factory(test_engine)
    closed = TestClient(app, raise_server_exceptions=False)
    assert closed.post(_url(), json={}, headers=_auth()).status_code == 401
    assert (
        closed.get(
            f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1", headers=_auth()
        ).status_code
        == 401
    )
    # Health stays open by design.
    assert closed.get("/api/v1/health").status_code == 200


async def test_wrong_token_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_url(), json={}, headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


async def test_missing_token_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_url(), json={})
    assert res.status_code == 401


async def test_cancel_in_commit_window_never_half_writes(
    session: AsyncSession, test_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel delivered inside a commit: end state must be fully consistent.

    Either the commit landed (completed + event + spend) or it did not
    (claim intact or compensated). Partial writes are impossible by
    construction; this test proves it by re-reading from a fresh session.
    """
    from sqlalchemy.orm.session import SessionTransaction

    await make_world(session)
    await make_task(session, status="not_started")
    await session.commit()
    provider = MockProvider(texts=['{"summary": "x"}'])

    real_commit = SessionTransaction.commit
    calls = 0
    me = asyncio.current_task()

    def cancel_on_second_commit(self) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:  # persist-txn commit: cancel mid-flight
            assert me is not None
            me.cancel()
        return real_commit(self)

    monkeypatch.setattr(SessionTransaction, "commit", cancel_on_second_commit)
    with pytest.raises(asyncio.CancelledError):
        await execute_task(session, provider, _req())

    from app.persistence.engine import session_factory

    async with session_factory(test_engine)() as fresh:
        task = await tasks_repo.get(fresh, BIZ, "t_1")
        assert task is not None
        from app.persistence.repositories.concrete import ActivityRepository

        events = await ActivityRepository().list(fresh, BIZ, project_id="proj_1")
        kinds = [e.text for e in events if e.kind == "result"]
        project = await spend_of_project(fresh, BIZ, "proj_1")
        if task.status == "completed":
            # Commit landed before cancellation: full result present.
            assert task.summary == "x" and len(kinds) == 1
        else:
            # Commit did not land: claim intact (retryable) or compensated.
            assert task.status in ("working", "not_started")
            assert len(kinds) == 0
        # Invariant holds in every branch.
        from sqlalchemy import func, select

        from app.persistence.models.task import Task

        total = (
            await fresh.execute(
                select(func.coalesce(func.sum(Task.cost_accrued), 0)).where(
                    Task.business_id == BIZ, Task.project_id == "proj_1"
                )
            )
        ).scalar_one()
        assert project == total
