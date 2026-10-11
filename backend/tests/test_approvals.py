"""Approval decisions: atomic approve/reject with audit (Stage 7.6).

Decisions flip a pending approval to a terminal state via ONE conditional
UPDATE plus the audit event in the SAME transaction. Repeats are idempotent
success; opposite decisions after terminal are 409; concurrent opposites
serialize to exactly one winner. The linked task is never touched here —
the executor re-reads the gate live (pinned by the gate tests below).
"""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import ActivityRepository
from tests.conftest import OPERATOR_EMAIL, login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_approval, make_world
from tests.factories import approvals as approvals_repo

_activity = ActivityRepository()


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Seed world + pending approval + owner operator, log in (cookie in jar)."""
    await make_world(session)
    await make_approval(session, approval_id="ap_1")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


async def _login_user(
    session: AsyncSession,
    api_client: httpx.AsyncClient,
    email: str,
    password: str,
    grant_biz: str | None,
) -> dict:
    """Create user (optionally without any membership), log in."""
    from sqlalchemy import select

    from app.core.passwords import hash_password
    from app.persistence.models.user import User
    from app.persistence.repositories.concrete import MembershipRepository

    uid = f"u_{email.split('@')[0]}"
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is None:
        session.add(
            User(id=uid, email=email, password_hash=hash_password(password), display_name=email)
        )
        await session.flush()
        if grant_biz is not None:
            await MembershipRepository().grant(session, uid, grant_biz, "owner")
        await session.commit()
    res = await api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf_token"]}


def _approve_url(biz: str = BIZ, approval_id: str = "ap_1") -> str:
    return f"/api/v1/businesses/{biz}/approvals/{approval_id}/approve"


def _reject_url(biz: str = BIZ, approval_id: str = "ap_1") -> str:
    return f"/api/v1/businesses/{biz}/approvals/{approval_id}/reject"


async def _approval_events(session: AsyncSession, biz: str):
    return [e for e in await _activity.list(session, biz) if e.kind == "approval"]


async def test_approve_pending(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_approve_url(), json={}, headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "approved"
    assert body["approvalId"] == "ap_1" and body["status"] == "approved"
    assert body["contract"] == "v1"

    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "approved"
    events = await _approval_events(session, BIZ)
    assert len(events) == 1
    assert events[0].project_id == "proj_1"
    assert events[0].agent_name == OPERATOR_EMAIL
    assert "ap_1" in events[0].text and "approved" in events[0].text


async def test_reject_pending(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_reject_url(), json={}, headers=headers)

    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "rejected"
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "rejected"
    events = await _approval_events(session, BIZ)
    assert len(events) == 1 and "rejected" in events[0].text


async def test_anonymous_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await make_approval(session, approval_id="ap_1")
    await session.commit()

    assert (await api_client.post(_approve_url(), json={})).status_code == 401
    assert (await api_client.post(_reject_url(), json={})).status_code == 401
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "pending"


async def test_csrf_403(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _authed(session, api_client)
    assert (await api_client.post(_approve_url(), json={})).status_code == 403
    bad = await api_client.post(_approve_url(), json={}, headers={"X-CSRF-Token": "nope"})
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "forbidden"
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "pending"


async def test_nonmember_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await make_approval(session, approval_id="ap_1")
    await session.commit()
    headers = await _login_user(session, api_client, "stranger@example.com", "pw-stranger-1", None)

    res = await api_client.post(_approve_url(), json={}, headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "pending"


async def test_foreign_approval_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    from tests.factories import make_agent, make_business, make_project, make_task

    await make_world(session, biz_id=BIZ)
    await make_approval(session, approval_id="ap_a")
    await make_business(session, biz_id=BIZ_OTHER)
    await make_agent(session, agent_id="ag_2", biz_id=BIZ_OTHER)
    await make_project(session, project_id="proj_2", biz_id=BIZ_OTHER)
    await make_task(session, task_id="t_b", biz_id=BIZ_OTHER, project_id="proj_2", owner_id="ag_2")
    await session.commit()
    headers_b = await _login_user(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)

    # B reaches into A's business: uniform 404, A's row untouched, no audit.
    res = await api_client.post(_approve_url(BIZ, "ap_a"), json={}, headers=headers_b)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    row = await approvals_repo.get(session, BIZ, "ap_a")
    assert row is not None and row.status == "pending"
    assert await _approval_events(session, BIZ) == []
    assert await _approval_events(session, BIZ_OTHER) == []


async def test_unknown_approval_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_approve_url(BIZ, "nope"), json={}, headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_repeat_same_decision_idempotent(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    first = await api_client.post(_approve_url(), json={}, headers=headers)
    second = await api_client.post(_approve_url(), json={}, headers=headers)

    assert first.status_code == 200 and first.json()["outcome"] == "approved"
    assert second.status_code == 200
    assert second.json()["outcome"] == "already_approved"
    assert second.json()["status"] == "approved"
    # One decision, one audit event — repeats record nothing new.
    assert len(await _approval_events(session, BIZ)) == 1


async def test_opposite_after_terminal_409(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.post(_approve_url(), json={}, headers=headers)).status_code == 200

    res = await api_client.post(_reject_url(), json={}, headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["outcome"] == "conflict"
    assert res.json()["status"] == "approved"
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "approved"
    assert len(await _approval_events(session, BIZ)) == 1

    # And the mirror direction on a rejected approval.
    await make_approval(session, approval_id="ap_2")
    await session.commit()
    assert (
        await api_client.post(_reject_url(BIZ, "ap_2"), json={}, headers=headers)
    ).status_code == 200
    res = await api_client.post(_approve_url(BIZ, "ap_2"), json={}, headers=headers)
    assert res.status_code == 409
    row = await approvals_repo.get(session, BIZ, "ap_2")
    assert row is not None and row.status == "rejected"
    assert len(await _approval_events(session, BIZ)) == 2


async def test_concurrent_opposite_decisions(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)

    async def decide(path: str):
        return await api_client.post(path, json={}, headers=headers)

    first, second = await asyncio.gather(decide(_approve_url()), decide(_reject_url()))
    codes = sorted(r.status_code for r in (first, second))
    assert codes == [200, 409], [r.status_code for r in (first, second)]

    # Exactly one terminal state, exactly one audit event — never both.
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status in ("approved", "rejected")
    assert len(await _approval_events(session, BIZ)) == 1


async def test_audit_failure_rolls_back(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    async def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr("app.persistence.repositories.concrete.ActivityRepository.append", boom)
    # Starlette's ServerErrorMiddleware re-raises after responding, so the
    # httpx transport surfaces the failure instead of a 500 response body.
    # The load-bearing assertion is the rollback below, not the envelope.
    with pytest.raises(RuntimeError, match="audit store down"):
        await api_client.post(_approve_url(), json={}, headers=headers)

    # The decision was rolled back with the failed audit write.
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "pending"
    assert await _approval_events(session, BIZ) == []


async def test_approve_unblocks_execution(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.llm.mock import MockProvider
    from tests.factories import make_task

    await make_world(session)
    await make_approval(session, approval_id="ap_g")
    await make_task(session, task_id="t_g", status="not_started", approval_gate_id="ap_g")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    headers = {"X-CSRF-Token": csrf}
    monkeypatch.setattr(
        "app.api.v1.executions.create_provider",
        lambda settings: MockProvider(texts=['{"summary": "x"}']),
    )
    exec_url = f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_g/executions"

    before = await api_client.post(exec_url, json={}, headers=headers)
    assert before.status_code == 409
    assert before.json()["error"]["code"] == "approval_required"

    res = await api_client.post(_approve_url(BIZ, "ap_g"), json={}, headers=headers)
    assert res.status_code == 200, res.text

    after = await api_client.post(exec_url, json={}, headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["outcome"] == "completed"


async def test_reject_keeps_gate_closed(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.llm.mock import MockProvider
    from tests.factories import make_task

    await make_world(session)
    await make_approval(session, approval_id="ap_g")
    await make_task(session, task_id="t_g", status="not_started", approval_gate_id="ap_g")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    headers = {"X-CSRF-Token": csrf}
    monkeypatch.setattr(
        "app.api.v1.executions.create_provider",
        lambda settings: MockProvider(texts=['{"summary": "x"}']),
    )
    exec_url = f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_g/executions"

    assert (
        await api_client.post(_reject_url(BIZ, "ap_g"), json={}, headers=headers)
    ).status_code == 200
    res = await api_client.post(exec_url, json={}, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "approval_required"


async def test_get_not_allowed(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    # No GET route exists for decisions (the app-wide catch-all answers 404
    # for method mismatches): nothing is decided, nothing is written.
    assert (await api_client.get(_approve_url(), headers=headers)).status_code == 404
    assert (await api_client.get(_reject_url(), headers=headers)).status_code == 404
    row = await approvals_repo.get(session, BIZ, "ap_1")
    assert row is not None and row.status == "pending"
    assert await _approval_events(session, BIZ) == []
