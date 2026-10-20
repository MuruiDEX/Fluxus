"""Application entry point — create_app factory + middleware + lifespan."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import v1_router
from app.core.config import (
    Settings,
    get_settings,
    validate_production_config,
    validate_recovery_bound,
)
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.persistence.engine import create_engine_from_url, session_factory
from app.usage.pricing import configure_tariffs

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Fail fast: a threshold that cannot prove staleness must never serve.
    validate_recovery_bound(settings)
    if settings.database_url:
        engine = create_engine_from_url(
            settings.database_url,
            statement_timeout_ms=settings.db_statement_timeout_ms,
        )
        app.state.engine = engine
        app.state.session_factory = session_factory(engine)
        log.info(
            "backend starting",
            extra={"context": {"environment": settings.environment, "version": settings.version}},
        )
        yield
        await engine.dispose()
    else:
        log.info(
            "backend starting without persistence (execution endpoints disabled)",
            extra={"context": {"environment": settings.environment, "version": settings.version}},
        )
        yield
    log.info("backend stopping")


async def trace_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.trace_id = uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Fail fast (construction AND lifespan): a threshold that cannot prove
    # staleness must never serve, in any process that builds the app.
    validate_recovery_bound(settings)
    # Production misconfiguration must never serve either (no-op elsewhere).
    validate_production_config(settings)
    configure_logging(settings.log_level)
    # Load operator tariffs (fail fast on malformed config, before serving).
    configure_tariffs(settings.usage_tariffs_json)

    app = FastAPI(title="MUSE SPARK backend", version=settings.version)
    app.state.settings = settings

    # CORS: exact-origin allowlist from settings (local-first defaults;
    # production overrides via APP_CORS_ORIGINS). Never ["*"] with
    # credentials. Playwright's :3210 port is e2e-only (demo mode, no
    # backend) and stays out of the defaults.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Client-Contract", "X-CSRF-Token", "X-Trace-Id"],
    )
    app.middleware("http")(trace_id_middleware)
    app.include_router(v1_router)

    # Unknown paths must also speak the contract envelope (Starlette's
    # default 404 bypasses exception handlers, so we catch the remainder).
    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def catch_all(request: Request, path: str):  # type: ignore[no-untyped-def]
        raise HTTPException(status_code=404, detail=f"No route for /{path}.")

    register_error_handlers(app)
    app.router.lifespan_context = lifespan
    return app


app = create_app()
