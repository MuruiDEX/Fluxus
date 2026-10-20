"""Marketplace reads + hire (Stage 8.1/8.2).

Covers: catalog list (shape, paging, stable order, empty), hired flags per
business, single get + unknown slug 404, hire 201 with field mapping +
scope + source_slug + audit, pack 422, repeat/concurrent duplicate 409,
unknown slug 404, foreign scope 404, anonymous 401, CSRF 403, rollback on
audit failure, unmasked unexpected IntegrityError, seed catalog parity.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.agent import Agent
from app.persistence.repositories.concrete import ActivityRepository, MarketplaceRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_agent, make_business, make_task, make_world

_catalog = MarketplaceRepository()
_activity = ActivityRepository()

LISTINGS = [
    {
        "id": "seo-specialist",
        "name": "SEO Specialist AI",
        "role": "SEO",
        "blurb": "Keywords and audits.",
        "price": "$19/month",
        "price_note": "per month",
        "rating": 4.8,
        "installs": "1.8k",
        "badge": "New",
        "capabilities": ["Keywords", "Briefs"],
        "permissions": {"read": ["Web"], "write": [], "actions": [], "denied": []},
        "category": "employee",
    },
    {
        "id": "cfo-ai",
        "name": "CFO AI",
        "role": "Finance",
        "blurb": "Forecasts.",
        "price": "$29/month",
        "price_note": "per month",
        "rating": 5.0,
        "installs": "2.7k",
        "badge": None,
        "capabilities": ["Forecasts"],
        "permissions": {"read": ["Finance"], "write": [], "actions": [], "denied": []},
        "category": "employee",
    },
    {
        "id": "fitness-pack",
        "name": "Fitness Business Pack",
        "role": "Business pack",
        "blurb": "Pack.",
        "price": "$99/month",
        "price_note": "one-click install",
        "rating": 4.9,
        "installs": "2.1k",
        "badge": "Popular",
        "capabilities": ["Sales AI"],
        "permissions": {"read": ["CRM"], "write": [], "actions": [], "denied": []},
        "category": "pack",
    },
]


async def _seed_catalog(session: AsyncSession) -> None:
    for row in LISTINGS:
        if await _catalog.get(session, BIZ, row["id"]) is None:
            await _catalog.create(session, BIZ, dict(row))
    await session.commit()


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    await make_world(session)
    await _seed_catalog(session)
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _list_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/marketplace"


def _item_url(slug: str, biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/marketplace/{slug}"


def _hire_url(slug: str, biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/marketplace/{slug}/hire"


async def _agents_with_source(session: AsyncSession, biz: str, slug: str) -> list:
    res = await session.execute(
        select(Agent).where(Agent.business_id == biz, Agent.source_slug == slug)
    )
    return list(res.scalars().all())


async def _system_events(session: AsyncSession, biz: str):
    return [e for e in await _activity.list(session, biz) if e.kind == "system"]


async def test_list_catalog_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(_list_url(), headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["contract"] == "v1" and body["total"] == 3
    assert [i["slug"] for i in body["items"]] == ["cfo-ai", "fitness-pack", "seo-specialist"]
    first = body["items"][0]
    assert first["name"] == "CFO AI" and first["priceNote"] == "per month"
    assert first["hired"] is False
    assert set(first.keys()) >= {
        "slug",
        "name",
        "role",
        "blurb",
        "price",
        "priceNote",
        "rating",
        "installs",
        "badge",
        "capabilities",
        "permissions",
        "category",
        "hired",
        "contract",
    }


async def test_list_empty(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    res = await api_client.get(_list_url(), headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text
    assert res.json()["items"] == [] and res.json()["total"] == 0


async def test_list_pagination(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(_list_url() + "?limit=2&offset=1", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1
    assert [i["slug"] for i in body["items"]] == ["fitness-pack", "seo-specialist"]
    bad = await api_client.get(_list_url() + "?limit=0", headers=headers)
    assert bad.status_code == 422, bad.text


async def test_hired_flag_per_business(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    from app.persistence.repositories.concrete import MembershipRepository

    headers = await _authed(session, api_client)
    await make_business(session, BIZ_OTHER)
    await MembershipRepository().grant(session, "u_operator", BIZ_OTHER, "owner")
    await session.commit()

    res = await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert res.status_code == 201, res.text

    mine = (await api_client.get(_list_url(), headers=headers)).json()
    assert {i["slug"]: i["hired"] for i in mine["items"]} == {
        "cfo-ai": False,
        "fitness-pack": False,
        "seo-specialist": True,
    }
    other = (await api_client.get(_list_url(BIZ_OTHER), headers=headers)).json()
    assert all(i["hired"] is False for i in other["items"])


async def test_read_listing_ok_and_unknown(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.get(_item_url("cfo-ai"), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["slug"] == "cfo-ai" and res.json()["hired"] is False
    missing = await api_client.get(_item_url("ghost"), headers=headers)
    assert missing.status_code == 404, missing.text


async def test_hire_ok(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["contract"] == "v1" and body["businessId"] == BIZ
    assert body["name"] == "SEO Specialist AI" and body["role"] == "SEO"
    assert body["status"] == "available" and body["hired"] is True
    assert body["id"]  # server-generated uuid, not the slug

    rows = await _agents_with_source(session, BIZ, "seo-specialist")
    assert len(rows) == 1
    agent = rows[0]
    assert agent.id == body["id"]
    assert agent.responsibilities == ["Keywords", "Briefs"]
    assert agent.permissions == {"read": ["Web"], "write": [], "actions": [], "denied": []}
    assert agent.cost_per_month == 19.0
    assert 0 <= agent.avatar_hue < 360
    events = await _system_events(session, BIZ)
    assert len(events) == 1
    assert agent.id in events[0].text and "seo-specialist" in events[0].text


async def test_hire_pack_422(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_hire_url("fitness-pack"), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "listing_not_hireable"
    assert await _agents_with_source(session, BIZ, "fitness-pack") == []


async def test_hire_repeat_409(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    first = await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert first.status_code == 201, first.text
    second = await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "already_hired"
    assert len(await _agents_with_source(session, BIZ, "seo-specialist")) == 1


async def test_hire_concurrent_duplicate(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """Two simultaneous hires: exactly one 201, the other 409 (PG index)."""
    headers = await _authed(session, api_client)
    results = await asyncio.gather(
        api_client.post(_hire_url("cfo-ai"), headers=headers),
        api_client.post(_hire_url("cfo-ai"), headers=headers),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409], [r.text for r in results]
    loser = results[0] if results[0].status_code == 409 else results[1]
    assert loser.json()["error"]["code"] == "already_hired"
    assert len(await _agents_with_source(session, BIZ, "cfo-ai")) == 1


async def test_hire_unknown_slug_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_hire_url("ghost"), headers=headers)
    assert res.status_code == 404, res.text


async def test_marketplace_foreign_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    for method in ("get", "post"):
        url = _list_url(BIZ_OTHER) if method == "get" else _hire_url("seo-specialist", BIZ_OTHER)
        res = await getattr(api_client, method)(url, headers=headers)
        assert res.status_code == 404, (method, res.text)


async def test_marketplace_anonymous_401(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await _seed_catalog(session)
    assert (await api_client.get(_list_url())).status_code == 401
    assert (await api_client.get(_item_url("seo-specialist"))).status_code == 401
    assert (await api_client.post(_hire_url("seo-specialist"))).status_code == 401


async def test_hire_csrf_403(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _authed(session, api_client)
    res = await api_client.post(_hire_url("seo-specialist"))
    assert res.status_code == 403, res.text
    assert await _agents_with_source(session, BIZ, "seo-specialist") == []


async def test_hire_rollback_on_audit_failure(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit failure rolls back the agent too — never a hire without trail."""
    from app.api.v1 import marketplace as marketplace_module

    headers = await _authed(session, api_client)

    async def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(marketplace_module._activity, "append", boom)
    # Unhandled app exceptions surface through the ASGI test transport
    # instead of a 500 body — the rollback assertion below is the point.
    with pytest.raises(RuntimeError, match="audit store down"):
        await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert await _agents_with_source(session, BIZ, "seo-specialist") == []


async def test_unexpected_integrity_not_masked(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-duplicate IntegrityError must not become 409 already_hired."""
    from app.api.v1 import marketplace as marketplace_module

    headers = await _authed(session, api_client)

    async def broken_create(*args, **kwargs):
        raise IntegrityError("stmt", "params", Exception("disk on fire"))

    monkeypatch.setattr(marketplace_module._agents, "create", broken_create)
    res = await api_client.post(_hire_url("seo-specialist"), headers=headers)
    assert res.status_code == 500, res.text
    # A plain-string 500 detail maps to the generic envelope code — the point
    # is it must NOT be masked as a duplicate hire.
    assert res.json()["error"]["code"] != "already_hired"


async def test_seed_catalog_parity() -> None:
    """Backend seed mirrors the six frontend seed listings.

    Slugs, categories and required fields must match
    frontend/lib/data/seed-northstar.ts `marketplace` or the LIVE catalog
    diverges from the DEMO copy it replaces.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from seed_demo import CATALOG

    from app.persistence.enums import MarketplaceCategory

    assert sorted(r["id"] for r in CATALOG) == sorted(
        [
            "fitness-pack",
            "sales-manager",
            "marketing-manager",
            "seo-specialist",
            "support-ai",
            "cfo-ai",
        ]
    )
    allowed = {c.value for c in MarketplaceCategory}
    required = {
        "id",
        "name",
        "role",
        "blurb",
        "price",
        "price_note",
        "rating",
        "installs",
        "badge",
        "capabilities",
        "permissions",
        "category",
    }
    for row in CATALOG:
        assert required <= set(row.keys()), row["id"]
        assert row["category"] in allowed, row["id"]
        assert row["permissions"].keys() == {"read", "write", "actions", "denied"}


async def test_hired_agent_assignable_to_task_and_project(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    """A hired agent is an ordinary scoped agent: task owner + team writer."""
    await make_world(session)
    await make_agent(session, agent_id="ag_2", status="available")
    await make_task(session, task_id="t_1", status="not_started")
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    headers = {"X-CSRF-Token": csrf}
    await _seed_catalog(session)

    hired_id = (await api_client.post(_hire_url("seo-specialist"), headers=headers)).json()["id"]

    task_res = await api_client.patch(
        f"/api/v1/businesses/{BIZ}/projects/proj_1/tasks/t_1",
        json={"ownerAgentId": hired_id},
        headers=headers,
    )
    assert task_res.status_code == 200, task_res.text
    assert task_res.json()["ownerAgentId"] == hired_id

    proj_res = await api_client.patch(
        f"/api/v1/businesses/{BIZ}/projects/proj_1",
        json={"teamIds": ["ag_1", hired_id]},
        headers=headers,
    )
    assert proj_res.status_code == 200, proj_res.text
    assert hired_id in proj_res.json()["teamIds"]
