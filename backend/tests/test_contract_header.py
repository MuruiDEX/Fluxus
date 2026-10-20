"""X-Client-Contract handling: backward compatible, strict on declared majors."""

import httpx


def _url() -> str:
    return "/api/v1/businesses/b/projects/p/tasks/t/executions"


async def test_missing_header_allowed(api_client: httpx.AsyncClient) -> None:
    # Current frontend sends no header: missing must keep working.
    res = await api_client.post(_url(), json={})
    # 401 (no token) proves the request passed the contract gate, not rejected.
    assert res.status_code == 401


async def test_malformed_header_allowed(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_url(), json={}, headers={"X-Client-Contract": "not-a-version"})
    assert res.status_code == 401


async def test_compatible_major_allowed(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_url(), json={}, headers={"X-Client-Contract": "1"})
    assert res.status_code == 401
    res = await api_client.post(_url(), json={}, headers={"X-Client-Contract": "1.9"})
    assert res.status_code == 401


async def test_unknown_major_rejected(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(_url(), json={}, headers={"X-Client-Contract": "2"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "contract_mismatch"
