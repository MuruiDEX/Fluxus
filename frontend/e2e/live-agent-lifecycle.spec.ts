/**
 * LIVE agent lifecycle journey (Stage 14) — real backend on :8000, real
 * Postgres dev DB, isolated `biz_e2e` context.
 *
 * Full cycle through real UI only: Marketplace → Hire → Team → Assign →
 * Disable → blocked Run → Activate → Run → Completed, with reload
 * persistence checks. Uses the support-ai listing (the hire journey owns
 * seo-specialist) so files stay independent within one run.
 *
 * No sleeps: every step waits on concrete UI state or API answers.
 */
import { expect, test } from "@playwright/test";
import { login } from "./live-helpers";

// One UI login per run (backend throttles repeated logins); the journey
// reuses the saved session. Deleted by globalSetup before every run.
const STATE_PATH = "e2e/.auth-state.json";

test.describe("LIVE auth (lifecycle)", () => {
  test("login lands in the authenticated shell", async ({ page }) => {
    await login(page);
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await page.context().storageState({ path: STATE_PATH });
  });
});

test.describe.serial("LIVE agent lifecycle", () => {
  test.use({ storageState: STATE_PATH });

  test("hire creates a real team member", async ({ page }) => {
    await page.goto("/marketplace/support-ai");
    const hire = page.getByRole("button", { name: /Hire/ });
    await expect(hire).toBeVisible();
    await hire.click();
    await expect(page.getByText("On your team")).toBeVisible();
    await expect(page.getByRole("button", { name: /Hire/ })).toHaveCount(0);
  });

  test("team shows the hireling with an active toggle", async ({ page }) => {
    await page.goto("/agents");
    // Team cards render the agent role; the hired row is the Support one.
    await expect(page.getByText("Support", { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });
    const card = page.locator('main div.grid > div:has-text("Support")').first();
    await expect(card.getByRole("button", { name: "Disable", exact: true })).toBeVisible();
    await expect(card.getByText("DISABLED", { exact: true })).toHaveCount(0);
  });

  test("assign an active agent to a task", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.getByText("Run me honestly")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await card.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Owner").selectOption({ label: "Customer Support AI · Support" });
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved — server confirmed.")).toBeVisible();
    await page.getByRole("button", { name: "Done" }).click();
    await page.reload();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await reloaded.getByRole("button", { name: "Edit" }).click();
    // Owner persisted server-side: reopening shows the hired agent selected.
    await expect(page.getByLabel("Owner").locator("option:checked")).toHaveText(
      "Customer Support AI · Support",
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: "Cancel" }).click();
  });

  test("disable blocks staffing and execution", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.getByText("Support", { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });
    const card = page.locator('main div.grid > div:has-text("Support")').first();
    await card.getByRole("button", { name: "Disable", exact: true }).click();
    await expect(card.getByText("DISABLED", { exact: true })).toBeVisible();
    await expect(card.getByText(/takes no new work/)).toBeVisible();

    // Owner picker keeps the current (disabled) owner visible but unpickable…
    await page.goto("/tasks");
    const taskCard = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await taskCard.getByRole("button", { name: "Edit" }).click();
    // The disabled current owner stays visible (context) but carries the
    // disabled attribute — it cannot be (re-)picked.
    await expect(
      page.getByRole("option", { name: "Customer Support AI · Support" }),
    ).toHaveAttribute("disabled", "");
    await page.getByRole("button", { name: "Cancel" }).click();

    // …and the engine refuses a new claim for it instead of running.
    await taskCard.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByText(/is disabled/)).toBeVisible({ timeout: 60_000 });
    await page.reload();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await expect(reloaded.getByText("Not started", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
  });

  test("activate reopens staffing and the task completes", async ({ page }) => {
    await page.goto("/agents");
    const card = page.locator('main div.grid > div:has-text("Support")').first();
    await expect(card.getByText("DISABLED", { exact: true })).toBeVisible({ timeout: 30_000 });
    await card.getByRole("button", { name: "Activate", exact: true }).click();
    await expect(card.getByRole("button", { name: "Disable", exact: true })).toBeVisible({
      timeout: 30_000,
    });

    await page.goto("/tasks");
    const taskCard = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await taskCard.getByRole("button", { name: "Run", exact: true }).click();
    await expect(taskCard.getByText("Completed", { exact: true })).toBeVisible({
      timeout: 90_000,
    });
    await page.reload();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await expect(reloaded.getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  });

  test("agent detail shows the assigned task and history survives", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.getByText("Support", { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });
    const cardLink = page.locator('main a[href^="/agents/"]', { hasText: "Support" }).first();
    await expect(cardLink).toBeVisible();
    await cardLink.click();
    await expect(page.getByText("Customer Support AI").first()).toBeVisible({
      timeout: 30_000,
    });
    // The completed assignment is listed; nothing was deleted by toggling.
    await expect(page.getByText("Run me honestly")).toBeVisible();
    await expect(page.getByText(/Active — can take new work/)).toBeVisible();
  });
});


