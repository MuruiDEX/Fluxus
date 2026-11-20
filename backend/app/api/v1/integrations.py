"""External integration endpoints (Stage 19).

Conventions match the rest of v1: business scope comes only from the path and
is re-checked against live membership; mutations require the session-bound
CSRF header; unknown/foreign rows are a uniform 404. Managing a connection is
owner-only. OAuth callbacks are a top-level browser navigation, so the
single-use state (not a JSON CSRF token) is the CSRF defense there.

Response schemas never contain tokens, PKCE verifiers or message bodies.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.v1.executions import _factory, _settings
from app.api.v1.schemas import (
    IntegrationConnectionListResponse,
    IntegrationConnectionResponse,
    IntegrationConnectRequest,
    IntegrationConnectResponse,
    IntegrationDisconnectResponse,
    IntegrationProviderListResponse,
    IntegrationProviderResponse,
)
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business, require_business_owner, require_user
from app.integrations import bootstrap as _integration_bootstrap  # noqa: F401
from app.integrations import service
from app.integrations.types import (
    IntegrationCredentialsUnavailable,
    IntegrationError,
    IntegrationNotConfigured,
)
from app.persistence.repositories.concrete import UserRepository

router = APIRouter()
_users = UserRepository()

_GUARD = [Depends(check_client_contract)]


def _http_error(exc: IntegrationError) -> HTTPException:
    if isinstance(exc, IntegrationNotConfigured | IntegrationCredentialsUnavailable):
        return HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message})
    if exc.code == "unknown_provider":
        return HTTPException(status_code=404, detail="Integration provider not found.")
    return HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


def _connection_response(payload: dict) -> IntegrationConnectionResponse:
    return IntegrationConnectionResponse(**payload)


@router.get(
    "/businesses/{business_id}/integrations/providers",
    response_model=IntegrationProviderListResponse,
    dependencies=_GUARD,
)
async def list_integration_providers(
    business_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    return IntegrationProviderListResponse(
        providers=[
            IntegrationProviderResponse(**provider)
            for provider in service.provider_listing(settings)
        ],
        contract=settings.contract_version,
    )


@router.get(
    "/businesses/{business_id}/integrations",
    response_model=IntegrationConnectionListResponse,
    dependencies=_GUARD,
)
async def list_integration_connections(
    business_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        rows = await service.connections.list_for_business(session, business_id)
        return IntegrationConnectionListResponse(
            connections=[_connection_response(service.connection_wire(row)) for row in rows],
            contract=settings.contract_version,
        )


@router.post(
    "/businesses/{business_id}/integrations/{provider}/connect",
    response_model=IntegrationConnectResponse,
    dependencies=_GUARD,
)
async def start_integration_connection(
    business_id: str,
    provider: str,
    request: Request,
    body: IntegrationConnectRequest | None = None,
    identity: Identity = Depends(require_business_owner),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    include_writes = bool(body.include_writes) if body is not None else False
    async with factory() as session:
        async with session.begin():
            if not await service.memberships.lock_owner(session, identity.user_id, business_id):
                raise HTTPException(status_code=403, detail="Business owner role required.")
            try:
                payload = await service.start_oauth(
                    session,
                    settings,
                    business_id=business_id,
                    user_id=identity.user_id,
                    provider_id=provider,
                    include_writes=include_writes,
                )
            except IntegrationError as exc:
                raise _http_error(exc) from exc
    return IntegrationConnectResponse(**payload, contract=settings.contract_version)


@router.get(
    "/businesses/{business_id}/integrations/{connection_id}",
    response_model=IntegrationConnectionResponse,
    dependencies=_GUARD,
)
async def get_integration_connection(
    business_id: str,
    connection_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    factory = _factory(request)
    async with factory() as session:
        connection = await service.connections.get(session, business_id, connection_id)
        if connection is None:
            raise HTTPException(
                status_code=404, detail="Integration connection not found in scope."
            )
        return _connection_response(service.connection_wire(connection))


@router.delete(
    "/businesses/{business_id}/integrations/{connection_id}",
    response_model=IntegrationDisconnectResponse,
    dependencies=_GUARD,
)
async def disconnect_integration(
    business_id: str,
    connection_id: str,
    request: Request,
    identity: Identity = Depends(require_business_owner),  # noqa: B008 - FastAPI idiom
):
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        async with session.begin():
            if not await service.memberships.lock_owner(session, identity.user_id, business_id):
                raise HTTPException(status_code=403, detail="Business owner role required.")
            user = await _users.get(session, identity.user_id)
            actor = user.email if user is not None else identity.user_id
            removed = await service.disconnect(
                session,
                settings,
                business_id=business_id,
                connection_id=connection_id,
                actor=actor,
            )
            if not removed:
                raise HTTPException(
                    status_code=404, detail="Integration connection not found in scope."
                )
    return IntegrationDisconnectResponse(
        outcome="disconnected",
        connectionId=connection_id,
        contract=settings.contract_version,
    )


@router.get("/integrations/oauth/callback", include_in_schema=True)
async def integration_oauth_callback(
    request: Request,
    identity: Identity = Depends(require_user),  # noqa: B008 - FastAPI idiom
):
    """OAuth redirect target. The state row proves ownership and freshness."""
    settings = _settings(request)
    factory = _factory(request)
    params = request.query_params
    frontend = str(getattr(settings, "integration_frontend_url", "")).rstrip("/")
    base = f"{frontend}/integrations"
    failure: IntegrationError | None = None
    connection = None
    try:
        async with factory() as session:
            async with session.begin():
                connection = await service.complete_oauth(
                    session,
                    settings,
                    user_id=identity.user_id,
                    state=params.get("state", ""),
                    code=params.get("code", ""),
                    error=params.get("error", ""),
                )
    except IntegrationError as exc:
        failure = exc
    if failure is not None:
        query = urlencode({"integration": "error", "code": failure.code})
        return RedirectResponse(f"{base}?{query}", status_code=303)
    assert connection is not None
    query = urlencode(
        {
            "integration": "connected",
            "provider": connection.provider,
            "account": connection.display_name,
        }
    )
    return RedirectResponse(f"{base}?{query}", status_code=303)


__all__ = ["router"]
