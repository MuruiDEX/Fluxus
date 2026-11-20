/**
 * Background refresh semantics for the LIVE stores (Phase 4).
 *
 * The poller itself is DOM-bound, but everything that makes it safe is in the
 * store: in-place application (no cleared slice / error flash), scope guards,
 * one request per slice, and discarding an answer when a write landed while
 * the poll was in flight.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveProjects, useLiveTasks } from "@/lib/store/live";

let projectIds: string[] = [];
let taskStatus = "not_started";
let delayMs = 0;
let fail = false;
let calls: string[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function projectPage(ids: string[]) {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      title: id,
      goal: "",
      status: "in_progress",
      progress: 10,
      budget: "100.00",
      aiBudget: "50.00",
      aiSpent: "1.10",
      deadline: "",
      teamIds: [],
      milestones: [],
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function taskPage(status: string) {
  return {
    items: [
      {
        id: "task_1",
        businessId: "biz_test",
        projectId: "proj_1",
        title: "Task",
        ownerAgentId: "ag_1",
        status,
        progress: 0,
        costAccrued: "0.00",
        etaMinutes: 0,
        summary: "",
        attempts: 0,
        approvalGateId: null,
        gateAt: null,
        costRate: null,
        contract: "v1",
      },
    ],
    total: 1,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

beforeEach(() => {
  vi.unstubAllGlobals();
  projectIds = [];
  taskStatus = "not_started";
  delayMs = 0;
  fail = false;
  calls = [];
  useLiveProjects.getState().reset();
  useLiveTasks.getState().reset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (fail) {
        return json({ error: { code: "network_unreachable", message: "Network unreachable" } }, 503);
      }
      if (/\/projects\?/.test(url)) return json(projectPage(projectIds));
      if (/\/tasks\?/.test(url)) return json(taskPage(taskStatus));
      return json({ error: { code: "x", message: "x" } }, 500);
    }),
  );
});

describe("LIVE refresh", () => {
  it("applies new rows in place without clearing what is on screen", async () => {
    projectIds = ["p1"];
    await useLiveProjects.getState().load("biz_test");
    expect(useLiveProjects.getState().projects).toHaveLength(1);

    projectIds = ["p1", "p2"];
    delayMs = 15;
    const pending = useLiveProjects.getState().refresh("biz_test");
    // While the poll is in flight the page keeps its rows and stays "ready":
    // no skeleton, no empty flash, no error state.
    expect(useLiveProjects.getState().status).toBe("ready");
    expect(useLiveProjects.getState().projects).toHaveLength(1);

    const report = await pending;
    expect(report).toEqual({ outcome: "ok" });
    expect(useLiveProjects.getState().projects).toHaveLength(2);
  });

  it("refuses to refresh a scope the slice is not showing", async () => {
    projectIds = ["p1"];
    await useLiveProjects.getState().load("biz_test");
    const before = calls.length;

    const report = await useLiveProjects.getState().refresh("biz_other");

    expect(report).toEqual({ outcome: "skipped" });
    expect(calls.length).toBe(before);
    expect(useLiveProjects.getState().businessId).toBe("biz_test");
  });

  it("never stacks a second request on a slice that is already busy", async () => {
    projectIds = ["p1"];
    await useLiveProjects.getState().load("biz_test");
    const before = calls.length;
    delayMs = 15;

    const first = useLiveProjects.getState().refresh("biz_test");
    const second = await useLiveProjects.getState().refresh("biz_test");

    expect(second).toEqual({ outcome: "skipped" });
    await first;
    expect(calls.length).toBe(before + 1);
  });

  it("drops a poll answer when a write landed while it was in flight", async () => {
    await useLiveTasks.getState().load("biz_test", "proj_1");
    delayMs = 15;

    const poll = useLiveTasks.getState().refresh("biz_test", "proj_1");
    // A server-confirmed write (execution reconcile, decision, edit) lands
    // before the older poll answer returns.
    useLiveTasks.getState().upsertTask({
      id: "task_1",
      businessId: "biz_test",
      projectId: "proj_1",
      title: "Task",
      ownerAgentId: "ag_1",
      status: "working",
      progress: 60,
      dependsOn: [],
      costAccrued: 0,
      etaMinutes: 0,
      summary: "",
    });
    const report = await poll;

    expect(report).toEqual({ outcome: "skipped" });
    const task = useLiveTasks.getState().tasks.find((t) => t.id === "task_1");
    expect(task?.status).toBe("working");
    expect(task?.progress).toBe(60);
  });

  it("reports a failure without replacing the data already loaded", async () => {
    projectIds = ["p1"];
    await useLiveProjects.getState().load("biz_test");
    fail = true;

    const report = await useLiveProjects.getState().refresh("biz_test");

    expect(report.outcome).toBe("error");
    expect(report.error).toBeTruthy();
    const state = useLiveProjects.getState();
    expect(state.status).toBe("ready");
    expect(state.projects).toHaveLength(1);
    expect(state.error).toBeNull();
  });

  it("refreshes the aggregated task scope across every project", async () => {
    await useLiveTasks.getState().loadAll("biz_test", []);
    expect(useLiveTasks.getState().key).toBe("biz_test/*");

    projectIds = ["p1", "p2"];
    const report = await useLiveTasks.getState().refreshAll("biz_test", ["proj_1", "proj_2"]);

    // Two project task pages fetched, one row per project (same id deduped).
    expect(report).toEqual({ outcome: "ok" });
    expect(calls.filter((url) => /\/tasks\?/.test(url))).toHaveLength(2);
    expect(useLiveTasks.getState().tasks).toHaveLength(1);
  });
});
