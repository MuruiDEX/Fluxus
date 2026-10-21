# Deployment — MUSE SPARK / AI Business Office

Reproducible deployment from a clean machine to a running, verified system.
Every command below matches the real code — no invented flags, no placeholders
(except secrets, which are never committed and never printed).

Bare-process local runs are loopback-first: backend binds `127.0.0.1` and
the frontend talks to it same-site. In the production Compose topology the
backend listens on `0.0.0.0` **inside its container only** (no published
port); Caddy reaches it over the internal Docker network. Public traffic
must terminate TLS at Caddy or another proxy (see §9 and §14).

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

`package-lock.json` pins the frontend tree. `backend/requirements.txt`
currently records compatible-version ranges, not a fully locked resolution;
it is reproducible only at the resolver level. Before a production release,
capture an exact backend lock (`pip freeze` from the deploy image, or add a
hash-pinned lockfile) and build from that — do not claim pin-for-pin locking
until it exists.

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
- Tag both production images per release, then keep the previous backend and
  frontend tags. `deploy/rollback.ps1` rolls back the **application tier
  only** and requires the previous image tags, a real `prod.env`, and a
  readable backup:

  ```powershell
  powershell -ExecutionPolicy Bypass -File deploy/rollback.ps1 `
    -PreviousApiImage fluxus-api:0.1.0-20261001 `
    -PreviousWebImage fluxus-web:live-20261001 `
    -BackupFile D:\fluxus-backups\fluxus-prod-muse-20261021-120000.dump -Yes
  ```

  It recreates only `backend` and `frontend` with `--no-build --no-deps`
  (Postgres, Caddy, volumes, migrations, and data untouched) and waits for
  both healthchecks. It never restores, drops, or migrates a database and
  never runs a schema downgrade.
- A database rollback is **manual and separately authorized**. Restore the
  pre-upgrade backup to a scratch database first, verify it (§12), then
  restore live or create a fresh database and repoint `APP_DATABASE_URL`.
  Never `alembic downgrade` live data and never drop/recreate the live
  database blindly.
- After either path, verify with §10 checks, `scripts/smoke_check.py`, and
  `deploy/verify.ps1` (§13).

## 12. Backup / restore and disaster actions

Production Compose backups use `deploy/backup.ps1` — a timestamped
`pg_dump -Fc` of the `postgres` service plus a `pg_restore --list` header
check. It requires explicit `-Yes` (never runs from preflight or deploy),
fails closed on bad config/placeholders/unavailable DB, never restores,
deletes, or overwrites, never prints secrets, and never proves a full
restore — always drill restores on a scratch database first.

```powershell
# from the repo root; prod.env must already be configured (§14.2)
powershell -ExecutionPolicy Bypass -File deploy/backup.ps1 -Yes
# optional: -BackupDir D:\fluxus-backups (default: deploy/backups/)
```

The script dumps inside the `postgres` container over its local socket (no
password on any command line), writes one unique
`fluxus-prod-<db>-<timestamp>.dump` file, refuses to overwrite, and keeps
failed/empty output deleted. Manual fallback without Compose:

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
The Stage 17 restore drill is recorded in §14.6; repeat it after any
Postgres or infrastructure change. A `pg_restore --list` header check is a
format check, not proof that a full restore succeeds.

## 13. Smoke test and post-deployment verification

Anonymous backend checks (stdlib only):

```powershell
# anonymous checks only
python backend/scripts/smoke_check.py --base-url http://127.0.0.1:8000
# full pass with operator credentials (session revoked at the end)
python backend/scripts/smoke_check.py --base-url https://app.example.com `
  --email op@example.com --password '...' --origin https://app.example.com
```

Anonymous checks verify: health, honest readiness, envelope+trace-id on 404,
CORS preflight, uniform 401, login cookie flags, authenticated read, uniform
foreign-404, marketplace shape, logout revocation. Exit 0 = all green.

After a production Compose deploy (§14), run the read-only HTTPS gate
`deploy/verify.ps1` from any machine — no credentials, no mutations, TLS
always validated, 10s default timeout (`-TimeoutSec 2..120`):

```powershell
powershell -ExecutionPolicy Bypass -File deploy/verify.ps1 -BaseUrl https://app.example.com
```

Exact checks performed (each mirrors a real backend contract):
- `GET /api/v1/health` → 200 `{status:ok, version, environment, contract}`
  plus `X-Trace-Id` (liveness; `HealthResponse` + `trace_id_middleware`).
- `GET /api/v1/readiness` → 200 `{status:ready, database:ok}` plus
  `X-Trace-Id`; a 503 `{status:degraded, ...}` FAILS the run — gate traffic
  on this, not on `/health`.
- `GET /api/v1/no-such-route-verify` → 404 contract envelope
  `{error:{code:not_found, message, trace_id}}` with the body `trace_id`
  matching the `X-Trace-Id` header.
- `OPTIONS /api/v1/auth/logout` with `Origin: <base>` →
  `Access-Control-Allow-Origin: <base>` exactly plus
  `Access-Control-Allow-Credentials: true`; a foreign origin must NOT be
  echoed (CORS allowlist from `APP_CORS_ORIGINS`). Preflight-only: no
  browser is involved and no session is created.
- `GET /` → 200 `text/html` storefront (proves Caddy routes non-`/api`
  traffic to the Next.js server).

Exit codes: 0 all passed; 1 base-URL usage problem (missing/http/loopback);
2 a check failed; 3 unreachable before checks completed. Limitations: this
proves the public contract surface only — it never logs in, never drills a
restore, and never replaces load or backup testing.

## 14. Production Compose deployment (Caddy + frontend + backend + Postgres)

`deploy/docker-compose.prod.yml` is the only supported production topology:
Caddy terminates TLS and owns ports 80/443; the frontend, backend, and
PostgreSQL publish nothing and talk over internal networks only
(`backend_net` is `internal: true`). One-shot `migrate` (`alembic upgrade
head`) must complete before `backend` starts; migration failure blocks the
deployment instead of reporting success. `backend` stays a single
replica with a single uvicorn worker — the login throttle is process-local
(`backend/app/core/rate_limit.py`); do not scale past 1 or add workers
without moving the throttle to a shared store. The frontend image bakes
`NEXT_PUBLIC_APP_MODE=live` plus the public HTTPS origin: LIVE never
falls back to demo data (fail-closed `AuthGate` + `backendRequestsEnabled`).

The backend container listens on `0.0.0.0:8000` inside `backend_net` so
Caddy can proxy to `backend:8000`; it publishes no host port. Its Docker
healthcheck probes `127.0.0.1:8000` from inside the same container.

### 14.1 Prerequisites

- A Linux/macOS/Windows host with Docker 29+ and the daemon running.
- A real domain whose A/AAAA record already points at this host (ACME needs
  it on first boot), with inbound 80+443 reachable.
- No real credentials or public deployment yet — everything below is local
  until the `up` step in §14.4, which only you run on your own server.

### 14.2 Configure secrets (never commit them)

```powershell
# from the repo root
Copy-Item deploy/prod.env.example deploy/prod.env
# edit deploy/prod.env: real DOMAIN, ACME_EMAIL, PUBLIC_API_ORIGIN
# (https origin matching DOMAIN), POSTGRES_PASSWORD (long random),
# APP_DATABASE_URL (password MUST match POSTGRES_PASSWORD), APP_CORS_ORIGINS
# (JSON array containing PUBLIC_API_ORIGIN). deploy/prod.env is gitignored.
```

`deploy/preflight.ps1` is the read-only gate — it never starts services,
touches DNS, changes firewall rules, migrates, or deletes data:

```powershell
# repeatable; safe to run as often as needed
powershell -ExecutionPolicy Bypass -File deploy/preflight.ps1
```

It fails fast when `prod.env` is missing (exit 1), any required value is
blank or still a template placeholder (exit 2), 80/443 is occupied (exit 3),
or the Docker daemon is down (exit 4). It validates `DOMAIN` against
`PUBLIC_API_ORIGIN`, the CORS allowlist against the prod guard, and the DB
password consistency — printing variable NAMES only, never secret values.

### 14.3 Validate and build (no services started)

```powershell
cd deploy
docker compose --env-file prod.env -f docker-compose.prod.yml config --quiet
docker compose --env-file prod.env -f docker-compose.prod.yml build
```

`config --quiet` is the topology proof: only Caddy publishes ports,
`backend_net` is internal, and every `${VAR:?...}` resolves from your
`prod.env` (a missing name fails here, before anything runs).

### 14.4 Deploy sequence (first boot and upgrades)

```powershell
cd deploy
# 0. back up live data BEFORE any migration (§12) — never skip on upgrade
# 1. start data + proxy dependencies
docker compose --env-file prod.env -f docker-compose.prod.yml up -d postgres caddy
# 2. run schema migrations one-shot (must exit 0)
docker compose --env-file prod.env -f docker-compose.prod.yml up migrate
# 3. start the app tier (waits on migrate success + backend readiness)
docker compose --env-file prod.env -f docker-compose.prod.yml up -d backend frontend
docker compose --env-file prod.env -f docker-compose.prod.yml ps
```

First boot: Caddy issues its certificate (minutes), then serves
`https://DOMAIN/` (frontend) with `https://DOMAIN/api/*` proxied to the
backend — a single public origin, so the Secure Lax session cookie works
same-site with no cross-origin CORS. Bootstrap the owner (§4), then run
the smoke test (§13) against `https://DOMAIN`. Gate traffic on
`/api/v1/readiness` (live `SELECT 1`), not `/api/v1/health` (liveness).

### 14.5 Recovery precautions

- `postgres` data persists in the `pgdata_prod` volume; `caddy_data` holds
  certificates. Never delete volumes as a cleanup shortcut.
- Run `deploy/rollback.ps1` to restore the previous **application images**
  (`-PreviousApiImage`, `-PreviousWebImage`, `-BackupFile`, `-Yes`). It never
  touches Postgres data, Caddy, volumes, migrations, or databases.
- Database recovery is manual and separately authorized (§11, §12): restore
  the verified backup to a scratch database, verify it, then restore live or
  repoint `APP_DATABASE_URL`; never `alembic downgrade` live data and never
  drop/recreate the live database blindly.
- The compose file itself changes rarely; tag images per release and keep
  the previous tags available locally (rollback never pulls or builds).
- Known limits: throttle resets on backend restart; ACME needs 80/443
  reachable at issuance AND renewal; `mock` LLM serves real data with
  offline AI (set `openai_compat` + key for real execution).

### 14.6 Restore drill status

**PASSED (2026-10-21, Stage 17).** A throwaway PostgreSQL 16 cluster on a
non-default port was created under the OS temp directory; two uniquely named
disposable databases were created. The source was migrated to Alembic
`b4e8f1a2c3d4`, seeded with one business/user/owner membership, dumped with
`pg_dump -Fc` (53,039 bytes, 117 archive entries), and restored into the
second database with `pg_restore`. Verified: identical 18-table public
schema, matching Alembic revision, row counts `1/1/1`, the owner-membership
join invariant, 56 FK/CHECK constraints, and a rejected invalid membership
role (CHECK enforced). A backend started against the restored database
returned health 200 `{status:ok}` and readiness 200
`{status:ready,database:ok}`. The temp cluster, databases, and archive were
removed afterward; no existing dev/test/prod database, Docker volume, or
backup was touched. This proves this fixture/restore path, not every future
backup — repeat the drill after Postgres or infrastructure changes.
