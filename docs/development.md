# Development — MUSE SPARK / AI Business Office

## Prerequisites

- Node.js 20+ (developed with Node 24), npm 10+.
- Python 3.12+ for the backend (`backend/`).
- PostgreSQL 16 for the backend: Compose `postgres` (:5432, dev) and the
  opt-in `postgres-test-isolated` service on `127.0.0.1:55434` for tests.
  Automatic embedded PostgreSQL startup is disabled. DB-backed pytest requires
  an explicit `--pg-base-url` and fails closed without it; ports `5432`,
  `5433` and `55433` are protected and rejected. `55434` is not protected,
  which does not make it safe by itself — the URL is accepted only when the
  numeric loopback host, explicit port and fixed test database names validate.
- Playwright browsers for E2E: `npm --prefix frontend exec playwright install chromium`
  (one-time; binaries live outside the repo in the Playwright cache).
- No `.env` secrets or external AI provider required (`APP_LLM_PROVIDER=mock` default, offline).

## Install

```bash
# from the repo root — frontend
npm run install:all
# equivalent: npm --prefix frontend install
```

```powershell
# backend (Windows PowerShell, from backend/)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
# dev/test extras: pytest, ruff, plus the legacy postgresql-binaries pin
# (still listed in requirements-dev.txt / pyproject.toml; see that file)
# pip-only runtime alternative: pip install -r requirements.txt
# (pyproject.toml is the source of truth; the requirements*.txt files mirror it)
```

## Run

```bash
npm run dev      # Next.js dev server → http://localhost:3000
npm run build    # production build (Turbopack)
npm run start    # serve the production build
```

```powershell
# backend (from backend/, .venv active; loopback only by default)
# dev DB: start it separately first (`docker compose up -d postgres` in backend/).
# Local dev credentials only (from backend/docker-compose.yml); never production.
$env:APP_DATABASE_URL = "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse"
.\.venv\Scripts\alembic upgrade head      # dev DB schema
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# test DB (separate): opt-in isolated service — see Verify, never :5433
# frontend LIVE mode: $env:NEXT_PUBLIC_API_URL="http://127.0.0.1:8000" (see frontend/.env.example)
```

All commands also work directly inside `frontend/` (`npm run dev`, …).
The root scripts are thin `npm --prefix frontend …` wrappers so you never
have to `cd` manually.

> Port flags do not survive the double-npm hop (`npm --port` is consumed as
> npm config). For a custom port, run from `frontend/` directly:
> `npm run dev -- --port 3210`.

## Verify

```bash
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
npm run test        # vitest run (engine unit tests, ~1s)
npm run test:e2e    # Playwright Chromium (spins up dev on :3210, ~1-2 min)
```

```powershell
# backend (from backend/, .venv active)

# Pure URL/configuration tests (no PostgreSQL, no server start)
.\.venv\Scripts\python -m pytest tests\test_pg_fixture_config.py -q

# DB-backed suite: only after verifying the dedicated isolated service.
$env:TEST_PG_PASSWORD = "<test-only password>"
docker compose --profile test-isolated up -d postgres-test-isolated
$PgBaseUrl = "postgresql://muse_test:<url-encoded-test-password>@127.0.0.1:55434"
.\.venv\Scripts\python -m pytest -q --pg-base-url $PgBaseUrl

.\.venv\Scripts\ruff check app tests alembic scripts
.\.venv\Scripts\ruff format --check app tests alembic scripts
.\.venv\Scripts\alembic upgrade head --sql | Out-Null  # migration chain renders
# wire manifest: tests/test_wire_manifest.py fails when docs/contracts/wire-manifest.json
# is stale; regenerate with .\.venv\Scripts\python scripts\gen_wire_manifest.py
```

The DB-backed suite is destructive: it drops/recreates tables in `muse_test`
and migration tests drop the `public` schema in `muse_mig`. Verify the
`127.0.0.1:55434` container, volume and port mapping first; the port number
alone is not proof of isolation. `5432`, `5433` and `55433` are rejected;
`pg_start.py` no longer starts embedded PostgreSQL.

Run at minimum `typecheck + lint + test` before pushing; run `test:e2e`
when touching the core loop (wizard, engine, approvals, brain).

## Workflows

- **New screen:** add `frontend/app/(app)/<name>/page.tsx`, add the nav item
  in `frontend/components/system/Sidebar.tsx`, compose from
  `frontend/components/system/` primitives.
- **New execution behavior:** pure function in `frontend/lib/sim/core.ts`,
  apply via `frontend/lib/sim/engine.ts`, cover with
  `frontend/lib/sim/orchestrator.test.ts`. Never put timers or task
  mutations in components.
- **New demo content:** extend `frontend/lib/data/seed-northstar.ts`,
  keep it behind `businessId`, label simulated figures as demo in the UI.
- **Debug E2E:** in dev only, `window.__engine` exposes
  `tickOnce/startLoop/stopLoop/applyApprovalToExecution/…` so tests drive
  the deterministic engine without wall-clock waits. Never used in production
  builds (`components/system/DevEngineHook.tsx` is a dev-only mount).

## Notes

- State is in-memory Zustand: a full page reload resets to seed data.
  E2E tests therefore navigate client-side (sidebar/links) within a journey.
- `frontend/AGENTS.md` is generated by Next.js tooling (`next dev`
  re-adds its block); commit it with your work to keep the tree clean.
