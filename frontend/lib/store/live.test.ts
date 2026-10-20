/**
 * LIVE store tests (Stage 7.3) — mocked fetch backend, no network.
 *
 * Covers: successful loads + mapping, business switching (immediate reset,
 * no stale flash), late-response discard (sequence guard, with and without
 * abort delivery), request abortion on supersede, 401/403/404/network/empty
 * states, unknown project ids, agents-cache idempotence, and proof that the
 * simulation engine cannot touch LIVE state.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { isLiveContext, useLiveAgents, useLiveProject, useLiveProjects, useLiveTasks } from "@/lib/store/live";

interface Scenario {
  test: (url: string) => boolean;
  status: number;
  body: unknown;
  delayMs?: number;
}

function mockLive(scenarios: Scenario[], opts?: { honorAbort?: boolean }) {
  const calls: string[] = [];
  const aborted: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url);
      const sc = scenarios.find((s) => s.test(url));
      if (!sc) {
        return new Response(JSON.stringify({ error: { code: "x", message: "x" } }), { status: 500 });
      }
      if (sc.delayMs) await new Promise((r) => setTimeout(r, sc.delayMs));
      if ((opts?.honorAbort ?? true) && init?.signal?.aborted) {
        aborted.push(url);
        throw new DOMException("aborted", "AbortError");
      }
      return new Response(JSON.stringify(sc.body), { status: sc.status });
    }),
  );
  return { calls, aborted };
}

function projectPage(ids: string[]) {
  return {
    items: ids.map((id, i) => ({
      id,
      businessId: "biz_test",
      title: `P${i}`,
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

function taskPage(ids: string[], projectId = "proj_1") {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      projectId,
      title: id,
      ownerAgentId: "ag_1",
      status: "not_started",
      progress: 0,
      costAccrued: "0.00",
      etaMinutes: 0,
      summary: "",
      attempts: 0,
      approvalGateId: null,
      gateAt: null,
      costRate: null,
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function agentPage(ids: string[]) {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      name: `Name ${id}`,
      role: "R",
      responsibilities: [],
      tools: [],
      permissions: {},
      status: "available",
      currentTask: null,
      costPerMonth: 0,
      performance: {},
      hired: true,
      avatarHue: 200,
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function resetLive() {
  useLiveProjects.getState().reset();
  useLiveProject.getState().reset();
  useLiveTasks.getState().reset();
  useLiveAgents.getState().reset();
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetLive();
});

describe("isLiveContext", () => {
  it("is live only with session and business", () => {
    expect(isLiveContext("authenticated", "biz_test")).toBe(true);
    expect(isLiveContext("authenticated", null)).toBe(false);
    expect(isLiveContext("anonymous", "biz_test")).toBe(false);
    expect(isLiveContext("unreachable", "biz_test")).toBe(false);
    expect(isLiveContext("unknown", null)).toBe(false);
  });
});

describe("live projects", () => {
  it("loads, maps money, and resets on business switch without flashing stale data", async () => {
    mockLive([
      {
        test: (u) => /\/projects(\?.*)?$/.test(u),
        status: 200,
        body: projectPage(["proj_1", "proj_2"]),
      },
    ]);
    const store = useLiveProjects.getState();
    await store.load("biz_test");
    let s = useLiveProjects.getState();
    expect(s.status).toBe("ready");
    expect(s.projects.map((p) => p.id)).toEqual(["proj_1", "proj_2"]);
    expect(s.total).toBe(2);
    expect(s.projects[0].aiSpent).toBeCloseTo(1.1);

    mockLive([{ test: () => true, status: 200, body: projectPage(["proj_9"]) }]);
    const pending = useLiveProjects.getState().load("biz_other");
    // Cleared synchronously: the old business never flashes.
    s = useLiveProjects.getState();
    expect(s.status).toBe("loading");
    expect(s.projects).toEqual([]);
    expect(s.businessId).toBe("biz_other");
    await pending;
    expect(useLiveProjects.getState().projects.map((p) => p.id)).toEqual(["proj_9"]);
  });

  it("aborts the superseded request", async () => {
    const { aborted } = mockLive([
      {
        test: (u) => u.includes("biz_test") && /\/projects(\?.*)?$/.test(u),
        status: 200,
        body: projectPage(["stale"]),
        delayMs: 30,
      },
      {
        test: (u) => u.includes("biz_other") && /\/projects(\?.*)?$/.test(u),
        status: 200,
        body: projectPage(["fresh"]),
      },
    ]);
    const store = useLiveProjects.getState();
    const first = store.load("biz_test");
    await store.load("biz_other");
    await first;
    expect(aborted.length).toBe(1);
    expect(useLiveProjects.getState().businessId).toBe("biz_other");
    expect(useLiveProjects.getState().projects.map((p) => p.id)).toEqual(["fresh"]);
  });

  it("discards a late response even when abort is not delivered", async () => {
    mockLive(
      [
        {
          test: (u) => u.includes("biz_slow") && /\/projects(\?.*)?$/.test(u),
          status: 200,
          body: projectPage(["stale"]),
          delayMs: 40,
        },
        {
          test: (u) => u.includes("biz_fast") && /\/projects(\?.*)?$/.test(u),
          status: 200,
          body: projectPage(["fresh"]),
        },
      ],
      { honorAbort: false },
    );
    const store = useLiveProjects.getState();
    const slow = store.load("biz_slow");
    await store.load("biz_fast");
    await slow;
    const s = useLiveProjects.getState();
    expect(s.businessId).toBe("biz_fast");
    expect(s.projects.map((p) => p.id)).toEqual(["fresh"]);
  });

  it("surfaces 403 and network errors with retry", async () => {
    mockLive([
      {
        test: () => true,
        status: 403,
        body: { error: { code: "forbidden", message: "CSRF validation failed." } },
      },
    ]);
    await useLiveProjects.getState().load("biz_test");
    let s = useLiveProjects.getState();
    expect(s.status).toBe("error");
    expect(s.error).toContain("CSRF");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await useLiveProjects.getState().load("biz_test");
    s = useLiveProjects.getState();
    expect(s.status).toBe("error");
    expect(s.projects).toEqual([]);
  });

  it("reads empty lists as ready-empty", async () => {
    mockLive([
      {
        test: () => true,
        status: 200,
        body: { items: [], total: 0, limit: 50, offset: 0, contract: "v1" },
      },
    ]);
    await useLiveProjects.getState().load("biz_test");
    const s = useLiveProjects.getState();
    expect(s.status).toBe("ready");
    expect(s.projects).toEqual([]);
    expect(s.total).toBe(0);
  });
});

describe("live project detail", () => {
  const PROJECT = {
    id: "proj_1",
    businessId: "biz_test",
    title: "P",
    goal: "",
    status: "in_progress",
    progress: 10,
    budget: "100.00",
    aiBudget: "50.00",
    aiSpent: "0.00",
    deadline: "",
    teamIds: [],
    milestones: [],
    contract: "v1",
  };

  it("loads one project and flags unknown ids as notFound (never a fallback)", async () => {
    mockLive([{ test: () => true, status: 200, body: PROJECT }]);
    await useLiveProject.getState().load("biz_test", "proj_1");
    let s = useLiveProject.getState();
    expect(s.status).toBe("ready");
    expect(s.project?.id).toBe("proj_1");
    expect(s.notFound).toBe(false);

    mockLive([
      {
        test: () => true,
        status: 404,
        body: { error: { code: "not_found", message: "Project not found in scope." } },
      },
    ]);
    await useLiveProject.getState().load("biz_test", "ghost");
    s = useLiveProject.getState();
    expect(s.status).toBe("error");
    expect(s.notFound).toBe(true);
    expect(s.project).toBeNull();
  });

  it("marks server errors as failures, not notFound", async () => {
    mockLive([
      {
        test: () => true,
        status: 500,
        body: { error: { code: "internal", message: "Unexpected server error." } },
      },
    ]);
    await useLiveProject.getState().load("biz_test", "proj_1");
    const s = useLiveProject.getState();
    expect(s.status).toBe("error");
    expect(s.notFound).toBe(false);
  });
});

describe("live tasks", () => {
  it("loads one project's tasks mapped to domain shape", async () => {
    mockLive([{ test: () => true, status: 200, body: taskPage(["t_b", "t_a"]) }]);
    await useLiveTasks.getState().load("biz_test", "proj_1");
    const s = useLiveTasks.getState();
    expect(s.status).toBe("ready");
    expect(s.tasks.map((t) => t.id)).toEqual(["t_b", "t_a"]);
    expect(s.tasks[0].dependsOn).toEqual([]);
    expect(s.tasks[0].costAccrued).toBe(0);
  });

  it("aggregates across projects for the tasks screen", async () => {
    mockLive([
      {
        test: (u) => u.includes("/projects/proj_1/tasks"),
        status: 200,
        body: taskPage(["t_1"], "proj_1"),
      },
      {
        test: (u) => u.includes("/projects/proj_2/tasks"),
        status: 200,
        body: taskPage(["t_2"], "proj_2"),
      },
    ]);
    await useLiveTasks.getState().loadAll("biz_test", ["proj_1", "proj_2"]);
    const s = useLiveTasks.getState();
    expect(s.status).toBe("ready");
    expect(s.tasks.map((t) => t.id)).toEqual(["t_1", "t_2"]);
    expect(s.tasks.map((t) => t.projectId)).toEqual(["proj_1", "proj_2"]);
  });

  it("401 is recorded (the session layer falls back to DEMO)", async () => {
    mockLive([
      {
        test: () => true,
        status: 401,
        body: { error: { code: "unauthorized", message: "Authentication required." } },
      },
    ]);
    await useLiveTasks.getState().load("biz_test", "proj_1");
    const s = useLiveTasks.getState();
    expect(s.status).toBe("error");
    expect(s.tasks).toEqual([]);
  });
});

describe("live agents cache", () => {
  it("loads once per business and skips refetch", async () => {
    const { calls } = mockLive([
      { test: (u) => /\/agents(\?.*)?$/.test(u), status: 200, body: agentPage(["ag_1"]) },
    ]);
    const store = useLiveAgents.getState();
    await store.ensure("biz_test");
    expect(useLiveAgents.getState().agents.map((a) => a.id)).toEqual(["ag_1"]);
    const count = calls.length;
    await useLiveAgents.getState().ensure("biz_test");
    expect(calls.length).toBe(count);
  });
});

describe("simulation isolation", () => {
  it("engine mutations cannot touch LIVE state", async () => {
    const { useProjects, useApprovals } = await import("@/lib/store/app");
    const { applyApprovalToExecution, failTaskInExecution, retryTaskInExecution } = await import(
      "@/lib/sim/engine"
    );
    mockLive([
      {
        test: (u) => /\/projects(\?.*)?$/.test(u),
        status: 200,
        body: projectPage(["proj_1"]),
      },
      {
        test: (u) => u.includes("/tasks"),
        status: 200,
        body: taskPage(["t_1"]),
      },
      {
        test: (u) => /\/agents(\?.*)?$/.test(u),
        status: 200,
        body: agentPage(["ag_1"]),
      },
    ]);
    await useLiveProjects.getState().load("biz_test");
    await useLiveTasks.getState().load("biz_test", "proj_1");
    await useLiveAgents.getState().ensure("biz_test");
    expect(useLiveProjects.getState().status).toBe("ready");
    expect(useLiveTasks.getState().status).toBe("ready");
    expect(useLiveAgents.getState().status).toBe("ready");
    const snapshot = JSON.stringify({
      projects: useLiveProjects.getState().projects,
      tasks: useLiveTasks.getState().tasks,
      agents: useLiveAgents.getState().agents,
    });

    const demoTask = useProjects.getState().tasks.find((t) => t.status === "working");
    if (demoTask) failTaskInExecution(demoTask.id, "probe");
    const failedDemo = useProjects.getState().tasks.find((t) => t.status === "failed");
    if (failedDemo) retryTaskInExecution(failedDemo.id);
    const pendingApproval = useApprovals.getState().approvals.find((a) => a.status === "pending");
    if (pendingApproval) applyApprovalToExecution(pendingApproval.id, "approved");

    expect(
      JSON.stringify({
        projects: useLiveProjects.getState().projects,
        tasks: useLiveTasks.getState().tasks,
        agents: useLiveAgents.getState().agents,
      }),
    ).toBe(snapshot);
  });
});
