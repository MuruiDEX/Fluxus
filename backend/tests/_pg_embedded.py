"""Embedded PostgreSQL startup is disabled for tests.

The former fallback targeted protected port 5433 and ``backend/.pgdata-5433``.
Tests must select an isolated server explicitly with ``--pg-base-url`` using a
numeric loopback host on a non-protected port. This module deliberately fails
closed so a listener on 5433 can never be adopted as a trusted test server.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PGDATA = BACKEND_DIR / ".pgdata-5433"
PORT = 5433


def ensure_postgres(timeout_s: float = 90.0, *, allow_existing_pgdata: bool = False) -> str:
    """Refuse automatic embedded startup; tests must use an explicit URL."""
    raise RuntimeError(
        "Refusing automatic embedded PostgreSQL startup because it targets protected "
        f"port {PORT}. Pass --pg-base-url with an isolated numeric loopback test server; "
        "protected ports 5432, 5433 and 55433 are never used."
    )
