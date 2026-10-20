# Deployment — MUSE SPARK / AI Business Office

Reproducible deployment from a clean machine to a running, verified system.
Every command below matches the real code — no invented flags, no placeholders
(except secrets, which are never committed and never printed).

The app is loopback-first: backend binds `127.0.0.1` and the frontend talks
to it same-site. Public traffic must terminate TLS at a reverse proxy in
front of both processes (see §9).

## 1. Requirements

- Python 3.12+ (`python --version`).
- Node.js 20+, npm 10+ (`node --version`, `npm --version`).
- PostgreSQL 16 — either Docker (compose file in `backend/`) or a managed
  instance reachable over the network.
- Docker 29+ with the daemon running, only if using the bundled databases.
- No external AI provider required: `APP_LLM_PROVIDER=mock` (default) runs
  fully offline. Real work needs an OpenAI-compatible endpoint + key.

## 2. Install dependencies

```powershell
# backend (from backend/): fresh venv + runtime deps
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
# editable install registers the package (setuptools finds app/ only —
# see pyproject [tool.setuptools.packages.find])
.\.venv\Scripts\pip install -e . --no-deps
# dev/test extras (pytest, ruff, embedded PG fallback)
.\.venv\Scripts\pip install -r requirements-dev.txt
```

```powershell
# frontend (from frontend/): locked install
cd frontend
npm ci
```

`backend/requirements.txt` mirrors `pyproject.toml` pin-for-pin (verified by
`pip install --dry-run`); `package-lock.json` pins the frontend tree.

## 3. Production environment variables

All backend secrets arrive via `APP_*` env (see `backend/app/core/config.py`);
nothing secret has a usable default. Frontend needs only the public backend
URL (baked at build time).

```powershell
$env:APP_ENVIRONMENT = "prod"
$env:APP_DATABASE_URL = "postgresql+asyncpg://USER:PASS@HOST:5432/muse"
$env:APP_SESSION_COOKIE_SECURE = "true"
$env:APP_CORS_ORIGINS = '["https://app.example.com"]'
$env:APP_LLM_PROVIDER = "openai_compat"
$env:APP_LLM_API_KEY = "<provider key, never committed>"
$env:APP_LLM_BASE_URL = "https://api.openai.com/v1"   # or Azure/OpenRouter/Ollama
$env:APP_LLM_MODEL = "gpt-4o-mini"
```

With `APP_ENVIRONMENT=prod` the process refuses to serve unless
`APP_DATABASE_URL` is set, `APP_SESSION_COOKIE_SECURE=true`, and
`APP_CORS_ORIGINS` lists at least one exact HTTPS origin that is not
loopback/local-only (`validate_production_config`, covered by
`tests/test_prod_config.py`). `APP_CORS_ORIGINS` accepts a JSON array of
exact origins only — `"*"`, paths, trailing slashes, and `http://` are
rejected. Defaults (no env) are the safe local set: loopback bind, mock LLM,
Lax non-Secure cookie, local CORS allowlist.

```powershell
# frontend build (from frontend/): bakes the LIVE API origin
$env:NEXT_PUBLIC_APP_MODE = "live"
$env:NEXT_PUBLIC_API_URL = "https://app.example.com"
npm run build:live
```

For local runs omit the vars and use `http://127.0.0.1:8000`
(see `frontend/.env.example`).

## 4. Bootstrap the owner (no public registration by design)

```powershell
cd backend
# database must already be migrated (see §5)
.\.venv\Scripts\python scripts\bootstrap_owner.py create-owner --email op@example.com
# (--password omitted → secure getpass prompt, never argv, never logs)
.\.venv\Scripts\python scripts\bootstrap_owner.py list-users
.\.venv\Scripts\python scripts\bootstrap_owner.py grant --email op@example.com --business biz_1 --role owner
```

A failed attach commits nothing (regression-tested in
`tests/test_bootstrap_owner.py`).

## 5. Alembic migrations

```powershell
cd backend
.\.venv\Scripts\alembic upgrade head   # forward-only on real data
.\.venv\Scripts\alembic current        # must print the single head
.\.venv\Scripts\alembic upgrade head --sql  # offline render check (no DB writes)
```

Single head is enforced by `tests/test_migrations.py` (upgrade/downgrade
cycle on an isolated schema). Downgrades are covered only there — never
downgrade production in place; restore from backup instead (§12).

## 6. Build and run

```powershell
# backend (from backend/, env as in §3)
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# frontend production (from frontend/, after npm run build)
npm run start -- --port 3000
```

Development alternative: `npm run dev` (port 3000) + backend as above.
E2E against production builds: `npm run test:e2e` (DEMO) and
`npm run test:e2e:live` (LIVE backend + Postgres + stub LLM, test-only).

## 7. NEXT_PUBLIC_API_URL

Public, baked at `npm run build` time. Local: `http://127.0.0.1:8000`.
Deployed behind the proxy (§9): the public API **origin only**, e.g.
`https://app.example.com` — do not append `/api`. The client adds `/api/v1`.
Paths, queries, fragments, and credentials are rejected by the build.
Rebuild after changing it.

`NEXT_PUBLIC_APP_MODE` is required for production builds: `live` for real
server data, `demo` for the self-contained demo. `npm run build` fails if it
is missing; use `npm run build:live` or `npm run build:demo`.

## 8. CORS / cookies / environment

- `APP_CORS_ORIGINS`: exact origins, JSON array. Must include the exact
  frontend origin (scheme + host + port); `localhost` and `127.0.0.1` are
  distinct origins to browsers. Under `APP_ENVIRONMENT=prod` every entry
  must be HTTPS and non-loopback.
- `APP_SESSION_COOKIE_SECURE=true` on any HTTPS deployment (required under
  `APP_ENVIRONMENT=prod`); keep `false` for local plain HTTP.
- `APP_ENVIRONMENT=prod` turns on the startup guard; `local`/`dev`/`test`
  are unaffected. Typos such as `production` are rejected at startup.
- Fixture scripts refuse `APP_ENVIRONMENT=prod`. `scripts/seed_demo.py`
  additionally requires `APP_ALLOW_DEMO_SEED=1`, and
  `scripts/e2e_live_setup.py` requires `APP_ALLOW_E2E_SETUP=1`, and the
  scratch-table diagnostic `scripts/repro_flush_anomaly.py` requires
  `APP_ALLOW_REPRO=1`; remote databases require the corresponding
  `APP_ALLOW_REMOTE_*` override.

## 9. TLS termination (recommended scheme)

The app terminates no TLS itself. Put a reverse proxy (nginx/Caddy/Traefik
or cloud LB) in front:

```
internet ──https──▶ proxy ──http://127.0.0.1:3000──▶ next start
                      └─────http://127.0.0.1:8000──▶ uvicorn (loopback only)
```

The proxy owns certificates, HSTS, and access logs. Backend `api_host`
stays loopback; `session_cookie_secure=true` requires the https frontend
origin. No `TrustedHostMiddleware` is bundled (no host-based URLs are
generated); add it at the proxy layer if its policy requires one.

## 10. Health / readiness / logs / shutdown

- `GET /api/v1/health` — liveness, dependency-free, always 200 when the
  process answers. Includes `version`, `environment`, `contract`.
- `GET /api/v1/readiness` — readiness: 200 `{status: ready}` only after a
  live `SELECT 1` against the configured database, else 503 `degraded`
  (`unconfigured`/`unreachable`). Gate orchestrator traffic on this, not
  on `/health`. No auth (probes carry no credentials), no internals.
- Every response carries `X-Trace-Id`; every error follows the contract
  envelope (`docs/contracts/domain-api.md` §6) with no tracebacks/SQL/
  paths/secrets in `message`. Backend logs one JSON line per event to
  stdout (no secrets, no request bodies with credentials).
- Shutdown: stop uvicorn (`SIGTERM`/Ctrl+C) — lifespan disposes the async
  engine pool; in-flight LLM calls finish or cancel, never half-persist
  (executor compensates or reports `unknown`). `next start` stops with the
  same signal.

## 11. Versioning and rollback

- Version lives in `backend/pyproject.toml` (`version`) and surfaces via
  `/api/v1/health`. Bump it with each release; frontend `package.json`
  carries its own version alongside.
- Rollback = previous build + database restore from a pre-upgrade backup
  (never `alembic downgrade` on live data):
  1. stop the backend,
  2. `pg_restore` the backup to the live database (or a fresh one + repoint
     `APP_DATABASE_URL`),
  3. deploy the previous backend/frontend build,
  4. verify with §10 checks + `scripts/smoke_check.py` (below).

## 12. Backup / restore and disaster actions

No backup automation is bundled — schedule this at operator level:

```powershell
# backup (custom format: schema + data, single file)
pg_dump "$env:APP_DATABASE_URL" -Fc -f muse-backup.dump
# restore to a SCRATCH database first and verify — never over live data blind
pg_restore -d "postgresql://USER:PASS@HOST:5432/muse_verify" muse-backup.dump
# only then, with the backend stopped, restore live (or repoint APP_DATABASE_URL)
```

Verify a restore with: table list vs. `alembic history`, row counts on
`businesses`/`users`, `alembic current` on the restored URL, then the
smoke check below. On incident: stop backend → restore → start → verify →
post-mortem the logs (trace ids correlate client reports to server lines).
Test restores are covered live in Stage 13 (temporary `muse_drill_*`
databases, dropped afterwards) — repeat the drill after any Postgres or
infrastructure change.

## 13. Smoke test after deployment

```powershell
# anonymous checks only
python backend/scripts/smoke_check.py --base-url http://127.0.0.1:8000
# full pass with operator credentials (session revoked at the end)
python backend/scripts/smoke_check.py --base-url https://app.example.com `
  --email op@example.com --password '...' --origin https://app.example.com
```

Checks: health, honest readiness, envelope+trace-id on 404, CORS echo,
uniform 401, login cookie flags, authenticated read, uniform foreign-404,
marketplace shape, logout revocation. Exit 0 = all green. Use a throwaway
operator or the bootstrap owner — never real user data.
