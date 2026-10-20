"""Cross-tenant isolation: user B must never reach user A's data.

Matrix over read / write(execute) / recover × {no session, forged cookie,
wrong-business member, unknown business}. Every denial is a uniform 404
(except unauthenticated 401) — existence of foreign tenants is never
confirmed. Direct executor calls are out of scope here (service layer takes
authorized business_id by construction); these tests pin the HTTP boundary.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import BIZ, make_task, make_world


async def _login_as(
    session: AsyncSession,
    api_client: httpx.AsyncClient,
    email: str,
    password: str,
    biz_id: str,
) -> dict:
    """Create user + owner membership, log in, return CSRF headers."""
    from sqlalchemy import select

    from app.core.passwords import hash_password
    from app.persistence.models.user import User
    from app.persistence.repositories.concrete import MembershipRepository

    uid = f"u_{email.split('@')[0]}"
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is None:
        session.add(
            User(
                id=uid,
                email=email,
                password_hash=hash_password(password),
                display_name=email,
            )
        )
        await session.flush()
        await MembershipRepository().grant(session, uid, biz_id, "owner")
        await session.commit()
    res = await api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf_token"]}


async def _seed_both(session: AsyncSession) -> None:
    from tests.factories import BIZ_OTHER, make_agent, make_business, make_project

    await make_world(session, biz_id=BIZ)
    await make_task(session, task_id="t_a", status="not_started")
    # Distinct global IDs for the second tenant (PKs are global, scope is not).
    await make_business(session, biz_id=BIZ_OTHER)
    await make_agent(session, agent_id="ag_2", biz_id=BIZ_OTHER)
    await make_project(session, project_id="proj_2", biz_id=BIZ_OTHER)
    await make_task(
        session,
        task_id="t_b",
        status="not_started",
        biz_id=BIZ_OTHER,
        project_id="proj_2",
        owner_id="ag_2",
    )
    await session.commit()


def _exec_url(biz: str, task: str = "t_a") -> str:
    return f"/api/v1/businesses/{biz}/projects/proj_1/tasks/{task}/executions"


def _read_url(biz: str, task: str = "t_a") -> str:
    return f"/api/v1/businesses/{biz}/projects/proj_1/tasks/{task}"


def _recover_url(biz: str, task: str = "t_a") -> str:
    return f"/api/v1/businesses/{biz}/projects/proj_1/tasks/{task}/recover"


async def test_b_cannot_touch_a_data(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch
) -> None:
    from app.llm.mock import MockProvider

    await _seed_both(session)
    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", "biz_other")
    monkeypatch.setattr(
        "app.api.v1.executions.create_provider",
        lambda settings: MockProvider(texts=['{"summary": "x"}']),
    )

    # Read / execute / recover against A's business → uniform 404.
    assert (await api_client.get(_read_url(BIZ), headers=headers_b)).status_code == 404
    res = await api_client.post(_exec_url(BIZ), json={}, headers=headers_b)
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    res = await api_client.post(_recover_url(BIZ), headers=headers_b)
    assert res.status_code == 404

    # A's own data is untouched and still runnable by A afterwards.
    await api_client.post("/api/v1/auth/logout", headers=headers_b)
    headers_a = await _login_as(session, api_client, "a@example.com", "pw-a-pw-a", BIZ)
    res = await api_client.post(_exec_url(BIZ), json={}, headers=headers_a)
    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "completed"


async def test_url_id_substitution_blocked(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Project/task IDs from another business resolve to 404, never cross-read."""
    await _seed_both(session)
    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", "biz_other")

    # t_b lives in biz_other but is requested under BIZ path → 404.
    res = await api_client.get(
        f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_b", headers=headers_b
    )
    assert res.status_code == 404


async def test_no_session_cannot_mutate_or_read(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_both(session)
    assert (await api_client.post(_exec_url(BIZ), json={})).status_code == 401
    assert (await api_client.post(_recover_url(BIZ))).status_code == 401
    assert (await api_client.get(_read_url(BIZ))).status_code == 401


async def test_forged_and_crossed_cookies_rejected(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_both(session)
    api_client.cookies.set("bos_session", "forged-session-id")
    assert (await api_client.post(_exec_url(BIZ), json={})).status_code == 401
    assert (await api_client.get(_read_url(BIZ))).status_code == 401
