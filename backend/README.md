# Backend — FastAPI foundation (Stage 1) + persistence (Stage 2) + LLM layer (Stage 3)

> Stage 2 implemented: async SQLAlchemy 2.0 persistence on PostgreSQL.
> Still absent (Stages 3+): LLM layer, orchestration port, CRUD endpoints,
> auth, frontend integration.

## What exists now

- `app/main.py` — `create_app()` factory, loopback-first config, CORS allowlist
  (local Next dev + Tauri schemes), trace-ID middleware, `/api/v1/*` router,
  contract error envelope for every failure, catch-all 404 in envelope shape.
- `app/api/v1/health.py` — `GET /api/v1/health` (no dependencies by design).
- `app/core/` — `config.py` (pydantic-settings, `APP_*` env), `logging.py`
  (JSON lines, never logs secrets), `errors.py` (envelope + handlers).
- `tests/` — pytest over `TestClient` (in-process, no network): health shape,
  404 envelope, trace-ID header.

## Setup / run / verify (Windows PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"

# databases: Docker Compose (primary) or embedded fallback (see below)
docker compose up -d postgres-test
# --- or, without Docker: pytest auto-starts embedded PostgreSQL 16 ---

.\.venv\Scripts\alembic upgrade head      # dev DB schema
.\.venv\Scripts\python scripts\seed_demo.py  # demo data (dev only)

.\.venv\Scripts\python -m pytest -q        # unit/API tests
.\.venv\Scripts\ruff check app tests alembic scripts   # lint
.\.venv\Scripts\ruff format --check app tests alembic scripts

# run locally (loopback only — see Security)
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Persistence (Stage 2)

- `app/persistence/`: `base` (mixins, portable types only), `enums`
  (contract literals + `literal_check`), `models/` (9 tables; no `users`,
  no `llm_usage` — deferred to Stages 6/4, see below),
  `repositories/` (scoped CRUD + reference validation + `spend.py` —
  the single legal `aiSpent` recompute path, contract §5.1).
- Write pattern (enforced by tests): one aggregate per flush, sequenced
  explicitly; repositories validate `business_id` scope and all cross-entity
  references (`DanglingReferenceError` → future 422). Activity log is
  append-only (no update/delete API).
- Migrations: `alembic/` + `0001 initial persistence` (hand-reviewed after
  autogenerate; `use_alter` cycle tasks↔approvals split into explicit
  create/drop constraint). `alembic upgrade head` / `downgrade -1` covered
  by tests on an isolated schema.
- Databases: `docker-compose.yml` — `postgres` (:5432, dev) and
  `postgres-test` (:5433, `muse_test` + `muse_mig` via init script).
  Without Docker, pytest auto-starts embedded PostgreSQL 16 binaries
  (`postgresql-binaries==16.15.0`, dev-only) on :5433 — same engine,
  zero system install. PG 18.x binaries are broken in this Windows
  environment (shared-memory reservation failures) and are pinned out.
  Helpers: `scripts/pg_start.py` (start embedded server),
  `scripts/pg_ensure_dbs.py` (create test databases).
- SQLite is a portability subset only (`test_portability_sqlite.py`:
  CHECK/FK/rollback/round-trips); PostgreSQL semantics are never weakened
  for it (row-locks, concurrency, migrations are PG-only tests).
- Dev seed: `scripts/seed_demo.py` (idempotent, Northstar subset, demo only).

## Security model (local single-user phase)

- Binds `127.0.0.1` by default; never `0.0.0.0` unless explicitly configured.
- CORS: exact-origin allowlist only (`localhost:3000`, Tauri schemes).
  No wildcard, no credentials+wildcard combination.
- Every response carries `X-Trace-Id`; every error follows the contract
  envelope with no internals (no tracebacks/SQL/paths in `message`).
- Secrets arrive via environment (`APP_*`) — none are defaulted, logged,
  or exposed to the frontend/desktop renderer. Frontend keeps zero secrets.
- Input validation is Pydantic-first; unknown enum literals and dangling
  cross-entity references are rejected per the contract (Stages 2+).

## Persistence decision (SQLite vs PostgreSQL)

Decision stands: **PostgreSQL** (SQLAlchemy 2.0 + Alembic, Stage 2), developed
against a local Postgres (Docker) and tested on an isolated database.

Desktop constraint (no Postgres/Docker install for the end user) stays an
open distribution question for Stage 2/7 — candidates: embedded Postgres
sidecar vs. file-backed engine behind the SAME repository interfaces.
The repository layer MUST abstract the engine so neither the API nor the
frontend can tell which one is underneath. No decision by code yet.

## Deferred models (deliberately absent in Stage 2)

- `users` — not needed without auth (Stage 6). Schema already reserves the
  concept via `business_id` scoping; no placeholder table.
- `llm_usage` — not needed without LLM calls (Stage 3/4). Spend tracking for
  AI runs lands with the provider layer. No billing code exists anywhere.

## LLM provider layer (Stage 3)

- `app/llm/`: `types.py` (requests, structured output, usage, error taxonomy),
  `base.py` (Protocol + `create_provider` factory), `mock.py` (scripted,
  fully offline — the default), `adapters/openai_compat.py` (raw httpx, no
  vendor SDK; same adapter later covers Azure/OpenRouter/Ollama via base_url),
  `validate.py` (JSON-Schema subset validator — compliance never claimed
  without local validation).
- Rules: `asyncio.CancelledError` propagates unwrapped and is never retried
  (streams/clients close in `finally`); `json_schema` first with `json_object`
  fallback; max 2 retries with backoff+jitter for 429/5xx/network only
  (attempts counted in metadata); provider auth failures use the distinct
  `provider_auth_error` code (contract v1.2), never user `unauthorized`.
- Secrets: `APP_LLM_API_KEY` → Settings → Authorization header, one use site;
  enforced absent from logs/exceptions by `tests/test_llm_secrets.py`.
- Configure: `APP_LLM_PROVIDER=mock|openai_compat`, `APP_LLM_API_KEY`,
  `APP_LLM_BASE_URL`, `APP_LLM_MODEL`, `APP_LLM_TIMEOUT_S`,
  `APP_LLM_MAX_RETRIES`, `APP_LLM_MAX_TOKENS`. No endpoints, no orchestration,
  no billing tables, no frontend changes in this stage.
- Details: `docs/contracts/llm-notes.md`.

## Agent execution engine (Stage 4)

- `app/execution/`: `executor.py` (`execute_task`: claim → context → LLM →
  validate → persist), `prompts.py` (pure prompt builders + fixed result
  schema + size-fitted `task_llm_result` envelope, max 8000 chars).
- Transaction discipline: atomic CAS claim committed before any network call;
  no transaction held across the LLM request; persist in one txn (status +
  summary + activity event + `recalculate_project_spend`); one aggregate per
  flush (flush-anomaly constraint, see `docs/flush-anomaly.md`).
- `execute_task` requires a session without an open transaction (loud
  `RuntimeError` otherwise) and always leaves the session clean.
- Cancellation re-raises `CancelledError` after confirmed-safe compensation
  (revert to pre-claim status, ownership re-verified); unknown states are
  reported as `unknown`, never silently fixed.
- `ai_spent` writable only via `recalculate_project_spend()` — enforced by
  `BaseRepository.update()` guard (`ImmutableFieldError`).
- No endpoints, migrations, dependencies, frontend changes, or contract changes.

## Roadmap (updated)

- Stage 2: DONE — persistence (models, repositories, migrations, isolated-DB tests).
- Stage 3: DONE — provider-agnostic LLM layer (OpenAI-compat adapter + mock; zero real calls in tests).
- Stage 4: DONE — single-task execution engine (claim → LLM → validate → persist; PG-tested).
- Stage 5: DONE — execution API (run task, read task, recover stale working) with
  local operator bearer auth (fail-closed), contract-header gating, recovery
  staleness bound validated at startup. No queues/SSE/frontend integration.
- Stage 6+: multi-user auth; CRUD API; frontend API client behind feature flag; Tauri packaging.
