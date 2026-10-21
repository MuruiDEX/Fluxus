"""Preflight regression tests (Stage 17): static checks, no Docker, no ports.

Pins deploy/preflight.ps1 + deploy/docker-compose.prod.yml WITHOUT touching
a real domain or production credentials:
- every ${VAR:?...}/$VAR reference in compose has a template entry AND the
  preflight Required list covers every hard-required name;
- deploy/prod.env (real secrets) is absent from the checkout AND gitignored;
- preflight never echoes a secret VALUE in any failure fixture output;
- exit codes stay fail-closed: missing file (1), config/placeholder (2).

Ports/Docker probes are exercised by the `-Skip*` matrix in CI operators'
hands, not here — this suite must pass on any dev box with Python only.
"""

import re
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent.parent / "deploy"
REPO_ROOT = DEPLOY.parent
PREFLIGHT = DEPLOY / "preflight.ps1"
COMPOSE = DEPLOY / "docker-compose.prod.yml"
EXAMPLE = DEPLOY / "prod.env.example"

_PWSH = "powershell"


def _run(script_args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PREFLIGHT)]
    return subprocess.run([*cmd, *script_args], capture_output=True, text=True, cwd=DEPLOY.parent)


def _required_names() -> list[str]:
    body = PREFLIGHT.read_text(encoding="utf-8")
    match = re.search(r"\$Required = @\((.*?)\)", body, re.S)
    assert match, "preflight must declare a $Required list"
    return re.findall(r"'([A-Z_0-9]+)'", match.group(1))


def _compose_hard_required() -> set[str]:
    body = COMPOSE.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{([A-Z_0-9]+):\?[^}]*\}", body))


def _example_keys() -> set[str]:
    keys = set()
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, _ = line.partition("=")
        keys.add(key.strip())
    return keys


def test_compose_required_vars_are_template_and_preflight_covered() -> None:
    """Every compose hard-require has a template entry and a preflight check."""
    hard = _compose_hard_required()
    assert hard, "compose must hard-require at least one variable"
    missing_template = sorted(hard - _example_keys())
    missing_preflight = sorted(hard - set(_required_names()))
    assert not missing_template, f"template misses: {missing_template}"
    assert not missing_preflight, f"preflight misses: {missing_preflight}"


def test_real_prod_env_absent_and_gitignored() -> None:
    """Real secrets must never sit in the checkout; .gitignore must cover them."""
    assert not (DEPLOY / "prod.env").exists(), "deploy/prod.env must not exist in the checkout"
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env*" in gitignore


def test_preflight_fails_closed_on_missing_file() -> None:
    missing = str(DEPLOY / "does-not-exist.env")
    proc = _run(["-EnvFile", missing, "-SkipPortCheck", "-SkipDockerCheck"])
    assert proc.returncode == 1
    assert "prod.env not found" in (proc.stdout + proc.stderr)


def _fixture(name: str, body: str) -> Path:
    path = DEPLOY / name
    path.write_text(body, encoding="utf-8")
    return path


def test_preflight_rejects_template_placeholders() -> None:
    fixture = _fixture(
        "preflight-test-placeholder.env",
        "DOMAIN=app.example.com\n"
        "ACME_EMAIL=ops@example.com\n"
        "PUBLIC_API_ORIGIN=https://app.example.com\n"
        "POSTGRES_PASSWORD=CHANGE_ME_secret\n"
        "APP_DATABASE_URL=postgresql+asyncpg://muse:CHANGE_ME_secret@postgres:5432/muse\n"
        'APP_CORS_ORIGINS=["https://app.example.com"]\n'
        "APP_LLM_PROVIDER=mock\n",
    )
    try:
        proc = _run(["-EnvFile", str(fixture), "-SkipPortCheck", "-SkipDockerCheck"])
    finally:
        fixture.unlink(missing_ok=True)
    assert proc.returncode == 2
    assert "placeholder" in (proc.stdout + proc.stderr).lower()


def test_preflight_never_prints_secret_values() -> None:
    """Failure output may name variables, never their secret contents."""
    secret_pw = "s3cr3t-pw-never-logged-9f8e7d6c5b4a"
    secret_db = f"postgresql+asyncpg://muse:{secret_pw}@postgres:5432/muse"
    fixture = _fixture(
        "preflight-test-leak.env",
        "DOMAIN=shop.test.invalid\n"
        "ACME_EMAIL=ops@shop.test.invalid\n"
        "PUBLIC_API_ORIGIN=https://shop.test.invalid\n"
        f"POSTGRES_PASSWORD={secret_pw}\n"
        f"APP_DATABASE_URL=postgresql+asyncpg://muse:wrong-password-not-the-secret@postgres:5432/muse\n"
        'APP_CORS_ORIGINS=["https://other.test.invalid"]\n'  # mismatch on purpose
        "APP_LLM_PROVIDER=mock\n",
    )
    try:
        proc = _run(["-EnvFile", str(fixture), "-SkipPortCheck", "-SkipDockerCheck"])
    finally:
        fixture.unlink(missing_ok=True)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2
    assert secret_pw not in combined
    assert secret_db not in combined
    assert "POSTGRES_PASSWORD" in combined  # names are fine, values are not


def _git_check_ignore(path: str) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def test_gitignore_protects_production_secrets_and_backups() -> None:
    assert _git_check_ignore("deploy/prod.env"), "deploy/prod.env must be gitignored"
    assert _git_check_ignore("deploy/backups/any.dump"), "backup directory must be gitignored"
    assert _git_check_ignore("deploy/some.dump"), "dump archives must be gitignored"
    assert not _git_check_ignore("deploy/prod.env.example"), "the template must stay tracked"


def test_backend_image_binds_container_interface_not_loopback() -> None:
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert '"--host", "0.0.0.0"' in dockerfile
    assert '"--host", "127.0.0.1"' not in dockerfile
    assert '"--workers", "1"' in dockerfile


def test_prod_compose_publishes_only_caddy_and_keeps_db_internal() -> None:
    body = COMPOSE.read_text(encoding="utf-8")
    assert re.findall(r"(?m)^\s*ports:\s*$", body) == ["    ports:"], "only Caddy may publish ports"
    assert 'internal: true' in body
    assert 'condition: service_completed_successfully' in body
    assert 'condition: service_healthy' in body
    assert "APP_SESSION_COOKIE_SECURE" in body


def test_preflight_allows_skip_flags() -> None:
    """Skip flags keep the config gate while bypassing environment probes."""
    fixture = _fixture(
        "preflight-test-skip.env",
        "DOMAIN=shop.test.invalid\n"
        "ACME_EMAIL=ops@shop.test.invalid\n"
        "PUBLIC_API_ORIGIN=https://shop.test.invalid\n"
        "POSTGRES_PASSWORD=check-only-fixture-password-0123456789abcdef\n"
        "APP_DATABASE_URL=postgresql+asyncpg://muse:check-only-fixture-password-0123456789abcdef@postgres:5432/muse\n"
        'APP_CORS_ORIGINS=["https://shop.test.invalid"]\n'
        "APP_LLM_PROVIDER=mock\n",
    )
    try:
        proc = _run(["-EnvFile", str(fixture), "-SkipPortCheck", "-SkipDockerCheck"])
    finally:
        fixture.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped" in (proc.stdout + proc.stderr).lower()


if __name__ == "__main__":
    sys.exit(f"run with: pytest {Path(__file__).name} -q")
