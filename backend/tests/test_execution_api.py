"""Execution + task API tests (TestClient, MockProvider вЂ” no network)."""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.executor import ExecutionRequest, execute_task
from app.llm.mock import MockProvider
from app.persistence.engine import session_factory
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_task, make_world, spend_of_project
from tests.factories import tasks as tasks_repo


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Seed world + owner operator, log in, return CSRF headers (cookie in jar)."""
    await _seed(session)
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


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
    provider = MockProvider(texts=['{"summary": "done", "findings": ["a", "b"]}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    headers = await _authed(session, api_client)
    res = await api_client.post(_url(), json={}, headers=headers)

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
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    headers = await _authed(session, api_client)
    res = await api_client.post(_url(), json={"timeout_s": 99999}, headers=headers)

    assert res.status_code == 200, res.text
    assert provider.calls[0].timeout_s == 300.0  # server max wins


async def test_execute_failed_outcome_is_200_with_code(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.llm.types import ProviderError

    provider = MockProvider(errors=[ProviderError("boom")])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    headers = await _authed(session, api_client)
    res = await api_client.post(_url(), json={}, headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "failed" and body["error"]["code"] == "provider_error"


async def test_execute_idempotent_repeat(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    headers = await _authed(session, api_client)
    first = await api_client.post(_url(), json={}, headers=headers)
    second = await api_client.post(_url(), json={}, headers=headers)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["outcome"] == "already_completed"
    assert len(provider.calls) == 1  # no second LLM invocation


async def test_execute_concurrent_double_post(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    provider = MockProvider(texts=['{"summary": "a"}', '{"summary": "b"}'], latency_s=0.5)
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    import asyncio

    headers = await _authed(session, api_client)

    async def post():
        return await api_client.post(_url(), json={}, headers=headers)

    results = list(await asyncio.gather(post(), post()))
    codes = sorted(r.status_code for r in results)
    outcomes = sorted(r.json()["outcome"] for r in results)
    assert codes == [200, 409], [r.text for r in results]
    assert outcomes == ["already_running", "completed"]
    assert len(provider.calls) == 1  # exactly one LLM invocation total


async def test_execute_unknown_task_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_url("nope"), json={}, headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_read_task(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(
        f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1", headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["businessId"] == BIZ and body["projectId"] == "proj_1"
    assert body["contract"] == "v1"
    assert "created_at" not in body and "updated_at" not in body


async def test_read_task_wrong_scope_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(
        "/api/v1/businesses/other-biz/projects/proj_1/tasks/t_1", headers=headers
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_unauthenticated_execution_401(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed(session)
    res = await api_client.post(_url(), json={})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    # Health stays open by design.
    assert (await api_client.get("/api/v1/health")).status_code == 200


async def test_forged_cookie_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed(session)
    api_client.cookies.set("bos_session", "forged-session-id")
    res = await api_client.post(_url(), json={})
    assert res.status_code == 401


async def test_execute_requires_csrf_on_top_of_session(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    """The session cookie alone never authorizes a mutation (Stage 6.1).

    Regression: require_business used to skip the CSRF check, so any
    authenticated cookie without a token could drive executions.
    """
    from app.llm.mock import MockProvider

    headers = await _authed(session, api_client)
    provider = MockProvider(texts=['{"summary": "done"}'])
    monkeypatch.setattr("app.api.v1.executions.create_provider", lambda settings: provider)

    missing = await api_client.post(_url(), json={})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "forbidden"
    wrong = await api_client.post(_url(), json={}, headers={"X-CSRF-Token": "wrong"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "forbidden"
    assert provider.calls == []  # rejected before any claim or LLM call

    ok = await api_client.post(_url(), json={}, headers=headers)
    assert ok.status_code == 200, ok.text


async def test_read_task_needs_no_csrf(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Safe methods stay CSRF-free: the check gates mutations only."""
    await _authed(session, api_client)
    res = await api_client.get(f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1")
    assert res.status_code == 200, res.text


async def test_cross_user_cookie_cannot_reach_foreign_business(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """User B authenticated, business of user A: uniform 404, no leakage."""
    from app.core.passwords import hash_password
    from app.persistence.models.user import User
    from app.persistence.repositories.concrete import MembershipRepository

    await make_world(session, biz_id="biz_b")
    await session.commit()
    session.add(
        User(
            id="u_b",
            email="b@example.com",
            password_hash=hash_password("pw-b-pw-b"),
            display_name="B",
        )
    )
    await session.flush()
    await MembershipRepository().grant(session, "u_b", "biz_b", "owner")
    await session.commit()

    res = await api_client.post(
        "/api/v1/auth/login", json={"email": "b@example.com", "password": "pw-b-pw-b"}
    )
    assert res.status_code == 200, res.text
    csrf = res.json()["csrf_token"]
    # B's session asking for A's business → 404, indistinguishable from missing.
    res = await api_client.post(
        f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1/executions",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


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
