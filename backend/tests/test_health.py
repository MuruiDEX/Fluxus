"""Health + error-envelope contract tests (Stage 1 foundation)."""

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.persistence.engine import create_engine_from_url, session_factory


def test_health_ok(client: TestClient) -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["contract"] == "v1"
    assert res.headers["x-trace-id"]


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/does-not-exist")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "not_found"
    assert err["trace_id"]
    assert "message" in err


async def test_readiness_ok_with_live_database(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/v1/readiness")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"status": "ready", "database": "ok"}
    assert res.headers["x-trace-id"]


def test_readiness_degraded_without_persistence(client: TestClient) -> None:
    # The TestClient app has no session_factory: readiness must say so
    # (503), never fake ready. No internals leak into the body.
    res = client.get("/api/v1/readiness")
    assert res.status_code == 503, res.text
    assert res.json() == {"status": "degraded", "database": "unconfigured"}


async def test_readiness_degraded_when_database_unreachable() -> None:
    settings = Settings(environment="test")
    app = create_app(settings)
    bad = create_engine_from_url("postgresql+asyncpg://muse:muse@127.0.0.1:59999/none")
    app.state.session_factory = session_factory(bad)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/v1/readiness")
    finally:
        await bad.dispose()
    assert res.status_code == 503, res.text
    assert res.json() == {"status": "degraded", "database": "unreachable"}


async def test_readiness_needs_no_auth(api_client: httpx.AsyncClient) -> None:
    # Probes carry no credentials by design (orchestrator health checks).
    res = await api_client.get("/api/v1/readiness")
    assert res.status_code == 200
    assert "bos_session" not in (res.headers.get("set-cookie") or "")
