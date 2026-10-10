"""Auth endpoints: login/logout/me, throttling, CSRF, session lifecycle."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.identity import hash_session_id
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.rate_limit import LoginThrottle
from app.main import create_app
from app.persistence.engine import session_factory
from tests.factories import BIZ, make_business


async def _user(session: AsyncSession, uid: str = "u_auth", email: str = "op@example.com") -> None:
    from app.persistence.models.user import User

    session.add(
        User(id=uid, email=email, password_hash=hash_password("correct-horse-1"), display_name="Op")
    )
    await session.flush()
    await session.commit()
    await session.commit()


async def _client_for(test_engine, **settings_over) -> httpx.AsyncClient:
    data: dict = {"environment": "test"}
    data.update(settings_over)
    app = create_app(Settings(**data))
    app.state.session_factory = session_factory(test_engine)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _login(
    client: httpx.AsyncClient, email: str = "op@example.com", password: str = "correct-horse-1"
):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_passwords_hash_verify_rehash() -> None:
    hashed = hash_password("correct-horse-1")
    assert hashed != "correct-horse-1" and verify_password(hashed, "correct-horse-1")
    assert not verify_password(hashed, "wrong")
    assert not verify_password("not-a-hash", "wrong")
    assert needs_rehash("not-a-hash") is True


async def test_throttle_bounds() -> None:
    throttle = LoginThrottle(max_attempts=2, window_seconds=60)
    assert throttle.check("a") and throttle.check("a")
    throttle.record("a")
    throttle.record("a")
    assert not throttle.check("a")
    assert throttle.check("b")  # per-key isolation


async def test_login_ok_sets_cookie_and_returns_csrf(session: AsyncSession, test_engine) -> None:
    await _user(session)
    await make_business(session)
    await session.commit()
    from app.persistence.repositories.concrete import MembershipRepository

    async with session_factory(test_engine)() as s2:
        await MembershipRepository().grant(s2, "u_auth", BIZ, "owner")
        await s2.commit()
    async with await _client_for(test_engine) as client:
        res = await _login(client)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["user"]["email"] == "op@example.com"
        assert body["csrf_token"] and body["businesses"] == [BIZ]
        assert "bos_session=" in res.headers.get("set-cookie", "")
        assert "httponly" in res.headers["set-cookie"].lower()


async def test_login_unknown_email_matches_wrong_password(
    session: AsyncSession, test_engine
) -> None:
    await _user(session)
    async with await _client_for(test_engine) as client:
        bad_pass = await _login(client, password="nope")
        unknown = await _login(client, email="ghost@example.com", password="nope")
    assert bad_pass.status_code == 401 and unknown.status_code == 401
    for res in (bad_pass, unknown):
        err = res.json()["error"]
        assert err["code"] == "unauthorized"
        assert err["message"] == "Invalid email or password."
        assert err["trace_id"]  # present but unique per request — never compared


async def test_login_throttled_after_budget(session: AsyncSession, test_engine) -> None:
    await _user(session)
    async with await _client_for(test_engine) as client:
        # Swap in a tight throttle through app state (same seam as prod code).
        client._transport.app.state.login_throttle = LoginThrottle(2, 600)
        assert (await _login(client, password="nope")).status_code == 401
        assert (await _login(client, password="nope")).status_code == 401
        res = await _login(client, password="nope")
        assert res.status_code == 429
        assert res.json()["error"]["code"] == "rate_limited"


async def test_logout_revokes_and_me_forgets(session: AsyncSession, test_engine) -> None:
    await _user(session)
    async with await _client_for(test_engine) as client:
        res = await _login(client)
        csrf = res.json()["csrf_token"]
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        out = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert out.status_code == 200
        # Cookie cleared by the client jar automatically? httpx keeps it вЂ”
        # server-side revocation is what matters:
        me2 = await client.get("/api/v1/auth/me")
        assert me2.status_code == 401


async def test_expired_session_rejected(session: AsyncSession, test_engine) -> None:
    await _user(session)
    from app.persistence.repositories.concrete import AuthSessionRepository

    async with session_factory(test_engine)() as s2:
        await AuthSessionRepository().create(
            s2,
            id_hash=hash_session_id("stale-raw-id"),
            user_id="u_auth",
            csrf_token="csrf-x",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await s2.commit()
    async with await _client_for(test_engine) as client:
        client.cookies.set("bos_session", "stale-raw-id")
        res = await client.get("/api/v1/auth/me")
        assert res.status_code == 401


async def test_csrf_required_on_mutation_but_not_on_read(
    session: AsyncSession, test_engine
) -> None:
    await _user(session)
    async with await _client_for(test_engine) as client:
        res = await _login(client)
        csrf = res.json()["csrf_token"]
        # Safe method works without any CSRF header.
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        # Mutation without token → 403 with the contract `forbidden` code.
        missing = await client.post("/api/v1/auth/logout")
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "forbidden"
        bad = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "nope"})
        assert bad.status_code == 403
        assert bad.json()["error"]["code"] == "forbidden"
        ok = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200


async def test_unknown_email_costs_a_password_verify(
    session: AsyncSession, test_engine, monkeypatch
) -> None:
    """No timing oracle: unknown email runs one dummy Argon2 verify."""
    import app.api.v1.auth as auth_module

    await _user(session)
    calls: list[str] = []
    real_verify = auth_module.verify_password

    def counting(password_hash: str, password: str) -> bool:
        calls.append(password_hash)
        return real_verify(password_hash, password)

    monkeypatch.setattr(auth_module, "verify_password", counting)
    async with await _client_for(test_engine) as client:
        unknown = await _login(client, email="ghost@example.com", password="nope")
        assert unknown.status_code == 401
        assert unknown.json()["error"]["code"] == "unauthorized"
    assert len(calls) == 1  # dummy-hash verify ran even with no such user
