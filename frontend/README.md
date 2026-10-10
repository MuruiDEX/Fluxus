# Frontend — MUSE SPARK application

The entire product lives here: Next.js 16 App Router + React 19 + TypeScript
strict + Tailwind CSS v4 + Zustand + Vitest + Playwright.

- `app/` — routes (`(app)` shell: Home, Office, Projects, Tasks, Agents,
  Approvals, Brain, Marketplace, Analytics + deferred placeholders).
- `components/system/` — shared design system extracted from the UX reference.
- `lib/` — `domain/` (model), `store/` (Zustand), `sim/` (deterministic
  orchestration engine + Team Lead), `data/` (Northstar Fitness demo seed).
- `e2e/` — Playwright browser tests for the primary journey.
- Configs + `package.json`/`package-lock.json` + `node_modules` belong here,
  not in the repo root.

Run from here (`npm run dev|build|test|…`) or from the repo root
(`npm run dev|build|test|…` — thin wrappers). Full map:
[`../docs/architecture.md`](../docs/architecture.md).
