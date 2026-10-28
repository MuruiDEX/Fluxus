"""Guards for developer-only fixture scripts (Stage 17).

Pins that demo/E2E writers cannot target production, cannot run without an
explicit script-specific opt-in, and cannot silently write to a non-loopback
database.
"""

import asyncio
import importlib

import pytest

from app.core.script_safety import UnsafeScriptError, guard_development_script

_GUARD_ENV = (
    "APP_ENVIRONMENT",
    "APP_ALLOW_DEMO_SEED",
    "APP_ALLOW_REMOTE_DEMO_SEED",
    "APP_ALLOW_E2E_SETUP",
    "APP_ALLOW_REMOTE_E2E_SETUP",
    "APP_ALLOW_REPRO",
    "APP_ALLOW_REMOTE_REPRO",
    "REPRO_DATABASE_URL",
)


def _call(database_url: str) -> None:
    guard_development_script(
        "seed_demo",
        database_url,
        opt_in_env="APP_ALLOW_DEMO_SEED",
        allow_remote_env="APP_ALLOW_REMOTE_DEMO_SEED",
    )


def _guard(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str = "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse",
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    monkeypatch.setenv("APP_ALLOW_DEMO_SEED", "1")
    _call(database_url)


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GUARD_ENV:
        monkeypatch.delenv(name, raising=False)


def test_guard_refuses_prod_even_with_every_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "prod")
    monkeypatch.setenv("APP_ALLOW_DEMO_SEED", "1")
    monkeypatch.setenv("APP_ALLOW_REMOTE_DEMO_SEED", "1")
    with pytest.raises(UnsafeScriptError, match="APP_ENVIRONMENT=prod"):
        guard_development_script(
            "seed_demo",
            "postgresql+asyncpg://muse:muse@db.example.com:5432/muse",
            opt_in_env="APP_ALLOW_DEMO_SEED",
            allow_remote_env="APP_ALLOW_REMOTE_DEMO_SEED",
        )


def test_guard_refuses_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_ALLOW_DEMO_SEED", "1")
    with pytest.raises(UnsafeScriptError, match="expected local, dev, or test"):
        _call("postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")


def test_guard_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    with pytest.raises(UnsafeScriptError, match="APP_ALLOW_DEMO_SEED=1"):
        _call("postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")


def test_guard_allows_loopback_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _guard(monkeypatch)
    _guard(monkeypatch, "postgresql+asyncpg://muse:muse@localhost:5432/muse")
    _guard(monkeypatch, "postgresql+asyncpg://muse:muse@[::1]:5432/muse")


def test_guard_refuses_remote_database_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnsafeScriptError, match="APP_ALLOW_REMOTE_DEMO_SEED=1"):
        _guard(monkeypatch, "postgresql+asyncpg://muse:muse@db.example.com:5432/muse")


def test_guard_allows_remote_with_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ALLOW_REMOTE_DEMO_SEED", "1")
    _guard(monkeypatch, "postgresql+asyncpg://muse:muse@db.example.com:5432/muse")


def test_guard_refuses_hostless_or_malformed_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(UnsafeScriptError, match="without a host"):
        _guard(monkeypatch, "postgresql+asyncpg:///muse")
    with pytest.raises(UnsafeScriptError, match="not a valid URL"):
        _guard(monkeypatch, "postgresql+asyncpg://muse:muse@localhost:notaport/muse")


@pytest.mark.parametrize(
    ("module_name", "opt_in_env", "engine_factory"),
    [
        ("scripts.seed_demo", "APP_ALLOW_DEMO_SEED", "create_engine_from_url"),
        ("scripts.e2e_live_setup", "APP_ALLOW_E2E_SETUP", "create_engine_from_url"),
        ("scripts.repro_flush_anomaly", "APP_ALLOW_REPRO", "create_async_engine"),
    ],
)
def test_fixture_scripts_refuse_prod_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    opt_in_env: str,
    engine_factory: str,
) -> None:
    module = importlib.import_module(module_name)
    monkeypatch.setenv("APP_ENVIRONMENT", "prod")
    monkeypatch.setenv(opt_in_env, "1")
    if module_name == "scripts.repro_flush_anomaly":
        monkeypatch.setenv(
            "REPRO_DATABASE_URL",
            "postgresql+asyncpg://testuser:testpass@127.0.0.1:55434/muse_test",
        )

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("fixture script tried to connect before its production guard")

    monkeypatch.setattr(module, engine_factory, _must_not_connect)
    with pytest.raises(SystemExit, match="APP_ENVIRONMENT=prod"):
        asyncio.run(module.main())


def test_pg_start_is_retired(capsys: pytest.CaptureFixture[str]) -> None:
    module = importlib.import_module("scripts.pg_start")
    assert module.main() == 2
    output = capsys.readouterr().err
    assert "embedded PostgreSQL startup is disabled" in output
    assert "55434" in output


def test_pg_ensure_dbs_requires_explicit_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.pg_ensure_dbs")

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("pg_ensure_dbs tried to connect without an explicit URL")

    monkeypatch.setattr(module, "create_async_engine", _must_not_connect)
    with pytest.raises(SystemExit):
        module.main([])


def test_pg_ensure_dbs_rejects_protected_port_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.pg_ensure_dbs")

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("pg_ensure_dbs tried to connect before URL validation")

    monkeypatch.setattr(module, "create_async_engine", _must_not_connect)
    with pytest.raises(SystemExit, match="protected port"):
        module.main(["--base-url", "postgresql://testuser:testpass@127.0.0.1:5433"])


def test_repro_requires_explicit_url_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.repro_flush_anomaly")

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("repro script tried to connect without REPRO_DATABASE_URL")

    monkeypatch.setattr(module, "create_async_engine", _must_not_connect)
    monkeypatch.delenv("REPRO_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="REPRO_DATABASE_URL is required"):
        asyncio.run(module.main())


def test_repro_rejects_protected_port_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.repro_flush_anomaly")

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("repro script tried to connect before URL validation")

    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    monkeypatch.setenv("APP_ALLOW_REPRO", "1")
    monkeypatch.setenv(
        "REPRO_DATABASE_URL",
        "postgresql+asyncpg://testuser:testpass@127.0.0.1:5433/muse_test",
    )
    monkeypatch.setattr(module, "create_async_engine", _must_not_connect)
    with pytest.raises(SystemExit, match="protected port"):
        asyncio.run(module.main())


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://testuser:testpass@127.0.0.1:55434?",
        "postgresql://testuser:testpass@127.0.0.1:55434#",
        "postgresql://testuser:testpass@127.0.0.1:55434/muse_test?",
    ],
)
def test_repro_rejects_bare_query_or_fragment_before_connecting(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A bare `?`/`#` parses as an empty query/fragment but still changes
    what the driver sees, so it must be rejected before any engine exists."""
    module = importlib.import_module("scripts.repro_flush_anomaly")

    def _must_not_connect(*args: object, **kwargs: object) -> None:
        pytest.fail("repro script tried to connect before URL validation")

    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    monkeypatch.setenv("APP_ALLOW_REPRO", "1")
    monkeypatch.setenv("REPRO_DATABASE_URL", value)
    monkeypatch.setattr(module, "create_async_engine", _must_not_connect)
    with pytest.raises(SystemExit, match="query or fragment") as caught:
        asyncio.run(module.main())
    assert "testpass" not in str(caught.value)
