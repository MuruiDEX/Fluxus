"""Stage 15 pack catalog, preview, and atomic idempotent install.

Covers: catalog list/detail/preview, install 201 (agents + workflow + audit +
receipt), same-version 200 already_installed, different-version 409, disabled
existing agent 422, reuse without mutation, missing agent listing 422
pack_unavailable, unknown slug 404, csrf/anon/foreign guards, audit failure
rollback, concurrent installs serialize to one 201, and per-business isolation.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packs.catalog import FITNESS_PACK
from app.persistence.models.agent import Agent
from app.persistence.models.pack import PackInstallation, PackInstallationItem
from app.persistence.models.project import Project
from app.persistence.repositories.concrete import (
    ActivityRepository,
    MarketplaceRepository,
    MembershipRepository,
)
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_agent, make_business, make_world

_catalog = MarketplaceRepository()
_activity = ActivityRepository()

AGENT_LISTINGS = [
    {
        "id": "sales-manager",
        "name": "AI Sales Manager",
        "role": "Sales",
        "blurb": "Closes deals.",
        "price": "$29/month",
        "price_note": "per month",
        "rating": 4.9,
        "installs": "1.2k",
        "badge": "Popular",
        "capabilities": ["CRM", "Outreach"],
        "permissions": {"read": ["CRM"], "write": [], "actions": [], "denied": []},
        "category": "employee",
    },
    {
        "id": "support-ai",
        "name": "Customer Support AI",
        "role": "Support",
        "blurb": "Answers tickets.",
        "price": "$19/month",
        "price_note": "per month",
        "rating": 4.7,
        "installs": "900",
        "badge": None,
        "capabilities": ["Tickets"],
        "permissions": {"read": ["Inbox"], "write": [], "actions": [], "denied": []},
        "category": "employee",
    },
]

PACK_LISTING = {
    "id": "fitness-pack",
    "name": "Fitness Business Pack",
    "role": "Business pack",
    "blurb": "One-click fitness stack.",
    "price": "$99/month",
    "price_note": "one-click install",
    "rating": 4.9,
    "installs": "2.1k",
    "badge": "Popular",
    "capabilities": ["Sales AI", "Support AI"],
    "permissions": {"read": ["CRM"], "write": [], "actions": [], "denied": []},
    "category": "pack",
}


async def _seed_catalog(session: AsyncSession, *, with_agents: bool = True) -> None:
    rows = [PACK_LISTING] + (AGENT_LISTINGS if with_agents else [])
    for row in rows:
        if await _catalog.get(session, BIZ, row["id"]) is None:
            await _catalog.create(session, BIZ, dict(row))
    await session.commit()


async def _authed(
    session: AsyncSession, api_client: httpx.AsyncClient, *, with_agents: bool = True
) -> dict:
    await make_world(session)
    await _seed_catalog(session, with_agents=with_agents)
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


def _packs_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/packs"


def _pack_url(slug: str = "fitness-pack", biz: str = BIZ) -> str:
    return f"{_packs_url(biz)}/{slug}"


def _preview_url(slug: str = "fitness-pack", biz: str = BIZ) -> str:
    return f"{_pack_url(slug, biz)}/preview"


def _install_url(slug: str = "fitness-pack", biz: str = BIZ) -> str:
    return f"{_pack_url(slug, biz)}/install"


async def _agent_count(session: AsyncSession, biz: str, slug: str) -> int:
    res = await session.execute(
        select(Agent).where(Agent.business_id == biz, Agent.source_slug == slug)
    )
    return len(list(res.scalars().all()))


async def _installations(session: AsyncSession, biz: str) -> list[PackInstallation]:
    res = await session.execute(select(PackInstallation).where(PackInstallation.business_id == biz))
    return list(res.scalars().all())


async def _items(session: AsyncSession, biz: str) -> list[PackInstallationItem]:
    res = await session.execute(
        select(PackInstallationItem).where(PackInstallationItem.business_id == biz)
    )
    return list(res.scalars().all())


async def test_list_and_read_and_preview(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)

    listed = await api_client.get(_packs_url(), headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["contract"] == "v1" and body["total"] == 1
    pack = body["items"][0]
    assert pack["slug"] == "fitness-pack"
    assert pack["version"] == FITNESS_PACK.version
    assert pack["installed"] is False and pack["installedVersion"] is None
    assert [a["slug"] for a in pack["agents"]] == ["sales-manager", "support-ai"]
    assert {a["action"] for a in pack["agents"]} == {"create"}
    assert pack["workflow"]["ref"] == "fitness-client-onboarding"
    assert [t["key"] for t in pack["workflow"]["tasks"]] == ["qualify", "book"]
    assert pack["workflow"]["tasks"][1]["dependsOn"] == ["qualify"]

    detail = await api_client.get(_pack_url(), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["slug"] == "fitness-pack"

    preview = await api_client.get(_preview_url(), headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["canInstall"] is True
    assert preview.json()["blockers"] == []


async def test_install_creates_agents_workflow_receipt_and_audit(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["outcome"] == "installed"
    assert body["pack"]["installed"] is True
    assert body["pack"]["installedVersion"] == "1.0.0"
    assert {a["slug"] for a in body["agents"]} == {"sales-manager", "support-ai"}
    assert all(a["action"] == "create" and a["agentId"] for a in body["agents"])
    assert body["project"]["title"] == "Fitness Client Onboarding"
    assert body["project"]["status"] == "planning"
    assert [t["title"] for t in body["tasks"]] == ["Qualify new lead", "Book onboarding session"]
    assert all(t["status"] == "not_started" for t in body["tasks"])
    book = [t for t in body["tasks"] if t["title"] == "Book onboarding session"][0]
    qualify = [t for t in body["tasks"] if t["title"] == "Qualify new lead"][0]
    assert book["dependsOn"] == [qualify["id"]]

    assert await _agent_count(session, BIZ, "sales-manager") == 1
    assert await _agent_count(session, BIZ, "support-ai") == 1
    sales = (
        await session.execute(
            select(Agent).where(Agent.business_id == BIZ, Agent.source_slug == "sales-manager")
        )
    ).scalar_one()
    assert sales.hired is True and sales.cost_per_month == 29.0

    installs = await _installations(session, BIZ)
    assert len(installs) == 1 and installs[0].version == "1.0.0"
    items = await _items(session, BIZ)
    assert {(i.kind, i.ref) for i in items} == {
        ("agent", "sales-manager"),
        ("agent", "support-ai"),
        ("workflow", "fitness-client-onboarding"),
    }
    events = [e for e in await _activity.list(session, BIZ) if e.kind == "system"]
    assert any("fitness-pack" in e.text and "installed" in e.text for e in events)

    detail = await api_client.get(_pack_url(), headers=headers)
    assert detail.json()["installed"] is True
    assert detail.json()["installedVersion"] == "1.0.0"
    assert detail.json()["agents"][0]["action"] == "reuse"

    marketplace = await api_client.get(
        f"/api/v1/businesses/{BIZ}/marketplace/fitness-pack", headers=headers
    )
    assert marketplace.json()["installed"] is True
    assert marketplace.json()["installedVersion"] == "1.0.0"


async def test_repeat_install_is_200_already_installed(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    first = await api_client.post(_install_url(), headers=headers)
    assert first.status_code == 201, first.text
    second = await api_client.post(_install_url(), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "already_installed"
    assert second.json()["installationId"] == first.json()["installationId"]
    assert await _agent_count(session, BIZ, "sales-manager") == 1
    assert len(await _installations(session, BIZ)) == 1


async def test_concurrent_install_serializes(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    results = await asyncio.gather(
        api_client.post(_install_url(), headers=headers),
        api_client.post(_install_url(), headers=headers),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 201], [r.text for r in results]
    assert await _agent_count(session, BIZ, "sales-manager") == 1
    assert len(await _installations(session, BIZ)) == 1


async def test_reuse_existing_agent_without_mutation(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    existing = await make_agent(
        session,
        agent_id="ag_sales",
        biz_id=BIZ,
        name="My Custom Sales",
        role="Sales Lead",
        status="working",
        hired=True,
        source_slug="sales-manager",
        cost_per_month=5.0,
    )
    await session.commit()

    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 201, res.text
    reused = [a for a in res.json()["agents"] if a["slug"] == "sales-manager"][0]
    assert reused["action"] == "reuse" and reused["agentId"] == existing.id
    refreshed = await session.get(Agent, "ag_sales")
    assert refreshed is not None
    assert refreshed.name == "My Custom Sales"
    assert refreshed.status == "working"
    assert refreshed.cost_per_month == 5.0
    assert await _agent_count(session, BIZ, "sales-manager") == 1
    assert await _agent_count(session, BIZ, "support-ai") == 1


async def test_disabled_existing_agent_blocks_install(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    await make_agent(
        session,
        agent_id="ag_sales",
        biz_id=BIZ,
        hired=False,
        source_slug="sales-manager",
    )
    await session.commit()

    preview = await api_client.get(_preview_url(), headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["canInstall"] is False
    assert preview.json()["blockers"][0]["code"] == "agent_disabled"

    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "agent_disabled"
    assert await _installations(session, BIZ) == []
    assert await _agent_count(session, BIZ, "support-ai") == 0


async def test_preview_version_conflict_and_install_409(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    from app.persistence.repositories.concrete import PackInstallationRepository

    await PackInstallationRepository().create(
        session,
        BIZ,
        {"id": "pi_old", "pack_slug": "fitness-pack", "version": "0.9.0"},
    )
    await session.commit()

    preview = await api_client.get(_preview_url(), headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["canInstall"] is False
    assert preview.json()["blockers"][0]["code"] == "pack_version_conflict"

    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "pack_version_conflict"
    assert await _agent_count(session, BIZ, "sales-manager") == 0


async def test_missing_agent_listing_is_pack_unavailable(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client, with_agents=False)
    preview = await api_client.get(_preview_url(), headers=headers)
    assert preview.status_code == 422, preview.text
    assert preview.json()["error"]["code"] == "pack_unavailable"

    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "pack_unavailable"
    assert await _installations(session, BIZ) == []


async def test_unknown_pack_is_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.get(_pack_url("ghost"), headers=headers)).status_code == 404
    assert (await api_client.get(_preview_url("ghost"), headers=headers)).status_code == 404
    assert (await api_client.post(_install_url("ghost"), headers=headers)).status_code == 404


async def test_pack_guards_csrf_anon_foreign(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await _seed_catalog(session)
    await seed_operator(session)

    # Anonymous (no session cookie yet) is 401 before anything else.
    assert (await api_client.get(_packs_url())).status_code == 401
    assert (await api_client.get(_pack_url())).status_code == 401
    assert (await api_client.post(_install_url())).status_code == 401

    csrf = await login_as(api_client)
    assert (await api_client.post(_install_url())).status_code == 403
    assert await _installations(session, BIZ) == []

    await make_business(session, BIZ_OTHER)
    await session.commit()
    foreign = await api_client.get(_packs_url(BIZ_OTHER), headers={"X-CSRF-Token": csrf})
    assert foreign.status_code == 404, foreign.text


async def test_install_rolls_back_on_audit_failure(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    async def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    from app.packs import installer as installer_module

    monkeypatch.setattr(installer_module.ActivityRepository, "append", boom)
    with pytest.raises(RuntimeError, match="audit store down"):
        await api_client.post(_install_url(), headers=headers)
    assert await _installations(session, BIZ) == []
    assert await _agent_count(session, BIZ, "sales-manager") == 0
    projects = await session.execute(
        select(Project).where(
            Project.business_id == BIZ,
            Project.title == "Fitness Client Onboarding",
        )
    )
    assert list(projects.scalars().all()) == []


async def test_install_is_business_scoped(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    await make_business(session, BIZ_OTHER)
    await MembershipRepository().grant(session, "u_operator", BIZ_OTHER, "owner")
    await session.commit()

    res = await api_client.post(_install_url(), headers=headers)
    assert res.status_code == 201, res.text

    other = await api_client.get(_packs_url(BIZ_OTHER), headers=headers)
    assert other.status_code == 200, other.text
    assert other.json()["items"][0]["installed"] is False
    assert await _agent_count(session, BIZ_OTHER, "sales-manager") == 0
