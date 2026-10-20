/**
 * LIVE intake journey (Stage 11) — real backend on :8000, deterministic
 * OpenAI-compatible stub on :8099 (test-only provider config), real Postgres
 * dev DB, isolated `biz_e2e` context.
 *
 * Covered user paths (all real UI, no mocks): goal → Team-Lead questions →
 * answers → brief review → plan → project execution with an approval gate →
 * finalize → Brain learning, plus reload persistence. Negative paths assert
 * honest errors with nothing persisted. No sleeps: every step waits on
 * concrete UI state.
 */
import { expect, test } from "@playwright/test";
import { ensureAuth, sidebarGo } from "./live-helpers";

// One UI login per file (backend throttles repeated logins); the journey
// reuses the saved session. Deleted by globalSetup before every run.
const STATE_PATH = "e2e/.auth-state.json";
const GOAL = "Launch the E2E intake slice.";

test.describe("LIVE auth (intake)", () => {
  test("login lands in the authenticated shell", async ({ page }) => {
    await ensureAuth(page);
    await expect(page.getByText("no backend connected")).toHaveCount(0);
    await page.context().storageState({ path: STATE_PATH });
  });
});

test.describe.serial("LIVE intake to Brain", () => {
  test.use({ storageState: STATE_PATH });

  test("empty goal is rejected without any request", async ({ page }) => {
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("Describe the goal first.")).toBeVisible();
  });

  test("invalid generation fails openly with nothing persisted", async ({ page }) => {
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByPlaceholder("I want to launch a fitness studio in Krakow.").fill(
      "Launch it. [e2e-invalid]",
    );
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("Model output failed validation.")).toBeVisible({
      timeout: 60_000,
    });
  });

  test("provider outage fails openly with nothing persisted", async ({ page }) => {
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByPlaceholder("I want to launch a fitness studio in Krakow.").fill(
      "Launch it. [e2e-down]",
    );
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("LLM request failed.")).toBeVisible({ timeout: 60_000 });
  });

  test("unknown brief resume is a clean error, not a fake brief", async ({ page }) => {
    await page.goto("/projects/new?brief=ghost-brief-xyz");
    // Server wording: uniform 404 never distinguishes missing from foreign.
    await expect(page.getByText("Brief not found in scope.")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("goal → questions → answers → brief review", async ({ page }) => {
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByPlaceholder("I want to launch a fitness studio in Krakow.").fill(GOAL);
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("Who is the offer for?")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("What does success look like in numbers?")).toBeVisible();
    await page.getByLabel("Who is the offer for?").fill("Newcomers");
    await page.getByLabel("What does success look like in numbers?").fill("More signups");
    await page.getByRole("button", { name: "Review brief" }).click();
    // Brief review renders the server-validated summary (stub-static here),
    // not the echo and not the raw goal text.
    await expect(page.getByText("Desired outcome")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("E2E intake goal")).toBeVisible();
  });

  test("plan builds the project and links to it", async ({ page }) => {
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByPlaceholder("I want to launch a fitness studio in Krakow.").fill(GOAL);
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("Who is the offer for?")).toBeVisible({ timeout: 60_000 });
    await page.getByLabel("Who is the offer for?").fill("Newcomers");
    await page.getByLabel("What does success look like in numbers?").fill("More signups");
    await page.getByRole("button", { name: "Review brief" }).click();
    await expect(page.getByText("Desired outcome")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Build plan & project" }).click();
    await expect(page.getByText("Planned — server confirmed.")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("E2E Intake Project")).toBeVisible();
    await page.getByRole("link", { name: "Open project" }).click();
    // Deep link carries both ids for the driver and finalization.
    await expect(page).toHaveURL(/\/projects\/[^/]+\?brief=[^&]+/);
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Prepare intake groundwork").first()).toBeVisible();
    await expect(page.getByText("Ship intake slice").first()).toBeVisible();
  });

  test("execution runs, gate approves, finalize reports to Brain", async ({ page }) => {
    // Full cycle in one test: leaving the page stops the driver (by design),
    // so the ?brief= deep link captured at planning time is reused to return.
    await page.goto("/projects/new");
    await expect(page.getByText("What do you want to accomplish?")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByPlaceholder("I want to launch a fitness studio in Krakow.").fill(GOAL);
    await page.getByRole("button", { name: "Ask the Team Lead" }).click();
    await expect(page.getByText("Who is the offer for?")).toBeVisible({ timeout: 60_000 });
    await page.getByLabel("Who is the offer for?").fill("Newcomers");
    await page.getByLabel("What does success look like in numbers?").fill("More signups");
    await page.getByRole("button", { name: "Review brief" }).click();
    await expect(page.getByText("E2E outcome", { exact: true })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Build plan & project" }).click();
    await expect(page.getByText("Planned — server confirmed.")).toBeVisible({ timeout: 60_000 });
    const projectHref = await page.getByRole("link", { name: "Open project" }).getAttribute("href");
    expect(projectHref).toMatch(/\/projects\/[^/]+\?brief=[^&]+/);
    await page.getByRole("link", { name: "Open project" }).click();
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("plan-driver").getByRole("button", { name: "Start" }).click();
    // First task runs; the gated one parks for approval.
    await expect(page.getByText(/Waiting for approval/)).toBeVisible({ timeout: 120_000 });
    // Direct navigation (the sidebar badge renames the link): leaving stops
    // the driver by design; we resume explicitly after deciding.
    await page.goto("/approvals");
    await expect(page.getByText("Approve intake slice").first()).toBeVisible({ timeout: 30_000 });
    // Both intake runs of this file gate identically-titled approvals: decide
    // every pending one, leaving other files' fixture gates untouched (each
    // click is a real audited decision). Settle between clicks: the list
    // mutates as rows leave Pending.
    for (let i = 0; i < 5; i++) {
      const cards = page.locator('main div.grid > div.rounded-2xl:has-text("Approve intake slice")');
      const n = await cards.count();
      if (n === 0) break;
      await cards.first().getByRole("button", { name: "Approve", exact: true }).click();
      await expect
        .poll(
          () =>
            page
              .locator('main div.grid > div.rounded-2xl:has-text("Approve intake slice")')
              .count(),
          { timeout: 30_000 },
        )
        .toBeLessThan(n);
    }
    // Our intake gates are decided; the fixture gate from another file may
    // still be pending — assert our card on the Approved tab instead.
    await page.getByRole("button", { name: /Approved \(\d+\)/ }).click();
    const decided = page.locator('main div.grid > div.rounded-2xl:has-text("Approve intake slice")');
    await expect(decided.getByText(/Decided — recorded in the audit trail/).first()).toBeVisible({
      timeout: 30_000,
    });

    // Returning reloads the page (fresh store): start again explicitly.
    await page.goto(projectHref!);
    await expect(page.getByText("Task graph")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("plan-driver").getByRole("button", { name: "Start" }).click();
    await expect(page.getByRole("button", { name: "Finalize brief" })).toBeVisible({
      timeout: 180_000,
    });
    await page.getByRole("button", { name: "Finalize brief" }).click();
    await expect(page.getByTestId("final-report")).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText("Final report")).toBeVisible();
    await expect(page.getByText("Learnings saved to Brain (1)")).toBeVisible();
    await expect(page.getByText("E2E intake learning")).toBeVisible();

    await sidebarGo(page, "Business Brain", /\/brain$/);
    await expect(page.getByText("E2E intake learning").first()).toBeVisible({ timeout: 30_000 });
    await page.reload();
    await expect(page.getByText("E2E intake learning").first()).toBeVisible({ timeout: 30_000 });
  });
});

