# Backend — FastAPI + persistence + LLM layer + execution API + auth (Stages 1–11)

> Stages 7–11 implemented on top of Stage 6: management writes
> (Project/Task PATCH, Brain POST), marketplace reads + agent hire, brief
> intake + finalize API, LIVE frontend integration, Playwright LIVE E2E
> (deterministic stub LLM, test-only config). No public registration, no
> billing, no pack install — see Roadmap.

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

# dev database only (:5432); start it separately before migrating/running.
# Tests use the isolated service below, never this database.
docker compose up -d postgres
# local dev credentials only (from backend/docker-compose.yml); never production
$env:APP_DATABASE_URL = "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse"

.\.venv\Scripts\alembic upgrade head      # dev DB schema
.\.venv\Scripts\python scripts\seed_demo.py  # demo data (dev only)

# pure URL/configuration tests: no PostgreSQL required
.\.venv\Scripts\python -m pytest tests\test_pg_fixture_config.py -q

# DB-backed tests require an explicit, verified isolated server
$env:TEST_PG_PASSWORD = "<test-only password>"
docker compose --profile test-isolated up -d postgres-test-isolated
$PgBaseUrl = "postgresql://muse_test:<url-encoded-test-password>@127.0.0.1:55434"
.\.venv\Scripts\python -m pytest -q --pg-base-url $PgBaseUrl

.\.venv\Scripts\ruff check app tests alembic scripts   # lint
.\.venv\Scripts\ruff format --check app tests alembic scripts

# run locally (loopback only; needs APP_DATABASE_URL above — see Security)
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Isolated PostgreSQL test server

Tests never start PostgreSQL and never discover a local server. DB-backed
tests require an explicit `--pg-base-url` for a numeric loopback host
(`127.0.0.1` or `::1`) on an explicit non-protected port; without it, test
database selection fails closed. Ports `5432`, `5433` and `55433` are
protected and rejected. `55434` is not in the protected set, but that alone
does not make it safe: every other URL rule still applies (numeric loopback
host, explicit port, no path/query/fragment, fixed `muse_test`/`muse_mig`
database names).

The opt-in Compose service publishes only `127.0.0.1:55434`, uses a dedicated
volume, creates `muse_test` on first start, and the init script creates
`muse_mig` only while that empty volume is being initialized for the first
time — it does not re-run on later container restarts. Set
`TEST_PG_PASSWORD` to a test-only value before starting it; never use
production credentials.

```powershell
$env:TEST_PG_PASSWORD = "<test-only password>"
docker compose --profile test-isolated up -d postgres-test-isolated
docker compose --profile test-isolated ps postgres-test-isolated
```

DB-backed tests are destructive: the test engine drops/recreates all tables in
`muse_test`, and migration tests drop the `public` schema in `muse_mig`.
Before running them, verify that `55434` is the dedicated isolated service
(container, volume and port mapping), not `backend/.pgdata-5433` or the
protected `postgres`/`postgres-test` services. The port number alone is not
proof; confirm the container and volume identity with `docker inspect`. If
that cannot be proven, stop.

`scripts/pg_start.py` no longer starts embedded PostgreSQL. To create the two
test databases on an already verified isolated server, use:

```powershell
.\.venv\Scripts\python scripts\pg_ensure_dbs.py --base-url $PgBaseUrl
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
- Databases: `docker-compose.yml` — `postgres` (:5432, dev) and the legacy
  `postgres-test` (:5433, protected). Tests use the opt-in
  `postgres-test-isolated` service on `127.0.0.1:55434` with its own volume
  (`muse_test` + `muse_mig`). Automatic embedded PostgreSQL startup is
  disabled; `scripts/pg_start.py` is a fail-closed stub. Use
  `scripts/pg_ensure_dbs.py --base-url ...` only against a verified isolated
  server.
- SQLite is a portability subset only (`test_portability_sqlite.py`:
  CHECK/FK/rollback/round-trips); PostgreSQL semantics are never weakened
  for it (row-locks, concurrency, migrations are PG-only tests).
- Dev seed: `scripts/seed_demo.py` (idempotent, Northstar subset, demo only).

## Security model (Stage 6: session auth + membership isolation)

- Binds `127.0.0.1` by default; never `0.0.0.0` unless explicitly configured.
- CORS: exact-origin allowlist only (local Next dev `localhost:3000` +
  `127.0.0.1:3000`, Tauri schemes; the `:3210` Playwright port is
  deliberately excluded — e2e runs backend-less in demo mode).
  No wildcard, no credentials+wildcard combination. `X-CSRF-Token` is an
  allowed header because browser mutations send it with
  `credentials: include`. Open the dev frontend via `http://127.0.0.1:3000`
  (same-site with a `127.0.0.1` backend): `SameSite=Lax` session cookies do
  not flow on cross-site `localhost` ↔ `127.0.0.1` fetches, by browser
  design — verified live in Stage 6.1, no flag disables this.
- Auth: `POST /api/v1/auth/login` (uniform 401 for unknown email / wrong
  password, 429 when throttled), HttpOnly `SameSite=Lax` session cookie,
  server-side session rows (SHA-256 id hash, expiry, revocation),
  `X-CSRF-Token` required on all state-changing requests (403 otherwise).
  Every domain request checks live membership against the `business_id`
  from the URL path only — foreign/unknown business is a uniform 404.
- Passwords: Argon2id via `argon2-cffi` defaults, rehashed transparently
  on login. No public registration endpoint by design.
- Every response carries `X-Trace-Id`; every error follows the contract
  envelope with no internals (no tracebacks/SQL/paths/secrets in `message`).
- Secrets arrive via environment (`APP_*`) — none are defaulted, logged,
  or exposed to the frontend/desktop renderer. Frontend keeps zero secrets
  (session id in HttpOnly cookie, CSRF token in memory only).

## Persistence decision (SQLite vs PostgreSQL)

Decision stands: **PostgreSQL** (SQLAlchemy 2.0 + Alembic, Stage 2), developed
against a local Postgres (Docker) and tested on an isolated database.

Desktop constraint (no Postgres/Docker install for the end user) stays an
open distribution question for Stage 2/7 — candidates: embedded Postgres
sidecar vs. file-backed engine behind the SAME repository interfaces.
The repository layer MUST abstract the engine so neither the API nor the
frontend can tell which one is underneath. No decision by code yet.

## Authentication & user isolation (Stage 6)

- `app/core/`: `passwords.py` (Argon2id hash/verify/rehash),
  `rate_limit.py` (in-memory login throttle — single-process desktop only,
  counters reset on restart; move to a shared store for SaaS),
  `identity.py` (`require_business` / `require_session` / `require_user` —
  the single trust boundary for domain operations).
- `app/api/v1/auth.py`: login / logout / me (see contract §8). Session
  cookie never has `Secure` on local HTTP — it is an explicit opt-in
  (`APP_SESSION_COOKIE_SECURE=true`) for real HTTPS deployments only;
  enabling it silently would break local Tauri/dev with no diagnostic.
- Owner bootstrap + recovery CLI (local operator only, never over HTTP):
  `python scripts/bootstrap_owner.py create-owner --email …`
  (`--password` omitted → secure `getpass` prompt), `reset-password`,
  `list-users`, `grant --email … --business … --role owner|member`.
  Requires a migrated DB (`alembic upgrade head` first). Existing
  businesses are never auto-attached — orphan data stays invisible until
  an explicit `grant`.
- Migrations: `alembic/versions/7f3a9c1e4b52_0002_authentication.py`
  (additive: `users`, `memberships`, `auth_sessions`; existing tables and
  rows untouched). **Backup before upgrading a database with real data:**
  `pg_dump "$APP_DATABASE_URL" -Fc -f backup-pre-stage6.dump`, then
  `alembic upgrade head`, and verify with
  `alembic current` + `SELECT count(*) FROM businesses`. Downgrade drops
  only the three auth tables (access links go with them — users lose
  access until re-granted); it is covered by the migration test on an
  isolated schema, not by a production rollback run — for production,
  restore from the `pg_dump` backup instead of downgrading in place.
- Tests: `tests/test_auth*.py` (endpoints, models, repos), `test_cors.py`
  (preflight config), `test_isolation.py` (cross-tenant matrix),
  `test_migrations.py` (upgrade/downgrade cycle + Stage 5 data survives).

## Deferred models (deliberately absent in Stage 2)

- `users` — not needed without auth (Stage 6). Schema already reserves the
  concept via `business_id` scoping; no placeholder table.
- `llm_usage` — not needed without LLM calls (Stage 3/4). Spend tracking for
  AI runs lands with the provider layer. No billing code exists anywhere.

## LLM provider layer (Stage 3)

- `app/llm/`: `types.py` (requests, structured output, usage, error taxonomy),
  `base.py` (Protocol + `create_provider` factory), `mock.py` (scripted,
  fully offline — the default), `adapters/openai_compat.py` (raw httpx, no
  vendor SDK; covers OpenRouter/Ollama-style OpenAI-compatible endpoints via
  base_url — Azure's native api-key/deployment/query shape needs a gateway or
  a dedicated adapter),
  `validate.py` (JSON-Schema subset validator — compliance never claimed
  without local validation).
- Rules: `asyncio.CancelledError` propagates unwrapped and is never retried
  (streams/clients close in `finally`); `json_schema` first with `json_object`
  fallback; max 2 retries with backoff+jitter only for safe 429/pre-send
  network failures; 5xx and post-send transport failures are held ambiguous
  (attempts counted in metadata); provider auth failures use the distinct
  `provider_auth_error` code (contract v1.2), never user `unauthorized`.
- Secrets: `APP_LLM_API_KEY` → Settings → Authorization header, one use site;
  enforced absent from logs/exceptions by `tests/test_llm_secrets.py`.
- Configure: `APP_LLM_PROVIDER=mock|openai_compat`, `APP_LLM_API_KEY`,
  `APP_LLM_BASE_URL`, `APP_LLM_MODEL`, `APP_LLM_TIMEOUT_S`,
  `APP_LLM_MAX_RETRIES`, `APP_LLM_MAX_TOKENS`, `APP_LLM_MAX_TOKENS_PARAM`.
  No endpoints, no orchestration,
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
  contract-header gating, recovery staleness bound validated at startup.
  The Stage 5 local operator bearer was fully removed in Stage 6 —
  session + membership auth replaced it, no fallback remains.
  No queues/SSE/full frontend integration.
- Stage 6: DONE — cookie-session auth (Argon2id, HttpOnly cookie, CSRF),
  per-business membership isolation on every domain endpoint, owner
  bootstrap CLI, additive migration preserving Stage 5 data, minimal
  frontend slice (login/logout/me, route guard, 401 handling, DEMO banner).
- Stage 7+: DONE — management writes; marketplace + hire; brief intake,
  planning, finalization API with strict server-side validation; LIVE
  frontend integration (reads, decisions, executions, writes, hire,
  intake, finalize); Playwright LIVE E2E against a deterministic stub LLM.
- Stage 12: production hardening — env-configurable CORS, prod startup
  guard, bounded login-throttle memory (see Production deployment below).
- Stage 14: agent lifecycle — `Agent.hired` is the active flag (no migration,
  no enum change); `POST .../agents/{id}/activate|disable` (idempotent,
  audited); disabled owners take no new work (PATCH owner/team_ids, plan
  save, execution claim refuse with 422/`agent_disabled`); in-flight runs,
  history and terminal answers untouched.
- Still absent: Tauri packaging, pack install, billing.

## Production deployment (Stage 12)

The app is loopback-first by default. A network deployment needs explicit
operator configuration — nothing production-sensitive has a usable default.

Required environment (`APP_` prefix, see `app/core/config.py`):

| Variable | Production value | Why |
|---|---|---|
| `APP_ENVIRONMENT=prod` | `prod` | Enables the startup guard below |
| `APP_DATABASE_URL` | real Postgres URL, e.g. `postgresql+asyncpg://user:pass@host:5432/muse` | Without it every domain endpoint is a 500 |
| `APP_SESSION_COOKIE_SECURE=true` | `true` | Session cookies must not travel over plain HTTP |
| `APP_CORS_ORIGINS` | JSON array of exact origins, e.g. `'["https://app.example.com"]'` | Default is the local dev/Tauri set; `"*"` is rejected at startup |
| `APP_LLM_PROVIDER` + `APP_LLM_API_KEY` (+ `APP_LLM_BASE_URL` for compatibles) | real provider credentials | `mock` (default) performs no real work |

Startup guard (`validate_production_config`, covered by
`tests/test_prod_config.py`): with `APP_ENVIRONMENT=prod` the process
refuses to serve without `APP_DATABASE_URL` or with
`APP_SESSION_COOKIE_SECURE` unset/false. Local/dev/test are unaffected.

Login throttle is in-memory per process (5 attempts / 300s per email,
bounded to 10000 identifiers) and resets on restart. For multi-process or
SaaS deployments move it to a shared store; single-desktop use needs
nothing.

Backup & restore (documented procedure — no automation is bundled, so
schedule this at the operator level):

```powershell
# backup (custom format, includes schema + data)
pg_dump "$env:APP_DATABASE_URL" -Fc -f muse-backup.dump
# verify a backup by restoring to a scratch database, never over live data
pg_restore -d "postgresql://muse:muse@127.0.0.1:5432/muse_restore" muse-backup.dump
# restore over live data only after stopping the backend
$env:APP_DATABASE_URL = "...live url..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Rules: back up before every `alembic upgrade head` on a database with
real data; verify with `alembic current`; never downgrade a production
database in place — restore from backup instead (downgrades are covered
only by the isolated-schema migration tests). Never point tests at a
production database (`muse_test`/`muse_mig` only).
