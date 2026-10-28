"""Embedded PostgreSQL startup is retired.

The old script targeted the protected ``backend/.pgdata-5433`` cluster.  It is
kept only as a fail-closed compatibility stub so existing references explain
the replacement instead of failing with a missing internal helper.

Use the opt-in isolated Compose service instead::

    docker compose --profile test-isolated up -d postgres-test-isolated

Then pass an explicit ``--pg-base-url`` for ``127.0.0.1:55434`` to pytest.
Ports 5432, 5433 and 55433 are protected.
"""

import sys

_MESSAGE = (
    "error: automatic embedded PostgreSQL startup is disabled. "
    "Use the opt-in isolated service: "
    "docker compose --profile test-isolated up -d postgres-test-isolated. "
    "Then run pytest with --pg-base-url for 127.0.0.1:55434. "
    "Protected ports 5432, 5433 and 55433 are rejected.\n"
)


def main() -> int:
    """Refuse to start or attach to the old protected cluster."""
    sys.stderr.write(_MESSAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
