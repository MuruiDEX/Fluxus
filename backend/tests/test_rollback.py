"""Rollback script tests (Stage 17): refusal paths and static safety pins.

No Docker, no database, and no live deployment are touched. The script's
dangerous boundary is the database: these tests prove it refuses to act
without explicit inputs and that it never contains an automatic restore,
drop, or downgrade path.
"""

import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent.parent / "deploy"
REPO_ROOT = DEPLOY.parent
ROLLBACK = DEPLOY / "rollback.ps1"
DOCS_DEPLOY = REPO_ROOT / "docs" / "deployment.md"

_PWSH = "powershell"


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROLLBACK), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=timeout,
    )


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / "prod.env"
    path.write_text(
        "DOMAIN=shop.test.invalid\n"
        "ACME_EMAIL=ops@shop.test.invalid\n"
        "PUBLIC_API_ORIGIN=https://shop.test.invalid\n"
        "POSTGRES_USER=muse\n"
        "POSTGRES_PASSWORD=check-only-fixture-password-0123456789abcdef\n"
        "POSTGRES_DB=muse\n"
        "APP_DATABASE_URL=postgresql+asyncpg://muse:check-only-fixture-password-0123456789abcdef@postgres:5432/muse\n"
        'APP_CORS_ORIGINS=["https://shop.test.invalid"]\n'
        "APP_LLM_PROVIDER=mock\n",
        encoding="utf-8",
    )
    return path


def _backup_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.dump"
    path.write_bytes(b"not-a-real-archive-but-non-empty")
    return path


def test_rollback_refuses_without_confirmation() -> None:
    proc = _run([])
    assert proc.returncode == 1
    assert "without -Yes" in (proc.stdout + proc.stderr)


def test_rollback_refuses_missing_env_file(tmp_path: Path) -> None:
    proc = _run(
        [
            "-Yes",
            "-EnvFile",
            str(tmp_path / "missing.env"),
            "-BackupFile",
            str(_backup_file(tmp_path)),
            "-PreviousApiImage",
            "fluxus-api:previous",
            "-PreviousWebImage",
            "fluxus-web:previous",
        ]
    )
    assert proc.returncode == 1
    assert "prod.env not found" in (proc.stdout + proc.stderr)


def test_rollback_refuses_missing_backup(tmp_path: Path) -> None:
    proc = _run(
        [
            "-Yes",
            "-EnvFile",
            str(_env_file(tmp_path)),
            "-BackupFile",
            str(tmp_path / "missing.dump"),
            "-PreviousApiImage",
            "fluxus-api:previous",
            "-PreviousWebImage",
            "fluxus-web:previous",
        ]
    )
    assert proc.returncode == 1
    assert "backup file" in (proc.stdout + proc.stderr).lower()


def test_rollback_refuses_missing_images(tmp_path: Path) -> None:
    proc = _run(
        [
            "-Yes",
            "-EnvFile",
            str(_env_file(tmp_path)),
            "-BackupFile",
            str(_backup_file(tmp_path)),
        ]
    )
    assert proc.returncode == 2
    assert "PreviousApiImage" in (proc.stdout + proc.stderr)


def test_rollback_refuses_latest_and_current_tags(tmp_path: Path) -> None:
    env = str(_env_file(tmp_path))
    backup = str(_backup_file(tmp_path))
    cases = [
        ("fluxus-api:latest", "fluxus-web:previous"),
        ("fluxus-api:prod", "fluxus-web:previous"),
        ("fluxus-api:previous", "fluxus-web:live-prod"),
        ("fluxus-api:previous", "fluxus-api:previous"),
        ("fluxus-api:bad image", "fluxus-web:previous"),
    ]
    for api, web in cases:
        proc = _run(
            [
                "-Yes",
                "-EnvFile",
                env,
                "-BackupFile",
                backup,
                "-PreviousApiImage",
                api,
                "-PreviousWebImage",
                web,
            ]
        )
        assert proc.returncode == 2, (api, web, proc.stdout, proc.stderr)


def test_rollback_source_has_no_automatic_database_destruction() -> None:
    body = ROLLBACK.read_text(encoding="utf-8")
    assert "-Yes" in body
    assert "PreviousApiImage" in body
    assert "PreviousWebImage" in body
    assert "BackupFile" in body
    assert "pg_restore --list" in body
    assert "MANUAL AND NOT PERFORMED" in body

    lowered = body.lower()
    for forbidden in ("alembic downgrade", "--clean", "--create", "drop database", "dropdb"):
        assert forbidden not in lowered, f"rollback must not contain {forbidden!r}"

    # The only pg_restore invocation may be the read-only archive listing.
    for line in body.splitlines():
        if "pg_restore" in line:
            assert "--list" in line, f"unexpected pg_restore usage: {line.strip()}"


def test_rollback_docs_match_script_limits() -> None:
    body = DOCS_DEPLOY.read_text(encoding="utf-8")
    assert "deploy/rollback.ps1" in body
    assert "PreviousApiImage" in body
    assert "PreviousWebImage" in body
    assert "BackupFile" in body
    assert "never" in body.lower()
    assert "manual" in body.lower()
