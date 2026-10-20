/**
 * Shared LIVE E2E helpers (Stage 9+) — real backend, no mocks.
 *
 * login() performs a real UI login with hydration-race tolerance: the dev
 * and production servers compile on demand, so the first paint may be
 * pre-hydration HTML where a click natively submits (page reloads to
 * /login?) instead of running React login. The loop refills and retries on
 * state, never on sleeps.
 */
import { expect, type Page } from "@playwright/test";

export const EMAIL = process.env.E2E_EMAIL ?? "e2e@example.com";
export const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-pass-1";
export const HOME_URL = /^http:\/\/127\.0\.0\.1:3000\/($|\?)/;

export async function login(page: Page) {
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  for (let attempt = 0; attempt < 4; attempt++) {
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    const stable = await page
      .waitForFunction(
        (email) => {
          const el = document.querySelector('input[type="email"]') as HTMLInputElement | null;
          if (!el || el.value !== email) return false;
          return new Promise<boolean>((resolve) => {
            setTimeout(() => resolve(el.value === email), 400);
          });
        },
        EMAIL,
        { timeout: 8000 },
      )
      .then(() => true)
      .catch(() => false);
    if (!stable) continue;
    // NOTE: /login mounts outside AuthGate, so no session check runs here
    // and the submit button keeps its "Please wait…" label until login
    // succeeds. The button still submits — click the form button itself.
    await page.locator("form").getByRole("button").first().click();
    try {
      await expect(page).toHaveURL(HOME_URL, { timeout: 15_000 });
      break;
    } catch {
      // Still on /login: either a server error (alert = fail loudly below)
      // or a pre-hydration native submit (retry refills after reload).
      if (await page.getByRole("alert").count()) break;
    }
  }
  await expect(page).toHaveURL(HOME_URL);
}

export async function sidebarGo(page: Page, name: string, url: RegExp) {
  await page.locator("aside").getByRole("link", { name, exact: true }).click();
  await expect(page).toHaveURL(url);
}

/**
 * Reuse the saved session when valid; fall back to a full UI login when the
 * spec runs standalone (empty state file) or the session lapsed. Keeps
 * login POSTs near one per run — the backend throttles repeated logins.
 */
export async function ensureAuth(page: Page) {
  await page.goto("/marketplace");
  try {
    await expect(page).toHaveURL(/\/login/, { timeout: 8000 });
  } catch {
    // Authenticated via the saved session: prove the shell, not the form.
    await expect(page.getByText("Available now")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    return;
  }
  await login(page);
}
