"""Embedded PostgreSQL for local dev/test — fallback when Docker is unavailable.

Real PostgreSQL engine (same SQL semantics: CHECK, FK, FOR UPDATE), zero
system install: binaries come from the `postgresql-binaries` pip package,
data lives in `backend/.pgdata-5433` (gitignored). If a server already
listens on 127.0.0.1:5433 (e.g. Docker Compose `postgres-test`), it is used
as-is and nothing is started. Dev/test tooling only — never production.
"""

import socket
import subprocess
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PGDATA = BACKEND_DIR / ".pgdata-5433"
HOST = "127.0.0.1"
PORT = 5433
DATABASES = ("muse_test", "muse_mig")


def _bin(name: str) -> str:
    from postgresql_binaries import bin as pg_bin

    exe = pg_bin() / (name + ".exe")
    if not exe.exists():
        exe = pg_bin() / name
    return str(exe)


def _port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((HOST, PORT))
            return True
        except OSError:
            return False


def _run(*args: str, env_extra: dict | None = None) -> None:
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    subprocess.run(list(args), check=True, capture_output=True, text=True, env=env)


def ensure_postgres(timeout_s: float = 90.0) -> str:
    """Return a libpq-style base URL; start embedded server if nothing listens."""
    if _port_open():
        return f"postgresql://muse:muse@{HOST}:{PORT}"
    PGDATA.mkdir(parents=True, exist_ok=True)
    if not (PGDATA / "PG_VERSION").exists():
        # UTF8 + C locale: template DBs must accept any Unicode the demo
        # data contains (e.g. "Kraków"); OS-locale defaults (WIN1251) do not.
        _run(
            _bin("initdb"),
            "-D",
            str(PGDATA),
            "-U",
            "muse",
            "--auth=trust",
            "-E",
            "UTF8",
            "--locale=C",
        )
    with open(PGDATA / "postgresql.conf", "a", encoding="utf-8") as fh:
        fh.write("\nlisten_addresses = '127.0.0.1'\nport = 5433\n")
    _run(_bin("pg_ctl"), "-D", str(PGDATA), "-l", str(PGDATA / "server.log"), "start")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _port_open():
            break
        time.sleep(0.5)
    if not _port_open():
        raise RuntimeError("embedded postgres did not become ready")
    _run(
        _bin("psql"),
        "-h",
        HOST,
        "-p",
        str(PORT),
        "-U",
        "muse",
        "-d",
        "postgres",
        "-c",
        "ALTER USER muse PASSWORD 'muse';",
    )
    for db in DATABASES:
        probe = subprocess.run(
            [
                _bin("psql"),
                "-h",
                HOST,
                "-p",
                str(PORT),
                "-U",
                "muse",
                "-d",
                "postgres",
                "-tAc",
                f"SELECT 1 FROM pg_database WHERE datname='{db}'",
            ],
            capture_output=True,
            text=True,
        )
        if probe.stdout.strip() != "1":
            _run(_bin("createdb"), "-h", HOST, "-p", str(PORT), "-U", "muse", db)
    return f"postgresql://muse:muse@{HOST}:{PORT}"
