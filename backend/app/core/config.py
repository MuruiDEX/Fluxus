"""Application settings — environment-based, safe defaults.

All secrets arrive via environment variables; nothing secret is ever
defaulted, logged, or sent to the frontend.
"""

import math
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Recovery cutoff policy (Stage 18 S2.2.3). 40 minutes is the smallest whole
# minute above the verified lifecycle bound (1772s) plus the required
# 10-minute policy margin (2372s). See validate_recovery_bound().
_DEFAULT_RECOVERY_STALE_MINUTES = 40


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
    # DEPRECATED and ignored (Stage 18 S2.2.4.6). Kept only so existing
    # deployment configuration still parses; a non-zero value has no runtime
    # effect. Ambiguous holds are potentially billable and are never released
    # by age; resolution requires an explicitly authorized reconciliation.
    usage_ambiguous_release_minutes: int = 0

    # --- Execution API (Stage 5). ---
    # Hard ceiling for a single LLM call inside one execution.
    execution_max_timeout_s: float = 300.0
    # --- Execution ownership lease (Stage 18 S2.2.4.2). ---
    # A successful claim grants a lease that the heartbeat extends every
    # interval. The wall is the absolute upper bound: heartbeats clamp the
    # expiry to it and never move it. The verified worst-case executor
    # lifecycle is 1772s (see _lifecycle_bounds), so 2400s fits.
    execution_lease_ttl_s: float = 300.0
    execution_heartbeat_interval_s: float = 60.0
    execution_lease_wall_s: float = 2400.0
    # Recovery staleness threshold. Startup validation (below) requires it to
    # strictly exceed the worst-case live run plus _RECOVERY_CUTOFF_MARGIN_S.
    recovery_stale_minutes: int = _DEFAULT_RECOVERY_STALE_MINUTES
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

    @field_validator("db_statement_timeout_ms")
    @classmethod
    def _positive_db_statement_timeout(cls, value: int) -> int:
        # PostgreSQL treats 0 as "no statement timeout": a hung lock wait
        # would then be unbounded and the recovery cutoff could no longer be
        # proven safe. Negative values are invalid server settings.
        if value <= 0:
            raise ValueError(
                "db_statement_timeout_ms must be a positive number of milliseconds; "
                "0 disables PostgreSQL statement_timeout"
            )
        return value

    @field_validator("llm_timeout_s")
    @classmethod
    def _positive_llm_timeout(cls, value: float) -> float:
        # The adapter derives its total deadline from this value. Zero,
        # negative, NaN or infinity would make that deadline non-finite or
        # immediate, so the reservation lifetime could not be bounded.
        if not math.isfinite(value) or value <= 0:
            raise ValueError("llm_timeout_s must be a finite positive number of seconds")
        return value

    @field_validator("execution_max_timeout_s")
    @classmethod
    def _positive_execution_max_timeout(cls, value: float) -> float:
        # The execution API passes this as the per-call cap. Non-finite or
        # non-positive values would make the recovery bound unprovable.
        if not math.isfinite(value) or value <= 0:
            raise ValueError("execution_max_timeout_s must be a finite positive number of seconds")
        return value

    @field_validator(
        "execution_lease_ttl_s",
        "execution_heartbeat_interval_s",
        "execution_lease_wall_s",
    )
    @classmethod
    def _positive_lease_timing(cls, value: float) -> float:
        # Zero/negative or non-finite timings would make a lease unprovable
        # (or expire immediately) and break the heartbeat's hard bound.
        if not math.isfinite(value) or value <= 0:
            raise ValueError("lease timings must be finite positive numbers of seconds")
        return value

    @model_validator(mode="after")
    def _validate_lease_timing(self) -> "Settings":
        # Renewals must happen while the lease is still live, and the absolute
        # wall must outlast the TTL granted by a claim.
        if self.execution_heartbeat_interval_s >= self.execution_lease_ttl_s:
            raise ValueError(
                "execution_heartbeat_interval_s must be strictly less than execution_lease_ttl_s"
            )
        if self.execution_lease_wall_s < self.execution_lease_ttl_s:
            raise ValueError(
                "execution_lease_wall_s must be greater than or equal to execution_lease_ttl_s"
            )
        return self

    @field_validator("llm_max_retries")
    @classmethod
    def _non_negative_retries(cls, value: int) -> int:
        # 0 is valid (no retries). A negative count is ambiguous and would
        # silently shrink the retry/backoff term of the recovery bound.
        if value < 0:
            raise ValueError("llm_max_retries must be zero or greater")
        return value

    @field_validator("recovery_stale_minutes")
    @classmethod
    def _positive_recovery_stale_minutes(cls, value: int) -> int:
        # A non-positive cutoff can never exceed a live run's lifetime, so
        # recovery would reset rows that may still be executing.
        if value <= 0:
            raise ValueError("recovery_stale_minutes must be a positive number of minutes")
        return value


# --- Verified lifecycle bounds (Stage 18 S2.2.2) ---
# Statement counts are the maximum number of SQL statements that can run
# before the reservation settles, traced from the production call paths:
# - Team Lead reserve: business/project read, FOR UPDATE refresh, lock,
#   supersede UPDATE, three aggregates, run_seq SELECT, INSERT.
# - Business settlement: ledger SELECT/SAVEPOINT/INSERT/RELEASE + reservation
#   UPDATE. Project settlement additionally recomputes aiSpent (4 statements).
# - Executor claim+reserve: claim UPDATE, two project locks, supersede, three
#   aggregates, run_seq, INSERT. Persist: task read/update, activity INSERT,
#   ledger SELECT/SAVEPOINT/INSERT/RELEASE, reservation UPDATE, task-cost
#   recompute (3), project-spend recompute (4).
# SAVEPOINT/RELEASE are separate statements. COMMIT is deliberately NOT
# counted: statement_timeout does not bound it.
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0
_BACKOFF_JITTER_MAX_S = 0.25
# SQLAlchemy QueuePool default; pinned against the real engine by
# test_recovery_bound.py. One acquisition per transaction/session.
_DB_POOL_TIMEOUT_S = 30.0
# Fixed allowance inside each lifecycle bound for local overhead.
_RECOVERY_MARGIN_S = 60.0
# Additional policy margin between the verified lifecycle bound and the
# recovery cutoff. It is a time-based cushion for the operations that
# statement_timeout does NOT bound (COMMIT, network stalls), not a guarantee.
_RECOVERY_CUTOFF_MARGIN_S = 600.0
_TEAMLEAD_MAX_GENERATIONS = 2
_TEAMLEAD_RESERVE_STATEMENTS = 9
# Business settlement: ledger SELECT/SAVEPOINT/INSERT/RELEASE + reservation
# UPDATE. On the unique-key conflict branch, RELEASE becomes ROLLBACK TO
# SAVEPOINT and an extra SELECT resolves the winner (one more statement).
_TEAMLEAD_BUSINESS_SETTLE_STATEMENTS = 6
# Project settlement adds aiSpent recomputation (lock SELECT, two SUMs, UPDATE).
_TEAMLEAD_PROJECT_SETTLE_STATEMENTS = 10
_EXECUTOR_CLAIM_RESERVE_STATEMENTS = 9
# Persist: 16 statements on the normal path, 17 when the ledger insert hits
# the unique-key race and resolves it with ROLLBACK TO SAVEPOINT + SELECT.
_EXECUTOR_PERSIST_STATEMENTS = 17


def _provider_deadline_seconds(timeout_s: float, max_retries: int) -> float:
    """Mirror the adapter's S2.1 total deadline, including backoff."""
    retries = max(0, max_retries)
    backoff = sum(
        min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**index)) + _BACKOFF_JITTER_MAX_S
        for index in range(retries)
    )
    return (1 + retries) * timeout_s + backoff


def _lifecycle_bounds(settings: "Settings") -> dict[str, float]:
    """Return the verified worst-case lifecycle of every production path.

    Each bound sums the provider's total S2.1 deadline (which already
    contains adapter retries and backoff), every SQL statement executed
    under statement_timeout, one pool acquisition per transaction, and the
    fixed recovery margin. Mutually exclusive branches use the longest one;
    sequential generations are summed.
    """
    statement_s = settings.db_statement_timeout_ms / 1000.0
    provider_teamlead = _provider_deadline_seconds(settings.llm_timeout_s, settings.llm_max_retries)
    provider_executor = _provider_deadline_seconds(
        settings.execution_max_timeout_s, settings.llm_max_retries
    )
    # Reserve and settlement use separate sessions, so each generation can
    # wait once for a pool connection in each transaction.
    business_generation = (
        provider_teamlead
        + (_TEAMLEAD_RESERVE_STATEMENTS + _TEAMLEAD_BUSINESS_SETTLE_STATEMENTS) * statement_s
        + 2 * _DB_POOL_TIMEOUT_S
    )
    project_generation = (
        provider_teamlead
        + (_TEAMLEAD_RESERVE_STATEMENTS + _TEAMLEAD_PROJECT_SETTLE_STATEMENTS) * statement_s
        + 2 * _DB_POOL_TIMEOUT_S
    )
    teamlead_business = _TEAMLEAD_MAX_GENERATIONS * business_generation + _RECOVERY_MARGIN_S
    teamlead_project = _TEAMLEAD_MAX_GENERATIONS * project_generation + _RECOVERY_MARGIN_S
    # The executor owns one session for the whole run: one pool acquisition.
    executor = (
        provider_executor
        + (_EXECUTOR_CLAIM_RESERVE_STATEMENTS + _EXECUTOR_PERSIST_STATEMENTS) * statement_s
        + _DB_POOL_TIMEOUT_S
        + _RECOVERY_MARGIN_S
    )
    return {
        "teamlead_questions": teamlead_business,
        "teamlead_summary": teamlead_business,
        "teamlead_planning": teamlead_business,
        "teamlead_finalize": teamlead_project,
        "executor": executor,
    }


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

    Checks a path-accurate bound for every production lifecycle:
    - Team Lead questions/summary/planning (business reservation, 2 generations)
    - Team Lead finalize (project reservation + aiSpent recompute, 2 generations)
    - Executor (claim+reserve, provider, persist; the longest branch)

    Components per path: provider S2.1 deadline (retries and backoff already
    included), statements × db_statement_timeout_ms, one pool acquisition per
    transaction, and the fixed margin. COMMIT and network stalls are outside
    statement_timeout and are not claimed as bounded.

    Policy (S2.2.3): the cutoff must strictly exceed the longest path by at
    least _RECOVERY_CUTOFF_MARGIN_S. That margin is an explicit time cushion
    for the unbounded operations above; it does NOT make time-only recovery a
    proof against an arbitrarily long live operation (that needs a lease).
    """
    bounds = _lifecycle_bounds(settings)
    path, bound = max(bounds.items(), key=lambda item: item[1])
    threshold = settings.recovery_stale_minutes * 60.0
    margin = threshold - bound
    required = bound + _RECOVERY_CUTOFF_MARGIN_S
    if threshold <= required:
        raise ValueError(
            f"recovery_stale_minutes={settings.recovery_stale_minutes} "
            f"({threshold:.0f}s) does not exceed worst-case live run "
            f"({path} bound {bound:.0f}s) plus the "
            f"{_RECOVERY_CUTOFF_MARGIN_S:.0f}s safety margin "
            f"(required > {required:.0f}s; available margin {margin:.0f}s); "
            f"recovery could reset a live execution."
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
