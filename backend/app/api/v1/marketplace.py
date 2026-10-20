"""Marketplace API (Stage 8.1 reads + 8.2 hire).

The catalog (`marketplace_listings`) is global; every route is still scoped
by the path business_id through the standard guard (session + live
membership; foreign scope is a uniform 404), which also lets the list mark
each row `hired` for the requesting business in one query.

- GET .../marketplace — paged catalog, stable slug order, empty OK.
- GET .../marketplace/{slug} — one listing, 404 on unknown slug.
- POST .../marketplace/{slug}/hire — materialize one Agent from an
  `employee` listing (201 AgentResponse). Pack/category rows are not
  hireable (422 listing_not_hireable); hiring the same slug twice in one
  business is 409 already_hired — enforced by the application check AND the
  partial unique index, so concurrent hirers serialize to exactly one 201.
"""

import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.api.v1.executions import _factory, _settings
from app.api.v1.reads import _LIMIT, _OFFSET, _agent_response
from app.api.v1.schemas import AgentResponse, MarketplaceListResponse, MarketplaceResponse
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business
from app.persistence.enums import AgentStatus
from app.persistence.exceptions import new_id
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    MarketplaceRepository,
    PackInstallationRepository,
    UserRepository,
)

router = APIRouter()
_marketplace = MarketplaceRepository()
_agents = AgentRepository()
_activity = ActivityRepository()
_users = UserRepository()
_installs = PackInstallationRepository()

# require_business is declared once, as a parameter (approvals pattern):
# FastAPI evaluates it a single time per request, so enforcement matches the
# sibling routers — but hire also needs the Identity value for the audit.
_GUARD = [Depends(check_client_contract)]

# Reuse the read API paging bounds (reads.py is the single place that pins
# limit/offset validation for list endpoints).

_PRICE_RE = re.compile(r"^\$(\d+(?:\.\d+)?)")


def parse_price(price: str) -> float:
    """Best-effort monthly cost from the display price ("$29/month" → 29.0).

    The catalog price is display text, not a ledger value; unparseable prices
    fall back to 0.0 (documented in contract v1.13) instead of failing hire.
    """
    match = _PRICE_RE.match((price or "").strip())
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def avatar_hue_for(slug: str) -> int:
    """Deterministic avatar hue from the slug (stable across hires)."""
    return int(hashlib.sha256(slug.encode("utf-8")).hexdigest(), 16) % 360


def _marketplace_response(
    listing,
    hired: bool,
    contract: str,
    installed_version: str | None = None,
) -> MarketplaceResponse:
    return MarketplaceResponse(
        slug=listing.id,
        name=listing.name,
        role=listing.role,
        blurb=listing.blurb,
        price=listing.price,
        priceNote=listing.price_note,
        rating=listing.rating,
        installs=listing.installs,
        badge=listing.badge,
        capabilities=list(listing.capabilities or []),
        permissions=dict(listing.permissions or {}),
        category=listing.category,
        hired=hired,
        installed=installed_version is not None,
        installedVersion=installed_version,
        contract=contract,
    )


async def _actor_email(session, identity: Identity) -> str:
    user = await _users.get(session, identity.user_id)
    return user.email if user is not None else identity.user_id


def _is_hire_conflict(exc: IntegrityError) -> bool:
    """True iff this IntegrityError is the hire duplicate (PG 23505).

    Only unique violations map to 409 already_hired; anything else fails
    closed as 500 — unexpected integrity errors are never masked.
    """
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", "") == "23505"


@router.get(
    "/businesses/{business_id}/marketplace",
    response_model=MarketplaceListResponse,
    dependencies=_GUARD,
)
async def list_marketplace(
    business_id: str,
    request: Request,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    del identity  # scope enforced by the guard; catalog itself is global
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        rows = await _marketplace.list(session, business_id, limit=limit, offset=offset)
        total = await _marketplace.count(session, business_id)
        hired = await _agents.hired_slugs(session, business_id)
        installs = {
            row.pack_slug: row.version
            for row in await _installs.list_for_business(session, business_id)
        }
        return MarketplaceListResponse(
            items=[
                _marketplace_response(
                    r,
                    r.id in hired,
                    settings.contract_version,
                    installs.get(r.id),
                )
                for r in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
            contract=settings.contract_version,
        )


@router.get(
    "/businesses/{business_id}/marketplace/{slug}",
    response_model=MarketplaceResponse,
    dependencies=_GUARD,
)
async def read_listing(
    business_id: str,
    slug: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    del identity
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        listing = await _marketplace.get(session, business_id, slug)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found.")
        hired = await _agents.hired_slugs(session, business_id)
        installation = await _installs.get_by_slug(session, business_id, listing.id)
        return _marketplace_response(
            listing,
            listing.id in hired,
            settings.contract_version,
            installation.version if installation is not None else None,
        )


@router.post(
    "/businesses/{business_id}/marketplace/{slug}/hire",
    response_model=AgentResponse,
    status_code=201,
    dependencies=_GUARD,
)
async def hire_agent(
    business_id: str,
    slug: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    try:
        async with factory() as session:
            async with session.begin():
                listing = await _marketplace.get(session, business_id, slug)
                if listing is None:
                    raise HTTPException(status_code=404, detail="Listing not found.")
                if listing.category != "employee":
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "listing_not_hireable",
                            "message": (
                                f"Listing {slug!r} is a {listing.category}, not a hireable agent."
                            ),
                        },
                    )
                hired = await _agents.hired_slugs(session, business_id)
                if listing.id in hired:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "already_hired",
                            "message": f"Listing {slug!r} is already hired.",
                        },
                    )
                agent = await _agents.create(
                    session,
                    business_id,
                    {
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
                    },
                )
                actor = await _actor_email(session, identity)
                await _activity.append(
                    session,
                    business_id,
                    {
                        "agent_name": actor,
                        "kind": "system",
                        "text": f"Agent {agent.id} hired from {listing.id} by {actor}.",
                    },
                )
                return _agent_response(agent, settings.contract_version)
    except IntegrityError as exc:
        # The session is rolled back (context exit) — re-check in a fresh
        # session whether the loser lost the hire race or hit something else.
        if not _is_hire_conflict(exc):
            raise HTTPException(status_code=500, detail="Hire could not be saved.") from exc
        async with factory() as session:
            hired = await _agents.hired_slugs(session, business_id)
            if slug in hired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "already_hired",
                        "message": f"Listing {slug!r} is already hired.",
                    },
                ) from exc
            raise HTTPException(status_code=500, detail="Hire could not be saved.") from exc
