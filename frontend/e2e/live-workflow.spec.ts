/**
 * LIVE core-workflow journey (Stage 10+, stub-validated runs since 11) —
 * real backend on :8000, real Postgres dev DB, isolated `biz_e2e` context
 * (globalSetup rebuilds the deterministic workflow graph through the
 * production repositories).
 *
 * Covered user paths (all real UI, no mocks): projects list/detail,
 * project PATCH, task PATCH, validating task runs (the deterministic stub
 * serves schema-valid results, so runs complete openly), approval decision,
 * driver recovery of a stale task, manual Brain create, business isolation.
 * No sleeps: every step waits on concrete UI state.
 */
import { expect, test } from "@playwright/test";
import { login } from "./live-helpers";

// One UI login per file (backend throttles repeated logins); the journey
// reuses the saved session. Deleted by globalSetup before every run.
const STATE_PATH = "e2e/.auth-state.json";

const EDITED_GOAL = "Verify the LIVE core workflow end to end. (edited by E2E)";
const EDITED_SUMMARY = "Patched summary via LIVE E2E.";

test.describe("LIVE auth (workflow)", () => {
  test("login lands in the authenticated shell", async ({ page }) => {
    await login(page);
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await page.context().storageState({ path: STATE_PATH });
  });
});

test.describe.serial("LIVE core workflow", () => {
  test.use({ storageState: STATE_PATH });

  test("projects list is isolated to the business", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByText("E2E Workflow Project")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("E2E Run Project")).toBeVisible();
    await expect(page.getByText("E2E Recovery Project")).toBeVisible();
    await expect(page.getByText("E2E Done Project")).toBeVisible();
    // Another business's data never leaks into page content. (The static
    // sidebar DemoFooter keeps demo copy by design — out of scope here.)
    const main = page.locator("main");
    await expect(main.getByText("Launch Kraków Fitness Studio")).toHaveCount(0);
    await expect(main.getByText("Northstar Fitness")).toHaveCount(0);
  });

  test("project detail renders and PATCH persists across reload", async ({ page }) => {
    await page.goto("/projects");
    await page.getByRole("link", { name: /E2E Workflow Project/ }).first().click();
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await page.locator("main").getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Goal").fill(EDITED_GOAL);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved — server confirmed.")).toBeVisible();
    await page.getByRole("button", { name: "Done" }).click();
    await expect(page.getByText(EDITED_GOAL)).toBeVisible();
    await page.reload();
    await expect(page.getByText(EDITED_GOAL)).toBeVisible({ timeout: 30_000 });
  });

  test("task PATCH persists across reload", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.getByText("Run me honestly")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await card.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Summary").fill(EDITED_SUMMARY);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved — server confirmed.")).toBeVisible();
    await page.getByRole("button", { name: "Done" }).click();
    await expect(page.getByText(EDITED_SUMMARY)).toBeVisible();
    await page.reload();
    await expect(page.getByText(EDITED_SUMMARY)).toBeVisible({ timeout: 30_000 });
  });

  test("task run completes with a valid stubbed result", async ({
    page,
  }) => {
    await page.goto("/tasks");
    await expect(page.getByText("Run me honestly")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await card.getByRole("button", { name: "Run", exact: true }).click();
    // The validating provider path returns a schema-valid result envelope,
    // so the run completes openly — asserted, never faked.
    await expect(card.getByText("Completed", { exact: true })).toBeVisible({ timeout: 90_000 });
    await page.reload();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Run me honestly")`);
    await expect(reloaded.getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  });

  test("gated task refuses while the gate is pending", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.getByText("Deploy workflow service")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Deploy workflow service")`);
    await card.getByRole("button", { name: "Run", exact: true }).click();
    // The gate is enforced before any execution — even on a completed task.
    await expect(page.getByText(/Waiting for approval/)).toBeVisible({ timeout: 60_000 });
  });

  test("approval decision records and persists", async ({ page }) => {
    await page.goto("/approvals");
    await expect(page.getByText("Approve workflow deploy")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Approve workflow deploy")`);
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    // The decided row leaves Pending for Approved (other files' pending
    // gates may remain — assert our card, not the empty state).
    await page.getByRole("button", { name: /Approved \(\d+\)/ }).click();
    const decided = page.locator(`main div.grid > div.rounded-2xl:has-text("Approve workflow deploy")`);
    await expect(decided.getByText(/Decided — recorded in the audit trail/)).toBeVisible({
      timeout: 30_000,
    });
    await expect(decided.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: /Approved \(\d+\)/ }).click();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Approve workflow deploy")`);
    await expect(reloaded.getByText(/Decided — recorded in the audit trail/)).toBeVisible({
      timeout: 30_000,
    });
  });

  test("gated task runs after approval (gate open, run completes)", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.getByText("Deploy workflow service")).toBeVisible({ timeout: 30_000 });
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Deploy workflow service")`);
    await card.getByRole("button", { name: "Run", exact: true }).click();
    // Gate open now: the run proceeds and completes on the valid result.
    await expect(card.getByText("Completed", { exact: true })).toBeVisible({ timeout: 90_000 });
    await page.reload();
    const reloaded = page.locator(`main div.grid > div.rounded-2xl:has-text("Deploy workflow service")`);
    await expect(reloaded.getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  });

  test("driver recovers a stale task and completes it", async ({ page }) => {
    await page.goto("/projects");
    await page.getByRole("link", { name: /E2E Recovery Project/ }).first().click();
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("plan-driver").getByRole("button", { name: "Start" }).click();
    // Recovery probe + valid run: the single-task graph ends awaiting
    // finalization. Watching forever would time this out.
    await expect(page.getByText(/All tasks complete/)).toBeVisible({ timeout: 150_000 });
    await page.reload();
    const card = page.locator(`main div.grid > div.rounded-2xl:has-text("Stuck workflow task")`);
    // Task graph on the detail page renders the recovered row.
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await expect(card.getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  });

  test("driver reports terminal state on the completed graph", async ({ page }) => {
    await page.goto("/projects");
    await page.getByRole("link", { name: /E2E Done Project/ }).first().click();
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("plan-driver").getByRole("button", { name: "Start" }).click();
    // No finalize section without a brief deep-link by design; the driver
    // honestly reports the terminal graph instead of inventing one.
    await expect(page.getByText(/finalization is pending/)).toBeVisible({ timeout: 60_000 });
  });

  test("manual Brain entry persists across reload", async ({ page }) => {
    await page.goto("/brain");
    await expect(page.getByText("Server memory for your business")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "Add note" }).click();
    await page.getByPlaceholder("Title").fill("E2E memory probe");
    await page.getByPlaceholder("What should the team remember?").fill("LIVE workflow verified.");
    await page.getByRole("button", { name: "Save to Business Brain" }).click();
    await expect(page.getByText(/Saved to Business Brain as/)).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Done" }).click();
    await expect(page.getByText("E2E memory probe").first()).toBeVisible();
    await page.reload();
    await expect(page.getByText("E2E memory probe").first()).toBeVisible({ timeout: 30_000 });
  });
});

