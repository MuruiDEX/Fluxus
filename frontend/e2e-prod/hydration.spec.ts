import { expect, test } from "@playwright/test";

const HYDRATION_PATTERNS = [
  // Matches React's actual hydration warning:
  // "A tree hydrated but some attributes of the server rendered HTML
  //  didn't match the client properties."
  /hydrated/i,
  /didn['’]t match/i,
  /server rendered HTML/i,
];

test("production Home hydrates without console warnings", async ({ page }) => {
  const messages: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      messages.push(`[console.${message.type()}] ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    messages.push(`[pageerror] ${error.message}`);
  });

  await page.goto("/");
  await expect(page.getByText(/Good morning/)).toBeVisible();

  // The dev-only engine hook must not exist in a production bundle.
  expect(
    await page.evaluate(() => (window as unknown as { __engine?: unknown }).__engine),
  ).toBeUndefined();

  // Let any post-hydration console output settle before asserting.
  await page.waitForTimeout(750);

  const hydrationMessages = messages.filter((message) =>
    HYDRATION_PATTERNS.some((pattern) => pattern.test(message)),
  );

  expect(
    hydrationMessages,
    `Hydration diagnostics:\n${messages.join("\n") || "(no console messages)"}`,
  ).toEqual([]);
});
