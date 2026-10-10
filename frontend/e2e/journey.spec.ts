import { expect, test, type Page } from "@playwright/test";

const SCOPE = { businessId: "biz_northstar", projectId: "proj_krakow_launch" };

/** Navigate and wait for React hydration (proof: dev engine hook attached). */
async function ready(page: Page, url: string) {
  await page.goto(url);
  await page.waitForFunction(
    () => (window as unknown as { __engine?: unknown }).__engine,
    undefined,
    { timeout: 20_000 },
  );
}

async function tick(page: Page, scope: { businessId: string; projectId: string }, n: number) {
  await page.evaluate(
    ({ scope, n }) => {
      const engine = (window as unknown as { __engine: { tickOnce: (s: unknown) => boolean } }).__engine;
      for (let i = 0; i < n; i++) engine.tickOnce(scope);
    },
    { scope, n },
  );
}

/** Answer Team Lead questions until the brief appears (max 8 rounds). */
async function answerUntilBrief(page: Page) {
  for (let i = 0; i < 8; i++) {
    if (await page.getByText("Business brief").count()) return;
    const review = page.getByRole("button", { name: /Review brief/ });
    if (await review.count()) {
      await review.click();
      return;
    }
    const input = page.locator("main input").first();
    const before = await input.getAttribute("placeholder");
    // Prefer the offered option chips when present (model/budget questions).
    const chips = page.locator("main button.rounded-full");
    if (await chips.count()) {
      await chips.first().click();
    } else {
      await input.fill("Own products and premium coaching");
    }
    // Submit via the arrow button (no animation-stale input risk).
    await page.locator("main").getByRole("button", { name: /Continue|Answer/ }).click();
    // Wait for the question transition to settle before the next round.
    await page
      .waitForFunction(
        (prev) => {
          const el = document.querySelector("main input");
          const text = document.body.innerText;
          return (
            text.includes("Business brief") ||
            text.includes("Review brief") ||
            (el && el.getAttribute("placeholder") !== prev)
          );
        },
        before,
        { timeout: 8000 },
      )
      .catch(() => {});
  }
  await expect(page.getByText("Business brief")).toBeVisible();
}

test("goal в†’ contextual questions в†’ brief в†’ launch", async ({ page }) => {
  await ready(page, "/projects/new?goal=" + encodeURIComponent("I want to launch a fitness studio in Krakow."));
  // Krakow detected: country/city skipped, first question branches to business model.
  await expect(page.getByText("Will you create your own products")).toBeVisible();
  await answerUntilBrief(page);
  // Gap detection + launch.
  await expect(page.getByText("Gap: SEO Specialist AI")).toBeVisible();
  await page.getByRole("button", { name: /Build My AI Team & Launch/ }).click();
  await expect(page).toHaveURL(/\/projects\/proj_custom_/);
  await expect(page.getByText("Task graph")).toBeVisible();
});

test("parallel execution в†’ approval gates в†’ completion в†’ brain report", async ({ page }) => {
  await ready(page, "/projects/proj_krakow_launch");
  await expect(page.getByText("Task graph")).toBeVisible();

  // Drive deterministic ticks: Financial + Brand finish, Marketing/Web hit gates.
  await tick(page, SCOPE, 25);
  await expect(page.getByText("Gate: Publish Google Ads campaign @ 90%")).toBeVisible();
  await expect(page.getByText("Gate: Deploy website to production @ 80%")).toBeVisible();

  // Resolve both gates through the real inbox UI (sidebar = client-side nav, state kept).
  await page.locator("aside").getByRole("link", { name: /Approvals/ }).click();
  await expect(page).toHaveURL(/\/approvals/);
  const approveButtons = page.getByRole("button", { name: "Approve", exact: true });
  await expect(approveButtons.first()).toBeVisible();
  const count = await approveButtons.count();
  for (let i = 0; i < count; i++) {
    await approveButtons.first().click();
  }
  // Resolved cards leave the Pending tab — the inbox is empty.
  await expect(page.getByText("Nothing here")).toBeVisible();

  // Back to the project through the UI; finish remaining work.
  await page.getByRole("link", { name: "Projects" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await page.getByRole("link", { name: "Launch Kraków Fitness Studio" }).click();
  await expect(page.getByText("Task graph")).toBeVisible();
  await tick(page, SCOPE, 40);
  await expect(page.getByTestId("project-status")).toHaveText("Completed");

  // The final report landed in Business Brain.
  await page.getByRole("link", { name: "Business Brain" }).click();
  await expect(page).toHaveURL(/\/brain/);
  await expect(page.getByText("Final report — Launch Kraków Fitness Studio")).toBeVisible();
});

test("marketplace hire lands on the team", async ({ page }) => {
  await ready(page, "/marketplace/seo-specialist");
  await page.getByRole("button", { name: /Hire/ }).click();
  await expect(page.getByText("On your team")).toBeVisible();
  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page).toHaveURL(/\/agents$/);
  await expect(page.getByText("SEO Specialist").first()).toBeVisible();
  // The hire propagated to the team store: no marketplace-only badge remains.
  // (exact + case-sensitive: avoids matching nav/breadcrumb "Marketplace" text)
  await expect(page.locator("main").getByText("MARKETPLACE", { exact: true })).toHaveCount(0);
});

test("task failure and retry operate on shared engine state", async ({ page }) => {
  await ready(page, "/tasks");
  await page.getByRole("button", { name: "Simulate failure" }).first().click();
  await expect(page.getByText("Failed", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /Retry/ }).first().click();
  await expect(page.getByRole("button", { name: "Simulate failure" }).first()).toBeVisible();
});

test("office views, command bar, dark mode and mobile nav", async ({ page }) => {
  await ready(page, "/office");
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(page.getByText("Execution timeline")).toBeVisible();
  await page.getByRole("button", { name: "Whiteboard", exact: true }).click();
  await expect(page.getByText("Plan whiteboard")).toBeVisible();

  // Command bar opens with Ctrl+K and starts a Team Lead flow.
  await page.keyboard.press("Control+k");
  await expect(page.getByPlaceholder("What do you want to accomplish?")).toBeVisible();
  await page.getByText("Hire an AI sales manager").click();
  await expect(page).toHaveURL(/\/projects\/new\?goal=/);

  // Dark mode toggles a real theme class (no white cards in dark mode).
  await ready(page, "/");
  await page.getByLabel("Toggle theme").click();
  await expect(page.locator("html.dark")).toHaveCount(1);

  // Mobile: hamburger opens the navigation drawer.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("Open navigation").click();
  await expect(page.getByRole("link", { name: "Marketplace" }).first()).toBeVisible();
});
