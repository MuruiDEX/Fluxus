"""Production readiness guards (Stage 12).

Pins: CORS allowlist is env-configurable with safe defaults and rejects "*";
production startup fails fast without a database or without Secure cookies;
the login throttle bounds its memory across sprayed identifiers.
"""

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings, validate_production_config
from app.core.rate_limit import LoginThrottle
from app.main import create_app


def test_cors_defaults_are_local_only() -> None:
    assert Settings(environment="test").cors_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "tauri://localhost",
        "http://tauri.localhost",
    ]


def test_cors_rejects_wildcard() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", cors_origins=["https://app.example.com", "*"])


def test_cors_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_CORS_ORIGINS", '["https://app.example.com"]')
    assert Settings(environment="test").cors_origins == ["https://app.example.com"]


async def test_custom_cors_origins_take_effect() -> None:
    settings = Settings(environment="test", cors_origins=["https://app.example.com"])
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.options(
            "/api/v1/auth/logout",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert res.headers.get("access-control-allow-origin") == "https://app.example.com"
        other = await client.options(
            "/api/v1/auth/logout",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in {k.lower() for k in other.headers}


def test_prod_guard_is_noop_off_prod() -> None:
    validate_production_config(Settings(environment="local"))
    validate_production_config(Settings(environment="test"))
    validate_production_config(Settings(environment="dev"))


def test_prod_guard_requires_database() -> None:
    with pytest.raises(ValueError, match="APP_DATABASE_URL"):
        validate_production_config(
            Settings(environment="prod", session_cookie_secure=True, database_url=None)
        )


def test_prod_guard_requires_secure_cookies() -> None:
    with pytest.raises(ValueError, match="APP_SESSION_COOKIE_SECURE"):
        validate_production_config(
            Settings(
                environment="prod",
                session_cookie_secure=False,
                database_url="postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse",
            )
        )


def test_prod_guard_passes_when_configured() -> None:
    validate_production_config(
        Settings(
            environment="prod",
            session_cookie_secure=True,
            database_url="postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse",
        )
    )


def test_create_app_enforces_prod_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic against APP_DATABASE_URL leaked by other suites (which set
    # os.environ without restoring): the guard must see an empty setting.
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="APP_DATABASE_URL"):
        create_app(Settings(environment="prod", session_cookie_secure=True))


def test_throttle_bounds_distinct_keys() -> None:
    throttle = LoginThrottle(max_attempts=2, window_seconds=300, max_keys=3)
    for i in range(5):
        assert throttle.check(f"user{i}@example.com")
        throttle.record(f"user{i}@example.com")
    # Memory stays bounded no matter how many identifiers are sprayed.
    assert len(throttle._hits) <= 3
    # Recently seen keys keep their windows; evicted ones start over.
    throttle.record("user4@example.com")
    assert not throttle.check("user4@example.com")
    assert throttle.check("user0@example.com")


def test_throttle_window_still_applies() -> None:
    throttle = LoginThrottle(max_attempts=1, window_seconds=300)
    assert throttle.check("a@example.com")
    throttle.record("a@example.com")
    assert not throttle.check("a@example.com")
