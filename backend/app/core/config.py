"""Application settings — environment-based, safe defaults.

All secrets arrive via environment variables; nothing secret is ever
defaulted, logged, or sent to the frontend.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    app_name: str = "muse-spark-backend"
    version: str = "0.1.0"
    environment: str = "local"  # local | dev | prod
    log_level: str = "INFO"

    # Local-first binding: loopback only unless explicitly overridden.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Contract version echoed by every domain response (see docs/contracts/domain-api.md).
    contract_version: str = "v1"

    # --- LLM provider (Stage 3). Secrets only via environment. ---
    # "mock" (default: fully offline) or "openai_compat".
    llm_provider: str = "mock"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0
    # Retries of idempotent-safe failures (429/5xx/network). Attempts total = 1 + max.
    llm_max_retries: int = 2
    llm_max_tokens: int | None = None

    # --- Execution API (Stage 5). ---
    # Local operator bearer secret. Absent = fail-closed: every endpoint
    # except /health answers 401. Never logged, never returned.
    operator_token: str | None = None
    # Hard ceiling for a single LLM call inside one execution.
    execution_max_timeout_s: float = 300.0
    # Recovery staleness threshold. Startup validation (below) guarantees it
    # strictly exceeds the worst-case lifetime of a live executor run.
    recovery_stale_minutes: int = 30
    # Per-statement DB timeout (ms). Bounds persist/compensate phases and
    # turns a hung row-lock wait in recovery into an error instead of a hang.
    db_statement_timeout_ms: int = 30000
    # Optional persistence wiring for the API process. Absent = execution
    # endpoints answer 500 (health stays dependency-free by design).
    database_url: str | None = None


def _worst_case_live_run_seconds(
    *,
    attempts: int,
    max_timeout_s: float,
    backoff_total_s: float,
    db_statements: int,
    statement_timeout_s: float,
    margin_s: float,
) -> float:
    total = (
        attempts * max_timeout_s + backoff_total_s + db_statements * statement_timeout_s + margin_s
    )
    return total


def validate_recovery_bound(settings: "Settings") -> None:
    """Fail fast if the staleness threshold cannot prove a stale row is dead.

    Bound components (all from code/config, no guesses):
    - LLM phase: (1 + llm_max_retries) attempts × server timeout cap, plus
      adapter backoff (cap 8.0s + 0.25s jitter per retry — mirrored from the
      adapter constants and pinned by test_recovery_bound.py).
    - DB phases: persist (~6 statements) + compensation (~3 statements),
      each bounded by statement_timeout.
    - Fixed 60s margin.
    """
    # NOTE: executor MAX_ATTEMPTS counts re-runs across requests, not attempts
    # inside one run — the in-run bound uses the adapter retry budget instead.
    backoff_total = settings.llm_max_retries * (8.0 + 0.25)
    db_statements = 6 + 3  # persist path + compensation path
    bound = _worst_case_live_run_seconds(
        attempts=settings.llm_max_retries + 1,
        max_timeout_s=settings.execution_max_timeout_s,
        backoff_total_s=backoff_total,
        db_statements=db_statements,
        statement_timeout_s=settings.db_statement_timeout_ms / 1000.0,
        margin_s=60.0,
    )
    threshold = settings.recovery_stale_minutes * 60.0
    if threshold <= bound:
        raise ValueError(
            f"recovery_stale_minutes={settings.recovery_stale_minutes} "
            f"({threshold:.0f}s) does not exceed worst-case live run "
            f"({bound:.0f}s); recovery could reset a live execution."
        )


def get_settings() -> Settings:
    return Settings()
