import { defineConfig, devices } from "@playwright/test";

/**
 * Production-build checks. These do not run in the normal dev E2E suite:
 * the webServer builds a demo bundle, then starts it on its own port.
 */
export default defineConfig({
  testDir: "./e2e-prod",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3211",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run build:demo && npm run start -- -p 3211",
    url: "http://127.0.0.1:3211/",
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
