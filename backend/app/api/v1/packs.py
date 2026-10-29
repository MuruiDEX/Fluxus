"""Pack catalog, preview, and installation API (Stage 15).

- GET .../packs — catalog packs with per-business installed state.
- GET .../packs/{slug} — one server manifest + preview of its agents/workflow.
- GET .../packs/{slug}/preview — explicit, read-only install plan + blockers.
- POST .../packs/{slug}/install — atomic install; 201 when materialized,
  200 `already_installed` for the same version, 409 for a different version.

Unknown slugs are a uniform 404. A manifest whose catalog rows are missing is
422 `pack_unavailable`. Disabled existing agents block the install with 422
`agent_disabled`. Nothing is written unless the whole pack can be installed.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.executions import _factory, _settings, _task_responses
from app.api.v1.reads import _at_iso, _project_response
from app.api.v1.schemas import (
    PackAgentResponse,
    PackBlockerResponse,
    PackInstallResponse,
    PackListResponse,
    PackPreviewResponse,
    PackResponse,
    PackWorkflowResponse,
    PackWorkflowTaskResponse,
)
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business
from app.packs.catalog import PackSpec, get_pack
from app.packs.installer import (
    InstallOutcome,
    PackUnavailableError,
    PackVersionConflictError,
    agents_by_source,
    install_pack,
    read_outcome,
)
from app.persistence.exceptions import AgentDisabledError
from app.persistence.models.marketplace_listing import MarketplaceListing
from app.persistence.models.pack import PackInstallation, PackInstallationItem
from app.persistence.repositories.concrete import (
    MarketplaceRepository,
    PackInstallationRepository,
    UserRepository,
)

router = APIRouter()
_packs = PackInstallationRepository()
_catalog = MarketplaceRepository()
_users = UserRepository()

_GUARD = [Depends(check_client_contract)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Pack not found.")


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "pack_unavailable", "message": str(exc)},
    )


def _version_conflict(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "pack_version_conflict", "message": str(exc)},
    )


def _disabled(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "agent_disabled", "message": str(exc)},
    )


async def _catalog_listing(session, slug: str) -> MarketplaceListing | None:
    return await _catalog.get(session, "", slug)


async def _catalog_map(session, spec: PackSpec) -> dict[str, MarketplaceListing]:
    rows: dict[str, MarketplaceListing] = {}
    for slug in spec.agent_slugs:
        listing = await _catalog_listing(session, slug)
        if listing is not None:
            rows[slug] = listing
    return rows


async def _workflow_project_id(
    session, business_id: str, installation: PackInstallation | None
) -> str | None:
    if installation is None:
        return None
    res = await session.execute(
        select(PackInstallationItem.entity_id).where(
            PackInstallationItem.business_id == business_id,
            PackInstallationItem.installation_id == installation.id,
            PackInstallationItem.kind == "workflow",
        )
    )
    return res.scalar_one_or_none()


async def _pack_response(
    session,
    business_id: str,
    spec: PackSpec,
    listing: MarketplaceListing | None,
    installation: PackInstallation | None,
    contract: str,
) -> PackResponse:
    listings = await _catalog_map(session, spec)
    found = await agents_by_source(session, business_id, spec.agent_slugs)
    agents: list[PackAgentResponse] = []
    for slug in spec.agent_slugs:
        agent = found.get(slug)
        if agent is None:
            action = "create"
        elif installation is not None or agent.hired:
            action = "reuse"
        else:
            action = "blocked"
        agent_listing = listings.get(slug)
        name = agent.name if agent is not None else (agent_listing.name if agent_listing else slug)
        role = agent.role if agent is not None else (agent_listing.role if agent_listing else "")
        agents.append(
            PackAgentResponse(
                slug=slug,
                name=name,
                role=role,
                capabilities=list(agent_listing.capabilities or []) if agent_listing else [],
                agentId=agent.id if agent is not None else None,
                action=action,
            )
        )

    workflow = None
    if spec.workflow is not None:
        project_id = await _workflow_project_id(session, business_id, installation)
        workflow = PackWorkflowResponse(
            ref=spec.workflow.ref,
            title=spec.workflow.title,
            goal=spec.workflow.goal,
            projectId=project_id,
            tasks=[
                PackWorkflowTaskResponse(
                    key=task.key,
                    title=task.title,
                    ownerAgentSlug=task.owner_slug,
                    dependsOn=list(task.depends_on),
                )
                for task in spec.workflow.tasks
            ],
        )

    return PackResponse(
        slug=spec.slug,
        version=spec.version,
        name=listing.name if listing is not None else spec.name,
        description=spec.description,
        blurb=listing.blurb if listing is not None else "",
        price=listing.price if listing is not None else "",
        priceNote=listing.price_note if listing is not None else "",
        rating=listing.rating if listing is not None else 0.0,
        installs=listing.installs if listing is not None else "",
        installed=installation is not None,
        installedVersion=installation.version if installation is not None else None,
        installedAt=_at_iso(installation.created_at) if installation is not None else None,
        agents=agents,
        workflow=workflow,
        contract=contract,
    )


async def _install_response(
    session,
    business_id: str,
    spec: PackSpec,
    outcome: InstallOutcome,
    contract: str,
    *,
    status_outcome: str,
) -> PackInstallResponse:
    listing = await _catalog_listing(session, spec.slug)
    listings = await _catalog_map(session, spec)
    installation = outcome.installation
    pack = await _pack_response(session, business_id, spec, listing, installation, contract)
    project = outcome.project
    tasks = await _task_responses(session, business_id, outcome.tasks, contract)
    return PackInstallResponse(
        outcome=status_outcome,
        pack=pack,
        installationId=installation.id,
        installedAt=_at_iso(installation.created_at),
        agents=[
            PackAgentResponse(
                slug=agent.slug,
                name=agent.name,
                role=agent.role,
                capabilities=(
                    list(listings[agent.slug].capabilities or []) if agent.slug in listings else []
                ),
                agentId=agent.agent_id,
                action="create" if agent.created else "reuse",
            )
            for agent in outcome.agents
        ],
        project=_project_response(project, contract) if project is not None else None,
        tasks=tasks,
        contract=contract,
    )


async def _existing_installation(session, business_id: str, slug: str) -> PackInstallation | None:
    return await _packs.get_by_slug(session, business_id, slug)


@router.get(
    "/businesses/{business_id}/packs",
    response_model=PackListResponse,
    dependencies=_GUARD,
)
async def list_packs(
    business_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    del identity
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        listings = [
            row
            for row in await _catalog.list(session, business_id, limit=200)
            if row.category == "pack"
        ]
        installations = {
            row.pack_slug: row for row in await _packs.list_for_business(session, business_id)
        }
        items = []
        for listing in listings:
            spec = get_pack(listing.id)
            if spec is None:
                continue
            items.append(
                await _pack_response(
                    session,
                    business_id,
                    spec,
                    listing,
                    installations.get(spec.slug),
                    settings.contract_version,
                )
            )
        return PackListResponse(
            items=items,
            total=len(items),
            limit=200,
            offset=0,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/packs/{slug}",
    response_model=PackResponse,
    dependencies=_GUARD,
)
async def read_pack(
    business_id: str,
    slug: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    del identity
    spec = get_pack(slug)
    if spec is None:
        raise _not_found()
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        listing = await _catalog_listing(session, slug)
        installation = await _existing_installation(session, business_id, slug)
        return await _pack_response(
            session,
            business_id,
            spec,
            listing,
            installation,
            settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/packs/{slug}/preview",
    response_model=PackPreviewResponse,
    dependencies=_GUARD,
)
async def preview_pack(
    business_id: str,
    slug: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    del identity
    spec = get_pack(slug)
    if spec is None:
        raise _not_found()
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        listing = await _catalog_listing(session, slug)
        if listing is None or listing.category != "pack":
            raise _unavailable(
                PackUnavailableError(f"Pack {slug!r} is not available in the catalog.")
            )
        for agent_slug in spec.agent_slugs:
            agent_listing = await _catalog_listing(session, agent_slug)
            if agent_listing is None or agent_listing.category != "employee":
                raise _unavailable(
                    PackUnavailableError(
                        f"Pack agent {agent_slug!r} is not available in the catalog."
                    )
                )
        installation = await _existing_installation(session, business_id, slug)
        blockers: list[PackBlockerResponse] = []
        if installation is not None and installation.version != spec.version:
            blockers.append(
                PackBlockerResponse(
                    code="pack_version_conflict",
                    message=(f"Pack is installed at {installation.version}, not {spec.version}."),
                )
            )
        found = await agents_by_source(session, business_id, spec.agent_slugs)
        for agent_slug, agent in found.items():
            if not agent.hired:
                blockers.append(
                    PackBlockerResponse(
                        code="agent_disabled",
                        message=f"Agent {agent_slug!r} is disabled.",
                    )
                )
        pack = await _pack_response(
            session, business_id, spec, listing, installation, settings.contract_version
        )
        already = installation is not None and installation.version == spec.version
        return PackPreviewResponse(
            pack=pack,
            canInstall=not blockers and not already,
            blockers=blockers,
            contract=settings.contract_version,
        )


@router.post(
    "/businesses/{business_id}/packs/{slug}/install",
    response_model=PackInstallResponse,
    status_code=201,
    dependencies=_GUARD,
)
async def install(
    business_id: str,
    slug: str,
    request: Request,
    response: Response,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    spec = get_pack(slug)
    if spec is None:
        raise _not_found()
    settings = _settings(request)
    factory = _factory(request)
    try:
        async with factory() as session:
            async with session.begin():
                user = await _users.get(session, identity.user_id)
                actor = user.email if user is not None else identity.user_id
                outcome = await install_pack(session, business_id, spec, actor=actor)
                status_outcome = "installed" if outcome.created else "already_installed"
                payload = await _install_response(
                    session,
                    business_id,
                    spec,
                    outcome,
                    settings.contract_version,
                    status_outcome=status_outcome,
                )
                if not outcome.created:
                    response.status_code = 200
        return payload
    except PackUnavailableError as exc:
        raise _unavailable(exc) from exc
    except PackVersionConflictError as exc:
        raise _version_conflict(exc) from exc
    except AgentDisabledError as exc:
        raise _disabled(exc) from exc
    except IntegrityError as exc:
        if getattr(getattr(exc, "orig", None), "sqlstate", "") != "23505":
            raise HTTPException(status_code=500, detail="Pack install could not be saved.") from exc
        async with factory() as session:
            existing = await _existing_installation(session, business_id, slug)
            if existing is None:
                raise HTTPException(
                    status_code=500, detail="Pack install could not be saved."
                ) from exc
            if existing.version != spec.version:
                raise _version_conflict(
                    PackVersionConflictError(
                        f"Pack {slug!r} is installed at {existing.version!r}, not {spec.version!r}."
                    )
                ) from exc
            outcome = await read_outcome(session, business_id, existing)
            response.status_code = 200
            return await _install_response(
                session,
                business_id,
                spec,
                outcome,
                settings.contract_version,
                status_outcome="already_installed",
            )
