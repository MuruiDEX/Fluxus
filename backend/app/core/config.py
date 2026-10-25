"""Application settings — environment-based, safe defaults.

All secrets arrive via environment variables; nothing secret is ever
defaulted, logged, or sent to the frontend.
"""

from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    app_name: str = "muse-spark-backend"
    version: str = "0.1.0"
    # Reject typo'd environments instead of silently bypassing the prod guard.
    environment: Literal["local", "dev", "test", "prod"] = "local"
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
    # Retries of idempotent-safe failures (429 and pre-send network failures).
    # 5xx responses are treated as ambiguous/possibly billed and are NOT
    # retried. Attempts total = 1 + max.
    llm_max_retries: int = 2
    # Hard cap on completion tokens for every call. Stage 16 turns this into a
    # real request field AND the output bound for conservative budget holds.
    llm_max_tokens: int | None = 1024
    # Wire field that carries `llm_max_tokens` / `team_lead_max_tokens`.
    # Most OpenAI-compatible endpoints (gpt-4o-mini, Ollama, typical gateways)
    # accept `max_tokens`; some newer OpenAI reasoning models reject it and
    # require `max_completion_tokens`. Only the selected field is sent, and the
    # value is unchanged, so the budget reservation still prices the same limit.
    llm_max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    # Output-token cap for the Team Lead calls (questions / summary / plan /
    # finalization). It is BOTH the limit sent to the provider AND the output
    # bound the budget reservation is priced from, so a Team Lead reservation
    # uses a conservative bound (under provider/tokenizer assumptions) rather
    # than an unstated assumption.
    #
    # Budget impact: one Team Lead call reserves at most
    #   input_bound*input_rate + team_lead_max_tokens*output_rate
    # and that hold is released to the settled cost on success. A project whose
    # remaining `ai_budget` is smaller than that bound is refused with
    # `budget_exceeded` BEFORE the provider is called (fail closed).
    # `None` disables the cap entirely: no conservative bound exists, so
    # budgeted projects fail closed with `budget_unpriced` (Stage 18 S1).
    team_lead_max_tokens: int | None = 2048

    # --- Usage accounting & budget enforcement (Stage 16). ---
    # Operator-supplied tariff catalog (JSON array). Empty = no verified prices,
    # so unpriced calls are recorded as `unknown` (fail-closed in strict mode).
    usage_tariffs_json: str | None = None
    # Internal reserve amount for allow_unpriced_ai projects. NOT a proven
    # maximum provider cost — the bill may exceed it (best-effort mode).
    usage_unpriced_reserve_amount: float = 1.0
    # Automatic release of aged `ambiguous` reservations after N minutes.
    # 0 (default) disables time-based release: ambiguous holds never auto-clear.
    usage_ambiguous_release_minutes: int = 0

    # --- Execution API (Stage 5). ---
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

    # --- Authentication (Stage 6). ---
    # Session lifetime and login throttle. Cookie `Secure` flag follows
    # deployment scheme (see api/v1/auth.py — never enabled silently).
    session_ttl_seconds: int = 12 * 3600
    session_cookie_name: str = "bos_session"
    session_cookie_secure: bool = False
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # --- CORS allowlist (Stage 12: env-configurable for deployments). ---
    # Default is the local-first set (Next dev on both loopback spellings +
    # Tauri schemes). Production MUST override with its exact origins as a
    # JSON array — never "*" with credentials (browsers reject it anyway).
    # Example: APP_CORS_ORIGINS='["https://app.example.com"]'
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "tauri://localhost",
        "http://tauri.localhost",
    ]

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        # "*" with allow_credentials=True is rejected by browsers AND would
        # let any site read authenticated responses if it ever worked.
        if "*" in value:
            raise ValueError('cors_origins must list exact origins, never "*"')
        return value

    @field_validator("llm_max_tokens")
    @classmethod
    def _positive_executor_cap(cls, value: int | None) -> int | None:
        # Zero/negative would be sent on the wire while the pricing bound
        # clamped output to zero, silently under-reserving the call.
        if value is not None and value <= 0:
            raise ValueError("llm_max_tokens must be a positive token count or None")
        return value

    @field_validator("team_lead_max_tokens")
    @classmethod
    def _positive_team_lead_cap(cls, value: int | None) -> int | None:
        # A cap of zero (or negative) would make every Team Lead call
        # unprovable and silently break the budget reservation bound.
        if value is not None and value <= 0:
            raise ValueError("team_lead_max_tokens must be a positive token count or None")
        return value


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


def _production_origin_problem(origin: str) -> str | None:
    """Return why an exact CORS origin is unsafe for production, if at all."""
    if not origin or origin != origin.strip():
        return "the origin must be non-empty with no surrounding whitespace"
    try:
        parsed = urlparse(origin)
        _ = parsed.port  # validate the port alongside the hostname
        host = parsed.hostname
    except ValueError:
        return "the origin is not a valid URL"
    if parsed.scheme != "https":
        return "the origin must use https"
    if not host:
        return "the origin must include a host"
    if parsed.username or parsed.password:
        return "the origin must not contain credentials"
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        return "the origin must not contain a path, query, fragment, or trailing slash"
    host = host.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        return "the origin must not point at loopback or a local-only host"
    return None


def validate_production_config(settings: "Settings") -> None:
    """Fail fast on production misconfiguration (Stage 12 hardening).

    Local/dev/test are untouched — this runs only when
    environment == "prod". Production without a database serves 500s from
    every domain endpoint, a non-Secure session cookie over HTTPS is either
    dropped or exposed, and the localhost CORS defaults would both reject
    the real frontend and trust development-only origins. All must be
    explicit operator decisions, never silent defaults.
    """
    if settings.environment != "prod":
        return
    if not settings.database_url:
        raise ValueError(
            "APP_DATABASE_URL is required when APP_ENVIRONMENT=prod: "
            "serving without persistence turns every domain endpoint into a 500."
        )
    if not settings.session_cookie_secure:
        raise ValueError(
            "APP_SESSION_COOKIE_SECURE=true is required when APP_ENVIRONMENT=prod: "
            "session cookies must not travel over plain HTTP in production."
        )
    if not settings.cors_origins:
        raise ValueError(
            "APP_CORS_ORIGINS must list at least one exact https origin when "
            "APP_ENVIRONMENT=prod (for example '[\"https://app.example.com\"]')."
        )
    for index, origin in enumerate(settings.cors_origins):
        problem = _production_origin_problem(origin)
        if problem is not None:
            raise ValueError(
                f"APP_CORS_ORIGINS[{index}] is invalid when APP_ENVIRONMENT=prod: {problem}."
            )
