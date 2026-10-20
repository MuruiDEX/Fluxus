/**
 * LIVE marketplace hire journey (Stage 9) — real backend on :8000, real
 * Postgres dev DB, isolated `biz_e2e` context (globalSetup resets hires).
 *
 * No sleeps: every step waits on concrete UI state or API answers.
 * DEMO flows are untouched (default playwright config, journey.spec.ts).
 */
import { expect, test } from "@playwright/test";
import { login, sidebarGo } from "./live-helpers";

// One UI login per run (backend throttles repeated logins); the journey
// reuses the saved session. Deleted by globalSetup before every run.
const STATE_PATH = "e2e/.auth-state.json";

test.describe("LIVE auth", () => {
  test("login lands in the authenticated shell", async ({ page }) => {
    await login(page);
    // Authenticated shell: no unreachable-backend banner, no login redirect.
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await page.context().storageState({ path: STATE_PATH });
  });
});

test.describe.serial("LIVE marketplace hire", () => {
  test.use({ storageState: STATE_PATH });

  test("saved session opens the shell without demo banner", async ({ page }) => {
    await page.goto("/marketplace");
    await expect(page.getByText("Available now")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await expect(page.getByText("Live · server").first()).toBeVisible();
    await expect(page.getByText("SEO Specialist AI")).toBeVisible();
  });

  test("hire succeeds and the button flips without reload", async ({ page }) => {
    await page.goto("/marketplace/seo-specialist");
    const hire = page.getByRole("button", { name: /Hire/ });
    await expect(hire).toBeVisible();
    await hire.click();
    // Server-confirmed hire: no manual refresh anywhere in this flow.
    await expect(page.getByText("On your team")).toBeVisible();
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
  });

  test("hired agent appears in the team via client-side nav", async ({ page }) => {
    await page.goto("/marketplace/seo-specialist");
    await expect(page.getByText("On your team")).toBeVisible();
    await sidebarGo(page, "Agents", /\/agents$/);
    // Team cards render the agent role; list order follows server ids
    // (uuid), so select the hired card by its role, not by position.
    const cardLink = page.locator('main a[href^="/agents/"]', { hasText: "SEO" }).first();
    await expect(cardLink).toBeVisible();
    await cardLink.click();
    await expect(page.getByText("SEO Specialist AI").first()).toBeVisible();
  });

  test("re-hire shows hired state, not a second agent", async ({ page }) => {
    await page.goto("/marketplace");
    await expect(page.getByText("SEO Specialist AI")).toBeVisible({ timeout: 30_000 });
    // The hired row flips to its terminal state; unrelated rows keep Hire.
    // (Other spec files may hire other listings in the same run, so assert
    // per-card state, never a global button count.)
    const hiredCard = page.locator('main div.grid > div:has-text("SEO Specialist AI")').first();
    await expect(hiredCard.getByText("On your team")).toBeVisible({ timeout: 30_000 });
    await expect(hiredCard.getByRole("button", { name: /Hire/ })).toHaveCount(0);
    const cfoCard = page.locator('main div.grid > div:has-text("CFO AI")').first();
    await expect(cfoCard.getByRole("button", { name: /Hire/ })).toHaveCount(1);
  });

  test("pack listing explains itself and offers install, never hire", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    await expect(page.getByText("What this pack installs")).toBeVisible({ timeout: 30_000 });
    // Install state depends on spec order in the shared biz_e2e scope: either
    // the install control or the terminal installed badge is present.
    await expect(
      page.getByRole("button", { name: /Install Pack/ }).or(page.getByText(/Installed/).first()),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
    await expect(page.getByText("On your team")).toHaveCount(0);
  });

  test("unknown slug is a clean not-found, not a fake success", async ({ page }) => {
    await page.goto("/marketplace/ghost-slug-xyz");
    await expect(page.getByText("Listing not found")).toBeVisible();
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
  });

  test("reload keeps server-consistent hired state", async ({ page }) => {
    await page.goto("/marketplace/seo-specialist");
    await expect(page.getByText("On your team")).toBeVisible();
    await page.reload();
    await expect(page.getByText("On your team")).toBeVisible();
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
  });

  test("expired session falls back to login, never fake LIVE", async ({ page }) => {
    await page.goto("/marketplace");
    await expect(page.getByText("Available now")).toBeVisible();
    await page.context().clearCookies();
    await page.reload();
    await expect(page).toHaveURL(/\/login/);
  });
});
