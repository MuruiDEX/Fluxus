"""Pure tests for the test-only PostgreSQL URL selector and safety guard.

These tests never open a database connection or start PostgreSQL.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _pg_embedded as pg
from tests._pg_test_urls import (
    _PROTECTED_PORTS,
    database_url,
    select_pg_base_url,
    validate_pg_base_url,
)

BASE = "postgresql://testuser:testpass@127.0.0.1:55434"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scheme", ["postgresql", "postgresql+asyncpg"])
def test_validate_accepts_postgresql_scheme(scheme: str) -> None:
    value = f"{scheme}://testuser:testpass@127.0.0.1:55434"
    assert validate_pg_base_url(value) == value


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
def test_validate_accepts_numeric_loopback_hosts(host: str) -> None:
    value = f"postgresql://testuser:testpass@{host}:55434"
    assert validate_pg_base_url(value) == value


def test_validate_accepts_isolated_55434() -> None:
    assert validate_pg_base_url(BASE) == BASE


def test_protected_ports_include_55433_and_still_exclude_55434() -> None:
    assert {5432, 5433, 55433} <= _PROTECTED_PORTS
    assert 55434 not in _PROTECTED_PORTS


def test_validate_rejects_localhost_requiring_numeric_loopback() -> None:
    value = "postgresql://testuser:testpass@localhost:55434"
    with pytest.raises(ValueError, match="numeric loopback") as caught:
        validate_pg_base_url(value)
    assert "testuser" not in str(caught.value)
    assert "testpass" not in str(caught.value)
    assert value not in str(caught.value)


def test_validate_normalizes_a_trailing_slash() -> None:
    assert validate_pg_base_url(BASE + "/") == BASE


@pytest.mark.parametrize("port", [5432, 5433, 55433])
def test_validate_rejects_protected_ports_without_echoing_credentials(port: int) -> None:
    value = f"postgresql://testuser:testpass@127.0.0.1:{port}"
    with pytest.raises(ValueError, match="protected port") as caught:
        validate_pg_base_url(value)
    message = str(caught.value)
    assert "testuser" not in message
    assert "testpass" not in message
    assert value not in message


def test_protected_port_message_lists_exactly_the_protected_ports() -> None:
    """The message is derived from the set, so both can never drift apart."""
    with pytest.raises(ValueError, match="protected port") as caught:
        validate_pg_base_url("postgresql://testuser:testpass@127.0.0.1:55433")
    message = str(caught.value)
    listed = {int(match) for match in re.findall(r"\b\d{4,5}\b", message)}
    assert listed == set(_PROTECTED_PORTS)
    assert 55434 not in listed


@pytest.mark.parametrize(
    "value",
    [
        "",
        "mysql://testuser:testpass@127.0.0.1:55434",
        "http://testuser:testpass@127.0.0.1:55434",
        "postgresql://testuser:testpass@db.example.com:55434",
        "postgresql://testuser:testpass@0.0.0.0:55434",
        "postgresql://testuser:testpass@192.168.1.5:55434",
        "postgresql://testuser:testpass@127.0.0.1:55434/muse_test",
        "postgresql://testuser:testpass@127.0.0.1:55434?sslmode=require",
        "postgresql://testuser:testpass@127.0.0.1:55434#fragment",
        "postgresql://testuser:testpass@127.0.0.1:55434?",
        "postgresql://testuser:testpass@127.0.0.1:55434#",
        "postgresql://testuser:testpass@127.0.0.1:55434/muse_test?",
        "postgresql://testuser:testpass@127.0.0.1",
        "postgresql://testuser:testpass@127.0.0.1:notaport",
        "postgresql://testuser:testpass@127.0.0.1:0",
        "postgresql://testuser:testpass@127.0.0.1:-1",
        "postgresql://testuser:testpass@127.0.0.1:",
        "postgresql://testuser:testpass@127.0.0.1:65536",
        "postgresql://testuser:testpass@127.0.0.1:99999",
    ],
)
def test_validate_rejects_unsafe_urls_without_echoing_credentials(value: str) -> None:
    with pytest.raises(ValueError) as caught:
        validate_pg_base_url(value)
    message = str(caught.value)
    assert "testuser" not in message
    assert "testpass" not in message
    if value:
        assert value not in message


def test_select_uses_valid_override() -> None:
    assert select_pg_base_url(BASE) == BASE


def test_select_requires_explicit_override() -> None:
    with pytest.raises(ValueError, match="--pg-base-url") as caught:
        select_pg_base_url(None)
    assert "testuser" not in str(caught.value)
    assert "testpass" not in str(caught.value)


def test_select_rejects_invalid_override_without_echoing_credentials() -> None:
    value = "postgresql://testuser:testpass@db.example.com:55434"
    with pytest.raises(ValueError) as caught:
        select_pg_base_url(value)
    message = str(caught.value)
    assert "testuser" not in message
    assert "testpass" not in message
    assert value not in message


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (BASE, "postgresql+asyncpg://testuser:testpass@127.0.0.1:55434/muse_test"),
        (
            "postgresql+asyncpg://testuser:testpass@127.0.0.1:55434",
            "postgresql+asyncpg://testuser:testpass@127.0.0.1:55434/muse_test",
        ),
    ],
)
def test_database_url_appends_fixed_test_database_names(base: str, expected: str) -> None:
    assert database_url(base, "muse_test") == expected
    assert database_url(base, "muse_mig") == expected.replace("/muse_test", "/muse_mig")


@pytest.mark.parametrize(
    "database",
    ["", "muse", "postgres", "muse_test/extra", "../muse_test", "muse_test?x", "muse_test#x"],
)
def test_database_url_rejects_unapproved_database_names(database: str) -> None:
    with pytest.raises(ValueError, match="muse_test or muse_mig"):
        database_url(BASE, database)


def test_database_fixture_without_url_fails_closed_before_connecting() -> None:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("APP_DATABASE_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "tests/test_health.py::test_readiness_ok_with_live_database",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "pytest.UsageError" in output
    assert "PostgreSQL test URL is required" in output


def test_ensure_postgres_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="protected port 5433"):
        pg.ensure_postgres()


def test_ensure_postgres_refuses_explicit_protected_opt_in() -> None:
    with pytest.raises(RuntimeError, match="protected port 5433"):
        pg.ensure_postgres(allow_existing_pgdata=True)


def test_ensure_postgres_does_not_start_existing_pgdata(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    monkeypatch.setattr(pg, "PGDATA", pgdata)

    with pytest.raises(RuntimeError, match="protected port 5433"):
        pg.ensure_postgres()

    assert not (pgdata / "postgresql.conf").exists()
