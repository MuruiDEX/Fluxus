"""Backup script regression tests (Stage 17): static + refusal paths only.

Pins deploy/backup.ps1 WITHOUT touching a real production database:
- refusal without -Yes (exit 1), missing file (exit 1), placeholders (exit 2);
- no secret VALUE ever appears in any failure output;
- unique timestamped names, no-overwrite rule, pg_restore --list verification
  step, and no destructive verbs are all present in the script source;
- backup never runs from preflight (no reference) and docs describe the flow.

A live dump/restore drill needs a scratch database and is NOT performed here.
"""

import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent.parent / "deploy"
REPO_ROOT = DEPLOY.parent
BACKUP = DEPLOY / "backup.ps1"
PREFLIGHT = DEPLOY / "preflight.ps1"
COMPOSE = DEPLOY / "docker-compose.prod.yml"
DOCS_DEPLOY = REPO_ROOT / "docs" / "deployment.md"

_PWSH = "powershell"


def _run(script_args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BACKUP)]
    return subprocess.run([*cmd, *script_args], capture_output=True, text=True, cwd=DEPLOY.parent)


def _fixture(name: str, body: str) -> Path:
    path = DEPLOY / name
    path.write_text(body, encoding="utf-8")
    return path


_VALID = (
    "DOMAIN=shop.test.invalid\n"
    "ACME_EMAIL=ops@shop.test.invalid\n"
    "PUBLIC_API_ORIGIN=https://shop.test.invalid\n"
    "POSTGRES_USER=muse\n"
    "POSTGRES_PASSWORD=check-only-fixture-password-0123456789abcdef\n"
    "POSTGRES_DB=muse\n"
    'APP_CORS_ORIGINS=["https://shop.test.invalid"]\n'
    "APP_LLM_PROVIDER=mock\n"
)


def test_backup_refuses_without_explicit_confirmation() -> None:
    fixture = _fixture("backup-test-noyes.env", _VALID)
    try:
        proc = _run(["-EnvFile", str(fixture)])
    finally:
        fixture.unlink(missing_ok=True)
    assert proc.returncode == 1
    assert "without -Yes" in (proc.stdout + proc.stderr)


def test_backup_fails_closed_on_missing_file() -> None:
    proc = _run(["-EnvFile", str(DEPLOY / "does-not-exist.env"), "-Yes"])
    assert proc.returncode == 1
    assert "prod.env not found" in (proc.stdout + proc.stderr)


def test_backup_rejects_template_placeholders() -> None:
    fixture = _fixture(
        "backup-test-placeholder.env",
        "POSTGRES_USER=muse\nPOSTGRES_DB=muse\nPOSTGRES_PASSWORD=CHANGE_ME_secret\n",
    )
    try:
        proc = _run(["-EnvFile", str(fixture), "-Yes"])
    finally:
        fixture.unlink(missing_ok=True)
    assert proc.returncode == 2
    assert "placeholder" in (proc.stdout + proc.stderr).lower()


def test_backup_never_prints_secret_values() -> None:
    """Failure output may name variables, never their secret contents."""
    secret_pw = "s3cr3t-pw-never-logged-9f8e7d6c5b4a"
    fixture = _fixture(
        "backup-test-leak.env",
        "POSTGRES_USER=muse\n"
        "POSTGRES_DB=muse\n"
        f"POSTGRES_PASSWORD={secret_pw}\n",
    )
    try:
        # No -Yes: the confirmation gate fires first; output must still be clean.
        proc = _run(["-EnvFile", str(fixture)])
    finally:
        fixture.unlink(missing_ok=True)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert secret_pw not in combined


def test_backup_source_safety_properties() -> None:
    """Static pins: unique names, no overwrite/prune, list-verify, no restore."""
    body = BACKUP.read_text(encoding="utf-8")
    assert "fluxus-prod-{0}-{1}.dump" in body, "timestamped unique filename required"
    assert "refusing to overwrite" in body.lower()
    assert "pg_restore --list" in body
    assert "does NOT prove a full restore" in body
    for verb in ("Remove-Item -LiteralPath $BackupDir", "restore live", "downgrade"):
        assert verb not in body, f"backup must not contain destructive path: {verb}"


def test_backup_not_wired_into_preflight_or_compose() -> None:
    """Backups never run automatically: no invocation from preflight or compose."""
    preflight = PREFLIGHT.read_text(encoding="utf-8").lower()
    compose = COMPOSE.read_text(encoding="utf-8").lower()
    assert "backup.ps1" not in preflight
    assert "backup.ps1" not in compose
    for verb in ("backup.ps1", "pg_dump", "pg_restore"):
        assert verb not in compose, f"compose must not invoke backups: {verb}"


def test_docs_cover_backup_flow_and_limits() -> None:
    body = DOCS_DEPLOY.read_text(encoding="utf-8")
    assert "deploy/backup.ps1" in body
    assert "-Yes" in body
    assert "pg_restore --list" in body
    assert "scratch" in body.lower()
