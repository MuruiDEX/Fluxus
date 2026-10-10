"""Health + error-envelope contract tests (Stage 1 foundation)."""

from fastapi.testclient import TestClient


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
