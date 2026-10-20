"""Health endpoint — liveness probe, no dependencies by design."""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    contract: str


class ReadinessResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.version,
        environment=settings.environment,
        contract=settings.contract_version,
    )


@router.get("/readiness", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    """Readiness probe: liveness PLUS a live database round-trip.

    Unlike /health (which is dependency-free by design), /ready answers 200
    only when a statement actually executes. Orchestrators must gate traffic
    on this endpoint: starting without persistence, a lost database, or an
    exhausted pool surfaces here as `degraded` (HTTP 503), never as a fake
    ready. No auth (probes carry no credentials) and no internals in the
    body — failures stay a two-word verdict.
    """
    from fastapi.responses import JSONResponse

    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unconfigured"}
        )
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unreachable"}
        )
    return ReadinessResponse(status="ready", database="ok")
