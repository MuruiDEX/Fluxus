"""Atomic, idempotent pack installation (Stage 15).

One call materializes every declared agent and workflow inside the caller's
transaction. The receipt (`pack_installations` + items) is written in the same
transaction, so a failure can never leave a half-installed pack or a receipt
for rows that do not exist.

Rules enforced here:
- catalog rows must exist and have the expected category (pack/employee);
- existing agents are reused unchanged (never re-hired, never overwritten);
- a disabled existing agent blocks the whole install before any write;
- same version repeats are a read-only `already_installed` answer;
- a different version is a `pack_version_conflict` (upgrades are not part of
  this stage);
- the audit event joins the same transaction.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.marketplace import avatar_hue_for, parse_price
from app.packs.catalog import PackSpec
from app.persistence.enums import AgentStatus, MarketplaceCategory
from app.persistence.exceptions import AgentDisabledError, new_id
from app.persistence.models.agent import Agent
from app.persistence.models.marketplace_listing import MarketplaceListing
from app.persistence.models.pack import PackInstallation, PackInstallationItem
from app.persistence.models.project import Project
from app.persistence.models.task import Task
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    MarketplaceRepository,
    PackInstallationRepository,
    PlanningRepository,
    ProjectRepository,
    TaskRepository,
)


class PackUnavailableError(Exception):
    """The pack (or one of its agent listings) is not available in the catalog."""


class PackVersionConflictError(Exception):
    """A different version is already installed (no upgrade path in Stage 15)."""


@dataclass
class InstalledAgent:
    slug: str
    agent_id: str
    name: str
    role: str
    created: bool


@dataclass
class InstallOutcome:
    installation: PackInstallation
    agents: list[InstalledAgent]
    project: Project | None
    tasks: list[Task]
    created: bool


async def read_outcome(
    session: AsyncSession, business_id: str, installation: PackInstallation
) -> InstallOutcome:
    """Read an existing receipt back as the same outcome shape (no writes)."""
    res = await session.execute(
        select(PackInstallationItem)
        .where(
            PackInstallationItem.business_id == business_id,
            PackInstallationItem.installation_id == installation.id,
        )
        .order_by(PackInstallationItem.kind, PackInstallationItem.ref)
    )
    items = list(res.scalars().all())
    agents_repo = AgentRepository()
    agents: list[InstalledAgent] = []
    project: Project | None = None
    tasks: list[Task] = []
    for item in items:
        if item.kind == "agent":
            agent = await agents_repo.get(session, business_id, item.entity_id)
            if agent is not None:
                agents.append(
                    InstalledAgent(
                        slug=item.ref,
                        agent_id=agent.id,
                        name=agent.name,
                        role=agent.role,
                        created=False,
                    )
                )
        elif item.kind == "workflow":
            project = await ProjectRepository().get(session, business_id, item.entity_id)
            if project is not None:
                tasks = await TaskRepository().list_for_project(session, business_id, project.id)
    return InstallOutcome(
        installation=installation, agents=agents, project=project, tasks=tasks, created=False
    )


async def _require_catalog(
    session: AsyncSession, spec: PackSpec
) -> tuple[MarketplaceListing, dict[str, MarketplaceListing]]:
    catalog = MarketplaceRepository()
    pack_listing = await catalog.get(session, "", spec.slug)
    if pack_listing is None or pack_listing.category != MarketplaceCategory.PACK.value:
        raise PackUnavailableError(f"Pack {spec.slug!r} is not available in the catalog.")
    listings: dict[str, MarketplaceListing] = {}
    for slug in spec.agent_slugs:
        listing = await catalog.get(session, "", slug)
        if listing is None or listing.category != MarketplaceCategory.EMPLOYEE.value:
            raise PackUnavailableError(f"Pack agent {slug!r} is not available in the catalog.")
        listings[slug] = listing
    return pack_listing, listings


async def agents_by_source(
    session: AsyncSession, business_id: str, slugs: tuple[str, ...]
) -> dict[str, Agent]:
    if not slugs:
        return {}
    res = await session.execute(
        select(Agent).where(Agent.business_id == business_id, Agent.source_slug.in_(tuple(slugs)))
    )
    return {agent.source_slug: agent for agent in res.scalars().all() if agent.source_slug}


def _new_agent_payload(listing: MarketplaceListing) -> dict:
    return {
        "id": new_id(),
        "name": listing.name,
        "role": listing.role,
        "responsibilities": list(listing.capabilities or []),
        "tools": [],
        "permissions": dict(listing.permissions or {}),
        "status": AgentStatus.AVAILABLE.value,
        "cost_per_month": parse_price(listing.price),
        "performance": {},
        "hired": True,
        "avatar_hue": avatar_hue_for(listing.id),
        "source_slug": listing.id,
    }


async def install_pack(
    session: AsyncSession,
    business_id: str,
    spec: PackSpec,
    *,
    actor: str,
) -> InstallOutcome:
    """Materialize the pack or return its existing same-version receipt.

    The caller owns the surrounding transaction. Any exception rolls back all
    writes made here, including the installation row and audit event.
    """
    _pack_listing, listings = await _require_catalog(session, spec)
    installs = PackInstallationRepository()
    existing = await installs.get_by_slug(session, business_id, spec.slug)
    if existing is not None:
        if existing.version != spec.version:
            raise PackVersionConflictError(
                f"Pack {spec.slug!r} is installed at {existing.version!r}, not {spec.version!r}."
            )
        return await read_outcome(session, business_id, existing)

    found = await agents_by_source(session, business_id, spec.agent_slugs)
    for slug, agent in found.items():
        if not agent.hired:
            raise AgentDisabledError(f"pack agent {slug!r} is disabled")

    installation = await installs.create(
        session,
        business_id,
        {"id": new_id(), "pack_slug": spec.slug, "version": spec.version},
    )

    agents_repo = AgentRepository()
    installed: list[InstalledAgent] = []
    agent_ids: dict[str, str] = {}
    for slug in spec.agent_slugs:
        agent = found.get(slug)
        created = agent is None
        if agent is None:
            agent = await agents_repo.create(
                session, business_id, _new_agent_payload(listings[slug])
            )
        agent_ids[slug] = agent.id
        installed.append(
            InstalledAgent(
                slug=slug,
                agent_id=agent.id,
                name=agent.name,
                role=agent.role,
                created=created,
            )
        )
        await installs.add_item(
            session,
            business_id,
            installation.id,
            kind="agent",
            ref=slug,
            entity_id=agent.id,
        )

    project: Project | None = None
    tasks: list[Task] = []
    workflow = spec.workflow
    if workflow is not None:
        plan = {
            "project": {"title": workflow.title, "goal": workflow.goal},
            "tasks": [
                {
                    "key": task.key,
                    "title": task.title,
                    "summary": task.summary,
                    "owner_agent_id": agent_ids[task.owner_slug],
                    "depends_on": list(task.depends_on),
                }
                for task in workflow.tasks
            ],
        }
        project, tasks, _approvals = await PlanningRepository().create_plan_graph(
            session, business_id, plan
        )
        await installs.add_item(
            session,
            business_id,
            installation.id,
            kind="workflow",
            ref=workflow.ref,
            entity_id=project.id,
        )

    await ActivityRepository().append(
        session,
        business_id,
        {
            "project_id": project.id if project is not None else None,
            "agent_name": actor,
            "kind": "system",
            "text": f"Pack {spec.slug}@{spec.version} installed by {actor}.",
        },
    )
    return InstallOutcome(
        installation=installation, agents=installed, project=project, tasks=tasks, created=True
    )
