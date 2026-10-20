# MUSE SPARK — AI Business Office

«You run the business. Your AI company runs the work.»

A premium AI Business Operating System: the owner states a goal, a Team Lead
AI asks only the questions that matter, assembles AI employees, executes work
in parallel with human approval gates, measures results, and remembers
everything in the Business Brain. Marketplace hiring extends the team.

**Status: full-stack local phase.** The frontend runs in two modes: DEMO
(simulated execution engine + Northstar Fitness demo data, clearly labeled)
and LIVE (FastAPI backend on `http://127.0.0.1:8000` via `NEXT_PUBLIC_API_URL`,
with cookie-session auth + membership isolation). Backend stages 1–6 are
implemented (persistence, LLM layer, execution engine + API, session auth);
see [`backend/README.md`](backend/README.md).

## Quick start

```bash
npm run install:all
npm run dev        # → http://localhost:3000
```

| Command | What it does |
|---|---|
| `npm run dev` | dev server |
| `npm run build` / `npm run start` | production build / serve it |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | eslint |
| `npm run test` | Vitest unit tests (engine) |
| `npm run test:e2e` | Playwright Chromium journey tests |
| `npm run test:e2e:live` | Playwright LIVE tests (real backend + Postgres, stub LLM) |

All commands delegate to `frontend/` — you can also run them there directly.

## Layout

- [`frontend/`](frontend/) — the whole application (Next.js 16, React 19,
  TypeScript, Tailwind v4, Zustand). See [`frontend/README.md`](frontend/README.md).
- [`backend/`](backend/) — FastAPI backend (auth, persistence, LLM layer,
  execution + briefs API, Alembic migrations). See [`backend/README.md`](backend/README.md).
- [`docs/architecture.md`](docs/architecture.md) — where everything lives
  and where new code goes.
- [`docs/development.md`](docs/development.md) — install, run, verify, workflows.

## Demo journey (3 minutes)

1. Open `/projects/new` (or press `Ctrl+K`) → *“I want to launch a fitness
   studio in Krakow.”*
2. Answer 2–3 contextual questions → review the brief → hire the recommended
   SEO agent → **Build My AI Team & Launch**.
3. Watch `/office`: parallel tasks, live agent statuses, activity feed.
4. Resolve the gates in `/approvals` → project completes → final report lands
   in `/brain`.

Stack: Next.js 16 App Router · React 19 · TS strict · Tailwind v4 · shadcn-style
primitives · Zustand · Lucide · Framer Motion · Recharts · Vitest · Playwright.
