"""CORS preflight for the Stage 6 cookie+CSRF browser client.

The frontend sends `credentials: include` plus `X-CSRF-Token` on mutations
from the local Next dev server, so the preflight must explicitly allow that
header and echo only allowlisted origins. ASGI-level assertions pin the
configuration; they are not a claim of Tauri/browser compatibility (that
needs a real browser against a live backend).
"""

import httpx

_PREFLIGHT = {
    "Origin": "http://localhost:3000",
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type,x-csrf-token",
}

_ALLOWED = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "tauri://localhost",
    "http://tauri.localhost",
]


async def _preflight(api_client: httpx.AsyncClient, origin: str) -> httpx.Response:
    headers = dict(_PREFLIGHT)
    headers["Origin"] = origin
    return await api_client.options("/api/v1/auth/logout", headers=headers)


async def test_preflight_allows_csrf_header_for_dev_origin(api_client: httpx.AsyncClient) -> None:
    res = await _preflight(api_client, "http://localhost:3000")
    assert res.status_code == 200, res.text
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    allow_headers = res.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allow_headers
    assert "content-type" in allow_headers


async def test_preflight_allows_loopback_ip_origin(api_client: httpx.AsyncClient) -> None:
    # `next dev` on localhost:3000 is also reachable as 127.0.0.1:3000 —
    # a distinct origin to the browser, so it must preflight cleanly too.
    res = await _preflight(api_client, "http://127.0.0.1:3000")
    assert res.status_code == 200, res.text
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


async def test_preflight_rejects_unknown_origin(api_client: httpx.AsyncClient) -> None:
    res = await _preflight(api_client, "https://evil.example")
    # No echo of the foreign origin — the browser will block the real request.
    assert "access-control-allow-origin" not in {k.lower() for k in res.headers}


async def test_allowlist_stays_tight(api_client: httpx.AsyncClient) -> None:
    for origin in _ALLOWED:
        res = await _preflight(api_client, origin)
        assert res.headers.get("access-control-allow-origin") == origin, origin
    # E2E-only :3210 port is deliberately NOT allowlisted (demo mode, no backend).
    res = await _preflight(api_client, "http://localhost:3210")
    assert "access-control-allow-origin" not in {k.lower() for k in res.headers}
