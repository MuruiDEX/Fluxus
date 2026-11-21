import { spawn, type ChildProcess } from "node:child_process";
import { expect, request, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import { apiOrigin, autonomousEnv, backendDirectory, stubOrigin } from "./autonomous-env";

const business = "/api/v1/businesses/biz_autonomous";
const projectPath = (key: string) => `${business}/projects/auto_${key}`;
const panel = (page: Page) => page.getByRole("region", { name: "Project execution" });
let api: APIRequestContext;
let stub: APIRequestContext;
let csrf: string;
let worker: ChildProcess | null;
let sawWorkerStart = false;

function startWorker() {
  sawWorkerStart = false;
  const child = spawn("python", ["-m", "app.execution.worker"], {
    cwd: backendDirectory,
    env: { ...process.env, ...autonomousEnv(), PYTHONUNBUFFERED: "1" },
    windowsHide: true,
  });
  child.stdout?.on("data", (data: Buffer) => {
    if (data.toString().includes('"worker starting"')) sawWorkerStart = true;
  });
  worker = child;
}

async function stopWorker() {
  const child = worker;
  worker = null;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  child.kill("SIGKILL");
  await exited;
}

test.beforeAll(async () => {
  api = await request.newContext({ baseURL: apiOrigin });
  stub = await request.newContext({ baseURL: stubOrigin });
  const login = await api.post("/api/v1/auth/login", {
    data: { email: "autonomous@example.test", password: "isolated-autonomous-test-1" },
  });
  expect(login.ok()).toBe(true);
  csrf = (await login.json()).csrf_token;
  startWorker();
  await expect.poll(() => sawWorkerStart).toBe(true);
});
test.afterAll(async () => {
  await stopWorker();
  await api?.dispose();
  await stub?.dispose();
});

async function openProject(browser: Browser, key: string) {
  const context = await browser.newContext({
    baseURL: "http://127.0.0.1:3000", storageState: await api.storageState(),
  });
  const page = await context.newPage();
  await page.goto(`/projects/auto_${key}`);
  return { context, page };
}

async function run(key: string) {
  const response = await api.get(`${projectPath(key)}/run`);
  expect(response.ok()).toBe(true);
  return response.json();
}

async function taskStatus(key: string, id: string) {
  return (await (await api.get(`${projectPath(key)}/tasks/${id}`)).json()).status;
}

async function count(marker: string) {
  return (await (await stub.get("/autonomy/counts")).json())[marker] ?? 0;
}

async function release(marker: string) {
  expect((await stub.post(`/autonomy/release/${marker}`)).status()).toBe(204);
}

async function decide(key: string, decision: "approve" | "reject" = "approve") {
  expect((await api.post(`${business}/approvals/auto_gate_${key}/${decision}`, {
    headers: { "X-CSRF-Token": csrf },
  })).ok()).toBe(true);
}

test("A · the worker advances a chain with every browser context closed", async ({ browser }) => {
  const { context, page } = await openProject(browser, "a");
  await panel(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(() => count("a1")).toBe(1);
  expect((await run("a")).activeTaskId).toBe("a1");
  expect(await count("a2")).toBe(0);
  await context.close();
  expect(browser.contexts()).toHaveLength(0);
  await release("a1");
  await expect.poll(async () => (await run("a")).state).toBe("completed");
  expect(await Promise.all(["a1", "a2", "a3"].map(count))).toEqual([1, 1, 1]);
  const reopened = await openProject(browser, "a");
  await expect(panel(reopened.page).getByText("3/3 tasks complete")).toBeVisible();
  await panel(reopened.page).getByRole("button", { name: "Finalize brief" }).click();
  await expect(reopened.page.getByTestId("final-report")).toBeVisible();
  await reopened.context.close();
});

test("B · approval continues gated work after an independent branch, rejection blocks", async ({ browser }) => {
  const first = await openProject(browser, "b");
  await panel(first.page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(async () => (await run("b")).state).toBe("waiting_approval");
  await expect(panel(first.page).getByText("Waiting for approval", { exact: true })).toBeVisible();
  expect(await count("b2")).toBe(1);
  expect(await count("b3")).toBe(0);
  await decide("b");
  await expect.poll(async () => (await run("b")).state).toBe("completed");
  expect(await count("b3")).toBe(1);
  await first.context.close();
  const rejected = await openProject(browser, "reject");
  await panel(rejected.page).getByRole("button", { name: "Start", exact: true }).click();
  await decide("reject", "reject");
  await expect.poll(async () => (await run("reject")).reasonCode).toBe("approval_rejected");
  expect(await count("r1")).toBe(0);
  await rejected.context.close();
});

test("C · Pause drains admitted work and explicit Resume continues", async ({ browser }) => {
  const { context, page } = await openProject(browser, "c");
  await panel(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(() => count("c1")).toBe(1);
  await panel(page).getByRole("button", { name: "Pause", exact: true }).click();
  await expect.poll(async () => (await run("c")).state).toBe("paused");
  await release("c1");
  await expect.poll(() => taskStatus("c", "c1")).toBe("completed");
  await expect.poll(async () => (await run("c")).activeTaskId).toBeNull();
  expect(await count("c2")).toBe(0);
  await panel(page).getByRole("button", { name: "Resume", exact: true }).click();
  await expect.poll(async () => (await run("c")).state).toBe("completed");
  expect(await count("c2")).toBe(1);
  await context.close();
});

test("D · Cancel drains committed work and approval cannot restart it", async ({ browser }) => {
  const { context, page } = await openProject(browser, "d");
  await panel(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(() => count("d1")).toBe(1);
  await panel(page).getByRole("button", { name: "Cancel run" }).click();
  await panel(page).getByRole("button", { name: "Confirm cancellation" }).click();
  await expect.poll(async () => (await run("d")).state).toBe("cancelled");
  await release("d1");
  await expect.poll(() => taskStatus("d", "d1")).toBe("completed");
  await decide("d");
  expect((await run("d")).state).toBe("cancelled");
  expect(await count("d2")).toBe(0);
  await context.close();
});

test("E · a definite failure exposes Retry after the active slot clears", async ({ browser }) => {
  const { context, page } = await openProject(browser, "e");
  await panel(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(async () => (await run("e")).state).toBe("needs_attention");
  const failed = await run("e");
  expect(failed).toMatchObject({ activeTaskId: null, retryTargetTaskId: "e1", retryTargetGeneration: 1 });
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/run/retry") && response.ok()),
    panel(page).getByRole("button", { name: "Retry task" }).click(),
  ]);
  expect((await api.post(`${projectPath("e")}/run/retry`, {
    headers: { "X-CSRF-Token": csrf },
    data: { expectedVersion: failed.version, taskId: "e1", expectedTaskGeneration: 1 },
  })).status()).toBe(409);
  await expect.poll(async () => (await run("e")).state).toBe("completed");
  expect(await count("e1")).toBe(2);
  await context.close();
});

test("F · a worker restart preserves settled work and parks the interrupted call", async ({ browser }) => {
  const { context, page } = await openProject(browser, "f");
  await panel(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect.poll(() => count("f1")).toBe(1);
  expect(await taskStatus("f", "f0")).toBe("completed");
  await stopWorker();
  await release("f1");
  startWorker();
  await expect.poll(async () => (await run("f")).reasonCode).toBe("ambiguous_execution");
  expect(await Promise.all(["f0", "f1", "f2"].map(count))).toEqual([1, 1, 0]);
  expect((await run("f")).allowedActions).not.toContain("retry");
  await expect(panel(page).getByText("Needs attention", { exact: true })).toBeVisible();
  await context.close();
});
