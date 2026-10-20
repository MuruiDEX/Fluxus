/**
 * LIVE pack install journey (Stage 15) — real backend on :8000, real Postgres
 * dev DB, isolated `biz_e2e` context (globalSetup resets pack installations).
 *
 * No sleeps: every step waits on concrete UI state. DEMO pack install parity
 * stays in the default (journey) config.
 */
import { expect, test } from "@playwright/test";
import { ensureAuth, sidebarGo } from "./live-helpers";

const STATE_PATH = "e2e/.auth-state.json";

test.describe("LIVE auth (packs)", () => {
  test("login lands in the authenticated shell", async ({ page }) => {
    await ensureAuth(page);
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await page.context().storageState({ path: STATE_PATH });
  });
});

test.describe.serial("LIVE pack install", () => {
  test.use({ storageState: STATE_PATH });

  test("pack detail shows its agents, workflow, and an install control", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    await expect(page.getByText("What this pack installs")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Included agents (2)")).toBeVisible();
    await expect(page.getByText("Fitness Client Onboarding")).toBeVisible();
    await expect(page.getByRole("button", { name: /Install Pack/ })).toBeVisible();
    // Packs never render the employee hire control.
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
  });

  test("install flips to Installed without a reload", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    const install = page.getByRole("button", { name: /Install Pack/ });
    await expect(install).toBeVisible();
    await install.click();
    await expect(page.getByText(/Installed v1\.0\.0/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Install Pack/ })).toHaveCount(0);
  });

  test("marketplace card reflects installed state", async ({ page }) => {
    await page.goto("/marketplace");
    await expect(page.getByText("Available now")).toBeVisible({ timeout: 30_000 });
    const card = page.locator('main div.grid > div:has-text("Fitness Business Pack")').first();
    await expect(card.getByText("Installed")).toBeVisible();
  });

  test("bundled agents join the team", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    await expect(page.getByText(/Installed v1\.0\.0/)).toBeVisible();
    await sidebarGo(page, "Agents", /\/agents$/);
    await expect(page.locator('main a[href^="/agents/"]', { hasText: "Sales" }).first()).toBeVisible();
    await expect(page.locator('main a[href^="/agents/"]', { hasText: "Support" }).first()).toBeVisible();
  });

  test("workflow project materializes in a safe planning state", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    await expect(page.getByText(/Installed v1\.0\.0/)).toBeVisible();
    await sidebarGo(page, "Projects", /\/projects$/);
    const link = page.getByRole("link", { name: /Fitness Client Onboarding/ }).first();
    await expect(link).toBeVisible({ timeout: 30_000 });
    await link.click();
    await expect(page).toHaveURL(/\/projects\/[^/]+$/);
    await expect(page.getByTestId("project-status")).toHaveText("Planning");
    // Installed workflow tasks are queued, never auto-run.
    await expect(page.getByText("Qualify new lead", { exact: true })).toBeVisible();
    await expect(page.getByText("Book onboarding session", { exact: true })).toBeVisible();
  });

  test("reload keeps the server-confirmed installed state", async ({ page }) => {
    await page.goto("/marketplace/fitness-pack");
    await expect(page.getByText(/Installed v1\.0\.0/)).toBeVisible();
    await page.reload();
    await expect(page.getByText(/Installed v1\.0\.0/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Install Pack/ })).toHaveCount(0);
  });
});
