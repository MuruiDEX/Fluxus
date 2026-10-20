import { defineConfig, devices } from "@playwright/test";

/**
 * LIVE E2E (Stage 9) — real backend + real Postgres, no mocks.
 *
 * - baseURL is http://127.0.0.1:3000 (NOT localhost): the backend CORS
 *   allowlist covers exactly this origin, and SameSite=Lax session cookies
 *   only flow same-site (localhost <-> 127.0.0.1 are cross-site by design).
 * - The storefront runs from the production build (`next start`), not the
 *   dev server: no HMR websocket, standard hydration, and the exact release
 *   artifact under test (NEXT_PUBLIC_API_URL is baked at build time —
 *   rebuild after changing it; the default equals the URL below).
 * - The backend API runs in-process via webServer against the dev database
 *   (:5432/muse); globalSetup migrates + seeds + resets the isolated
 *   `biz_e2e` hiring context.
 * - The backend talks to a deterministic OpenAI-compatible stub (test-only
 *   config; the default mock runtime is untouched): scripted valid answers
 *   for intake/plan/execution/finalize so the full cycle completes without
 *   external keys. Marker-driven negatives ([e2e-invalid], [e2e-down]) cover
 *   the failure paths.
 * - Only live-*.spec.ts files run here; the DEMO journey stays in the
 *   default config. Port 3000 must be free (CORS-fixed origin).
 * - Single worker: all spec files share one backend + one biz_e2e scope,
 *   so files must run serially — parallel workers would race hires,
 *   toggles and task runs against each other (proven flaky in Stage 14).
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /live-.*\.spec\.ts/,
  globalSetup: "./e2e/live-global-setup.ts",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/llm-stub.mjs --port 8099",
      url: "http://127.0.0.1:8099/health",
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/api/v1/health",
      timeout: 120_000,
      cwd: "../backend",
      env: {
        APP_DATABASE_URL: "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse",
        APP_LLM_PROVIDER: "openai_compat",
        APP_LLM_API_KEY: "e2e-dummy-key",
        APP_LLM_BASE_URL: "http://127.0.0.1:8099",
        APP_LLM_MODEL: "e2e-stub",
        // Stage 16: a budgeted project (ai_budget>0) fails closed without a
        // price. Price the deterministic stub so budgeted runs execute (and
        // accrue a confirmed cost) exactly as a real priced provider would.
        APP_USAGE_TARIFFS_JSON:
          '[{"provider":"openai_compat","model":"e2e-stub","input_per_1k":"0.01","output_per_1k":"0.03","source":"e2e"}]',
      },
      // Never reuse: a stale server (wrong DB, old code) would fake results.
      reuseExistingServer: false,
    },
    {
      command: "npm run start -- --port 3000",
      url: "http://127.0.0.1:3000/",
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
      },
      reuseExistingServer: false,
    },
  ],
});
