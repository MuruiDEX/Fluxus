"""Read-only domain API (Stage 7.0): lists + single gets.

Covers: owner reads own data, anonymous 401, foreign scope 404 (uniform,
no leakage), empty lists, pagination bounds, stable ordering, exact
response shapes (no internal columns, no secrets), envelope keys.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.concrete import ActivityRepository, BrainRepository
from tests.conftest import login_as, seed_operator
from tests.factories import (
    BIZ,
    BIZ_OTHER,
    make_agent,
    make_approval,
    make_business,
    make_project,
    make_task,
    make_world,
)

ACTIVITY = ActivityRepository()
BRAIN = BrainRepository()


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    """Seed operator + log in; session cookie stays in the client jar."""
    await seed_operator(session)
    await login_as(api_client)


async def _seed_reads(session: AsyncSession) -> None:
    """One business with two projects, three tasks, two agents, one approval."""
    await make_world(session)
    await make_project(session, project_id="proj_2", status="planning")
    await make_agent(session, agent_id="ag_2", status="available")
    await make_task(session, task_id="t_b", status="not_started")
    await make_task(session, task_id="t_a", status="not_started")
    await make_task(
        session, task_id="t_c", status="not_started", project_id="proj_2", owner_id="ag_2"
    )
    await make_approval(session, approval_id="ap_1")
    await BRAIN.create(
        session, BIZ, {"id": "br_1", "category": "goals", "title": "G1", "body": "B1"}
    )
    await BRAIN.create(
        session, BIZ, {"id": "br_2", "category": "results", "title": "G2", "body": "B2"}
    )
    await ACTIVITY.append(
        session,
        BIZ,
        {"agent_name": "Atlas", "text": "first", "kind": "work", "project_id": "proj_1"},
    )
    await ACTIVITY.append(session, BIZ, {"agent_name": "Atlas", "text": "second", "kind": "system"})
    await session.commit()


def _biz_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}"


async def test_read_business_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    await _authed(session, api_client)

    res = await api_client.get(_biz_url())
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body.keys()) == sorted(
        [
            "id",
            "name",
            "tagline",
            "industry",
            "location",
            "ownerName",
            "healthScore",
            "healthBreakdown",
            "metrics",
            "revenueSeries",
            "contract",
        ]
    )
    assert body["id"] == BIZ and body["contract"] == "v1"
    assert "notes" not in body  # internal-only column never leaves the process


async def test_read_endpoints_require_auth(api_client: httpx.AsyncClient) -> None:
    urls = [
        _biz_url(),
        f"{_biz_url()}/projects",
        f"{_biz_url()}/projects/proj_1",
        f"{_biz_url()}/projects/proj_1/tasks",
        f"{_biz_url()}/agents",
        f"{_biz_url()}/agents/ag_1",
        f"{_biz_url()}/approvals",
        f"{_biz_url()}/approvals/ap_1",
        f"{_biz_url()}/activity",
        f"{_biz_url()}/brain",
    ]
    for url in urls:
        res = await api_client.get(url)
        assert res.status_code == 401, url
        assert res.json()["error"]["code"] == "unauthorized"


async def test_list_projects(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/projects")
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body.keys()) == ["contract", "items", "limit", "offset", "total"]
    assert body["total"] == 2 and body["limit"] == 50 and body["offset"] == 0
    assert [p["id"] for p in body["items"]] == ["proj_1", "proj_2"]  # stable id order
    proj = body["items"][0]
    assert sorted(proj.keys()) == sorted(
        [
            "id",
            "businessId",
            "title",
            "goal",
            "status",
            "progress",
            "budget",
            "aiBudget",
            "aiSpent",
            "allowUnpricedAi",
            "deadline",
            "teamIds",
            "milestones",
            "contract",
        ]
    )
    assert proj["businessId"] == BIZ
    # Money serializes as the existing wire format does (Decimal → "0.00",
    # same as Stage 5 TaskResponse.costAccrued — wire-compat, not redefined).
    assert str(proj["aiSpent"]) == "0.00"


async def test_read_project_and_missing(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/projects/proj_1")
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "proj_1"

    missing = await api_client.get(f"{_biz_url()}/projects/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_list_tasks_scoped_and_ordered(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/projects/proj_1/tasks")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert [t["id"] for t in body["items"]] == ["t_a", "t_b"]  # stable, not insertion order
    assert all(t["projectId"] == "proj_1" for t in body["items"])

    other = await api_client.get(f"{_biz_url()}/projects/proj_2/tasks")
    assert [t["id"] for t in other.json()["items"]] == ["t_c"]

    page = await api_client.get(f"{_biz_url()}/projects/proj_1/tasks?limit=1&offset=1")
    assert page.status_code == 200
    assert [t["id"] for t in page.json()["items"]] == ["t_b"]
    assert page.json()["total"] == 2 and page.json()["limit"] == 1

    past_end = await api_client.get(f"{_biz_url()}/projects/proj_1/tasks?offset=99")
    assert past_end.json()["items"] == [] and past_end.json()["total"] == 2


async def test_list_tasks_missing_project_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/projects/nope/tasks")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_pagination_bounds_422(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    for bad in ("limit=0", "limit=201", "offset=-1"):
        res = await api_client.get(f"{_biz_url()}/projects/proj_1/tasks?{bad}")
        assert res.status_code == 422, bad
        assert res.json()["error"]["code"] == "validation_error"


async def test_agents_list_get(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/agents")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert [a["id"] for a in body["items"]] == ["ag_1", "ag_2"]
    agent = body["items"][0]
    assert sorted(agent.keys()) == sorted(
        [
            "id",
            "businessId",
            "name",
            "role",
            "responsibilities",
            "tools",
            "permissions",
            "status",
            "currentTask",
            "costPerMonth",
            "performance",
            "hired",
            "avatarHue",
            "contract",
        ]
    )

    one = await api_client.get(f"{_biz_url()}/agents/ag_2")
    assert one.status_code == 200 and one.json()["id"] == "ag_2"

    missing = await api_client.get(f"{_biz_url()}/agents/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_approvals_list_get(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/approvals")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1 and body["items"][0]["id"] == "ap_1"
    approval = body["items"][0]
    assert sorted(approval.keys()) == sorted(
        [
            "id",
            "businessId",
            "projectId",
            "title",
            "requestedBy",
            "requestedByAgentId",
            "ageLabel",
            "status",
            "what",
            "expectedResult",
            "cost",
            "affects",
            "rationale",
            "gatesTaskId",
            "contract",
        ]
    )

    one = await api_client.get(f"{_biz_url()}/approvals/ap_1")
    assert one.status_code == 200 and one.json()["status"] == "pending"

    missing = await api_client.get(f"{_biz_url()}/approvals/nope")
    assert missing.status_code == 404


async def test_activity_list_and_project_filter(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/activity")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert {e["text"] for e in body["items"]} == {"first", "second"}
    event = body["items"][0]
    assert sorted(event.keys()) == sorted(
        ["id", "at", "agentName", "text", "kind", "cost", "contract"]
    )
    assert event["at"].endswith("Z")  # RFC 3339 server truth (§4)

    filtered = await api_client.get(f"{_biz_url()}/activity?project_id=proj_1")
    assert filtered.status_code == 200
    assert [e["text"] for e in filtered.json()["items"]] == ["first"]

    unknown_scope = await api_client.get(f"{_biz_url()}/activity?project_id=nope")
    assert unknown_scope.status_code == 404
    assert unknown_scope.json()["error"]["code"] == "not_found"


async def test_brain_list_category_filter(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await _seed_reads(session)
    await _authed(session, api_client)

    res = await api_client.get(f"{_biz_url()}/brain")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert [e["id"] for e in body["items"]] == ["br_1", "br_2"]
    entry = body["items"][0]
    assert sorted(entry.keys()) == sorted(
        ["id", "businessId", "category", "title", "body", "updatedLabel", "contract"]
    )

    goals = await api_client.get(f"{_biz_url()}/brain?category=goals")
    assert goals.status_code == 200
    assert [e["id"] for e in goals.json()["items"]] == ["br_1"]
    assert goals.json()["total"] == 1

    bad = await api_client.get(f"{_biz_url()}/brain?category=telepathy")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_enum"


async def test_empty_lists(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    await _authed(session, api_client)

    for url in (
        f"{_biz_url()}/projects/proj_1/tasks",
        f"{_biz_url()}/approvals",
        f"{_biz_url()}/activity",
        f"{_biz_url()}/brain",
    ):
        res = await api_client.get(url)
        assert res.status_code == 200, url
        assert res.json()["items"] == [] and res.json()["total"] == 0


async def test_cross_tenant_reads_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    """User B reads A's scope: uniform 404 everywhere, existence never confirmed."""
    from tests.test_isolation import _login_as

    await make_world(session, biz_id=BIZ)
    await make_task(session, task_id="t_a", status="not_started")
    await make_business(session, biz_id=BIZ_OTHER)
    await make_agent(session, agent_id="ag_2", biz_id=BIZ_OTHER)
    await make_project(session, project_id="proj_2", biz_id=BIZ_OTHER)
    await session.commit()

    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    urls = [
        _biz_url(BIZ),
        f"{_biz_url(BIZ)}/projects",
        f"{_biz_url(BIZ)}/projects/proj_1",
        f"{_biz_url(BIZ)}/projects/proj_1/tasks",
        f"{_biz_url(BIZ)}/agents",
        f"{_biz_url(BIZ)}/agents/ag_1",
        f"{_biz_url(BIZ)}/approvals",
        f"{_biz_url(BIZ)}/activity",
        f"{_biz_url(BIZ)}/brain",
    ]
    for url in urls:
        res = await api_client.get(url, headers=headers_b)
        assert res.status_code == 404, url
        assert res.json()["error"]["code"] == "not_found"

    # Unknown business is indistinguishable from foreign business.
    ghost = await api_client.get(_biz_url("biz_ghost"), headers=headers_b)
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "not_found"
