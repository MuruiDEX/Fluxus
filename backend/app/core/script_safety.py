"""Fail-closed guards for developer-only database scripts.

Scripts that seed demo data or reset E2E fixtures must never touch a
production database. These helpers require a safe APP_ENVIRONMENT, an
explicit script-specific opt-in, and (by default) a loopback database host.
"""

import os
from urllib.parse import urlparse

_SAFE_ENVIRONMENTS = frozenset({"local", "dev", "test"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeScriptError(RuntimeError):
    """Raised when a developer-only script targets an unsafe database."""


def _database_host(database_url: str) -> str | None:
    try:
        parsed = urlparse(database_url)
        _ = parsed.port  # validate the port as well as the hostname
        return parsed.hostname
    except ValueError as exc:
        raise UnsafeScriptError("database URL is not a valid URL") from exc


def guard_development_script(
    script: str,
    database_url: str,
    *,
    opt_in_env: str,
    allow_remote_env: str,
) -> None:
    """Reject production, non-explicit, or unexpectedly remote invocations."""
    environment = (os.environ.get("APP_ENVIRONMENT") or "local").strip().lower()
    if environment == "prod":
        raise UnsafeScriptError(
            f"{script} refuses APP_ENVIRONMENT=prod: developer fixtures must never "
            "write to production."
        )
    if environment not in _SAFE_ENVIRONMENTS:
        raise UnsafeScriptError(
            f"{script} refuses APP_ENVIRONMENT={environment!r}: expected local, dev, or test."
        )
    if os.environ.get(opt_in_env) != "1":
        raise UnsafeScriptError(f"{script} requires {opt_in_env}=1 as an explicit opt-in.")
    host = _database_host(database_url)
    if host is None:
        raise UnsafeScriptError(
            f"{script} refuses a database URL without a host; use an explicit loopback URL."
        )
    if host.lower() in _LOOPBACK_HOSTS:
        return
    if os.environ.get(allow_remote_env) != "1":
        raise UnsafeScriptError(
            f"{script} refuses non-loopback database host {host!r}; set "
            f"{allow_remote_env}=1 only for an isolated development database."
        )
