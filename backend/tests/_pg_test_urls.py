"""Pure helpers for selecting the isolated PostgreSQL test server.

Tests must provide an explicit ``--pg-base-url`` pointing at a numeric
loopback server on a port that is not in ``_PROTECTED_PORTS`` (the dev/protected
servers and any other reserved container). There is deliberately no default
fallback: protected local servers must never be discovered automatically.
"""

from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"postgresql", "postgresql+asyncpg"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
# 55433 is a separate protected container: it must never be used as a test DB.
_PROTECTED_PORTS = frozenset({5432, 5433, 55433})
_TEST_DATABASES = frozenset({"muse_test", "muse_mig"})


def _protected_ports_label() -> str:
    """Render the protected ports so the message cannot drift from the set."""
    return ", ".join(str(port) for port in sorted(_PROTECTED_PORTS))


def validate_pg_base_url(value: str) -> str:
    """Return a normalized base URL or raise a credential-free ValueError."""
    raw = value.strip()
    if not raw:
        raise ValueError("PostgreSQL test URL must not be empty")

    parsed = urlsplit(raw)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError("PostgreSQL test URL must use a postgresql scheme")
    if parsed.hostname is None or parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("PostgreSQL test URL must use a numeric loopback host (127.0.0.1 or ::1)")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("PostgreSQL test URL has an invalid port") from exc
    if port is None:
        raise ValueError("PostgreSQL test URL must include an explicit port")
    if not 1 <= port <= 65535:
        raise ValueError("PostgreSQL test URL has an invalid port")
    if port in _PROTECTED_PORTS:
        raise ValueError(
            "PostgreSQL test URL must not target protected port(s) "
            f"{_protected_ports_label()}"
        )

    if parsed.path not in {"", "/"}:
        raise ValueError("PostgreSQL test URL must not include a database path")
    # Reject the literal delimiters too: `?`/`#` alone parse as an empty
    # query/fragment but still change what the driver sees.
    if parsed.query or parsed.fragment or "?" in raw or "#" in raw:
        raise ValueError("PostgreSQL test URL must not include a query or fragment")

    return raw.rstrip("/")


def database_url(base_url: str, database: str) -> str:
    """Append one fixed test database name to a validated loopback base URL."""
    base = validate_pg_base_url(base_url)
    if database not in _TEST_DATABASES:
        raise ValueError("Database name must be muse_test or muse_mig")

    if base.lower().startswith("postgresql://"):
        return "postgresql+asyncpg://" + base[len("postgresql://") :] + f"/{database}"
    return f"{base}/{database}"


def select_pg_base_url(override: str | None) -> str:
    """Return a validated explicit override; fail closed when it is absent."""
    if override is None:
        raise ValueError(
            "PostgreSQL test URL is required: pass --pg-base-url with an isolated "
            "numeric loopback test server; protected port(s) "
            f"{_protected_ports_label()} are rejected"
        )
    return validate_pg_base_url(override)
