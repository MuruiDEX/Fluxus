/**
 * LIVE management-write tests (Stage 7.2) — mocked fetch, no network.
 *
 * Covers: DEMO ban without any request, exact request shapes (allowlist
 * fields only, never executor-owned or derived fields), success cache sync
 * from server answers, single flight under concurrency, honest 401/403/404/
 * 422/network records, milestone + money/int helpers, and closed-state
 * static markup of the three entry buttons (no DOM test infra in this repo).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setCsrfToken } from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import {
  useLiveAgent,
  useLiveAgents,
  useLiveAgentsList,
  useLiveBrain,
  useLiveProject,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";
import { useLiveWrites } from "@/lib/store/writes";
import { AgentToggleButton, describeAgentToggle } from "@/components/system/AgentToggle";
import { BrainCreateButton } from "@/components/system/BrainCreate";
import { ProjectEditButton, milestoneAdd, milestoneMove, milestoneRemove, milestoneRename, milestoneToggle } from "@/components/system/ProjectEdit";
import { TaskEditButton } from "@/components/system/TaskEdit";
import { parseIntInput, parseMoneyInput } from "@/components/system/WriteModal";
import type { Agent, Project, Task } from "@/lib/domain/types";

function agentObj(hired = true): Agent {
  return {
    id: "ag_1",
    businessId: "biz_test",
    name: "Atlas",
    role: "Lead",
    responsibilities: [],
    tools: [],
    permissions: { read: [], write: [], actions: [], denied: [] },
    status: "available",
    currentTask: undefined,
    costPerMonth: 0,
    performance: { completionRate: 0, avgMinutes: 0, approvalRate: 0, rating: 0 },
    hired,
    avatarHue: 200,
  };
}

function agentJson(hired: boolean) {
  return {
    id: "ag_1",
    businessId: "biz_test",
    name: "Atlas",
    role: "Lead",
    responsibilities: [],
    tools: [],
    permissions: { read: [], write: [], actions: [], denied: [] },
    status: "available",
    currentTask: null,
    costPerMonth: 0,
    performance: {},
    hired,
    avatarHue: 200,
    contract: "v1",
  };
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Scenario {
  test: (url: string, init?: RequestInit) => boolean;
  status: number;
  body: unknown;
}

function mockFetch(scenarios: Scenario[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const hit = scenarios.find((s) => s.test(url, init));
      if (!hit) return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
      return jsonResponse(hit.status, hit.body);
    }),
  );
  return calls;
}

function signIn(businesses: string[] = ["biz_test"]) {
  useSession.setState({
    status: "authenticated",
    user: { id: "u_1", email: "op@example.com", displayName: "Op" },
    businesses,
    currentBusinessId: businesses[0] ?? null,
    error: null,
    sessionError: null,
  });
}

function signOut() {
  useSession.setState({
    status: "anonymous",
    user: null,
    businesses: [],
    currentBusinessId: null,
    error: null,
    sessionError: null,
  });
}

function projectObj(): Project {
  return {
    id: "proj_1",
    businessId: "biz_test",
    title: "P",
    goal: "G",
    status: "in_progress",
    progress: 10,
    budget: 100,
    aiBudget: 20,
    aiSpent: 0,
    deadline: "",
    teamIds: ["ag_1"],
    milestones: [{ label: "M1", done: false }],
  };
}

function projectJson(over: Record<string, unknown> = {}) {
  return {
    id: "proj_1",
    businessId: "biz_test",
    title: "New title",
    goal: "New goal",
    status: "review",
    progress: 10,
    budget: "100.50",
    aiBudget: "20.00",
    aiSpent: "0.00",
    deadline: "30 May 2026",
    teamIds: ["ag_1"],
    milestones: [{ label: "M1", done: true }],
    contract: "v1",
    ...over,
  };
}

function taskObj(): Task {
  return {
    id: "t_1",
    businessId: "biz_test",
    projectId: "proj_1",
    title: "T",
    ownerAgentId: "ag_1",
    status: "not_started",
    progress: 0,
    dependsOn: [],
    costAccrued: 0,
    etaMinutes: 30,
    summary: "S",
  };
}

function taskJson(over: Record<string, unknown> = {}) {
  return {
    id: "t_1",
    businessId: "biz_test",
    projectId: "proj_1",
    title: "Retitled",
    ownerAgentId: "ag_2",
    status: "not_started",
    progress: 0,
    dependsOn: [],
    costAccrued: "0.00",
    etaMinutes: 45,
    summary: "New summary",
    attempts: 0,
    approvalGateId: null,
    gateAt: 80,
    costRate: null,
    contract: "v1",
    ...over,
  };
}

function brainEntryJson(id = "br_9") {
  return {
    id,
    businessId: "biz_test",
    category: "goals",
    title: "Ship it",
    body: "Details",
    updatedLabel: "",
    contract: "v1",
  };
}

function brainPage(ids: string[]) {
  return {
    items: ids.map((id) => brainEntryJson(id)),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function seedProjectCache() {
  useLiveProject.setState({
    status: "ready",
    key: "biz_test/proj_1",
    project: projectObj(),
    notFound: false,
    error: null,
  });
  useLiveProjects.setState({
    status: "ready",
    businessId: "biz_test",
    projects: [projectObj()],
    total: 1,
    error: null,
  });
}

function seedTasksCache() {
  useLiveTasks.setState({ status: "ready", key: "biz_test/*", tasks: [taskObj()], error: null });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveWrites.setState({ projects: {}, tasks: {}, brains: {}, hires: {}, agents: {} });
  useLiveProject.setState({ status: "idle", key: null, project: null, notFound: false, error: null });
  useLiveProjects.setState({ status: "idle", businessId: null, projects: [], total: 0, error: null });
  useLiveTasks.setState({ status: "idle", key: null, tasks: [], error: null });
  useLiveAgentsList.setState({
    status: "idle",
    businessId: null,
    agents: [],
    total: 0,
    error: null,
  });
  useLiveAgent.setState({
    status: "idle",
    key: null,
    agent: null,
    notFound: false,
    error: null,
  });
  useLiveAgents.setState({ status: "idle", businessId: null, agents: [], error: null });
  useLiveProject.setState({ status: "idle", key: null, project: null, notFound: false, error: null });
  useLiveProjects.setState({ status: "idle", businessId: null, projects: [], total: 0, error: null });
  useLiveTasks.setState({ status: "idle", key: null, tasks: [], error: null });
  useLiveBrain.setState({
    status: "idle",
    businessId: null,
    category: null,
    entries: [],
    total: 0,
    error: null,
  });
});

describe("patchProject", () => {
  it("refuses DEMO contexts without any request", async () => {
    const calls = mockFetch([
      { test: () => true, status: 200, body: projectJson() },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { title: "X" });
    expect(calls).toHaveLength(0);
    const rec = useLiveWrites.getState().projects["biz_test/proj_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("unauthorized");
  });

  it("refuses foreign business without any request", async () => {
    signIn(["biz_other"]);
    const calls = mockFetch([
      { test: () => true, status: 200, body: projectJson() },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { title: "X" });
    expect(calls).toHaveLength(0);
    expect(useLiveWrites.getState().projects["biz_test/proj_1"].errorCode).toBe("forbidden");
  });

  it("sends only allowlist fields and syncs both caches from the answer", async () => {
    signIn();
    seedProjectCache();
    const calls = mockFetch([
      { test: (u, init) => u.includes("/projects/proj_1") && init?.method === "PATCH", status: 200, body: projectJson() },
    ]);
    await useLiveWrites
      .getState()
      .patchProject("biz_test", "proj_1", { title: "New title", status: "review" });
    expect(calls).toHaveLength(1);
    const sent = JSON.parse(calls[0].init?.body as string) as Record<string, unknown>;
    expect(sent).toEqual({ title: "New title", status: "review" });
    for (const forbidden of ["aiSpent", "progress", "id", "businessId"]) {
      expect(sent, `must not send ${forbidden}`).not.toHaveProperty(forbidden);
    }
    const rec = useLiveWrites.getState().projects["biz_test/proj_1"];
    expect(rec.phase).toBe("saved");
    expect(useLiveProject.getState().project?.title).toBe("New title");
    expect(useLiveProject.getState().project?.status).toBe("review");
    expect(useLiveProjects.getState().projects[0]?.title).toBe("New title");
  });

  it("records 422 honestly and leaves caches untouched", async () => {
    signIn();
    seedProjectCache();
    mockFetch([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "invalid_enum", message: "Unknown project status.", trace_id: "t" } },
      },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { status: "nope" });
    const rec = useLiveWrites.getState().projects["biz_test/proj_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("invalid_enum");
    expect(useLiveProject.getState().project?.title).toBe("P");
  });

  it("records 404 and 401 as errors (no hanging save)", async () => {
    signIn();
    mockFetch([
      { test: () => true, status: 404, body: { error: { code: "not_found", message: "Gone." } } },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "ghost", { title: "X" });
    expect(useLiveWrites.getState().projects["biz_test/ghost"].errorCode).toBe("not_found");

    mockFetch([
      { test: () => true, status: 401, body: { error: { code: "unauthorized", message: "Auth." } } },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { title: "X" });
    const rec = useLiveWrites.getState().projects["biz_test/proj_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("unauthorized");
  });

  it("reports network loss as unknown-outcome, never success", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { title: "X" });
    const rec = useLiveWrites.getState().projects["biz_test/proj_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("network_unreachable");
  });

  it("sends exactly one PATCH under concurrency", async () => {
    signIn();
    seedProjectCache();
    const calls = mockFetch([
      { test: () => true, status: 200, body: projectJson() },
    ]);
    const store = useLiveWrites.getState();
    await Promise.all([
      store.patchProject("biz_test", "proj_1", { title: "A" }),
      store.patchProject("biz_test", "proj_1", { title: "A" }),
    ]);
    expect(calls).toHaveLength(1);
  });

  it("clearProject drops the record", async () => {
    signIn();
    mockFetch([
      { test: () => true, status: 200, body: projectJson() },
    ]);
    await useLiveWrites.getState().patchProject("biz_test", "proj_1", { title: "A" });
    expect(useLiveWrites.getState().projects["biz_test/proj_1"]).toBeDefined();
    useLiveWrites.getState().clearProject("biz_test", "proj_1");
    expect(useLiveWrites.getState().projects["biz_test/proj_1"]).toBeUndefined();
  });
});

describe("patchTask", () => {
  it("sends only TaskPatch fields and applies the answered row", async () => {
    signIn();
    seedTasksCache();
    const calls = mockFetch([
      { test: (u, init) => u.includes("/tasks/t_1") && init?.method === "PATCH", status: 200, body: taskJson() },
    ]);
    await useLiveWrites
      .getState()
      .patchTask("biz_test", "proj_1", "t_1", { title: "Retitled", etaMinutes: 45 });
    expect(calls).toHaveLength(1);
    const sent = JSON.parse(calls[0].init?.body as string) as Record<string, unknown>;
    expect(Object.keys(sent).sort()).toEqual(["etaMinutes", "title"]);
    for (const forbidden of [
      "status",
      "progress",
      "costAccrued",
      "attempts",
      "approvalGateId",
      "costRate",
      "projectId",
    ]) {
      expect(sent, `must not send ${forbidden}`).not.toHaveProperty(forbidden);
    }
    const rec = useLiveWrites.getState().tasks["biz_test/proj_1/t_1"];
    expect(rec.phase).toBe("saved");
    const row = useLiveTasks.getState().tasks.find((t) => t.id === "t_1");
    expect(row?.title).toBe("Retitled");
    expect(row?.ownerAgentId).toBe("ag_2");
    // Executor-owned state is untouched by a metadata patch.
    expect(row?.status).toBe("not_started");
    expect(row?.costAccrued).toBe(0);
  });

  it("records dangling owner honestly", async () => {
    signIn();
    seedTasksCache();
    mockFetch([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "dangling_reference", message: "Missing agent." } },
      },
    ]);
    await useLiveWrites
      .getState()
      .patchTask("biz_test", "proj_1", "t_1", { ownerAgentId: "ag_ghost" });
    const rec = useLiveWrites.getState().tasks["biz_test/proj_1/t_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("dangling_reference");
    expect(useLiveTasks.getState().tasks.find((t) => t.id === "t_1")?.ownerAgentId).toBe("ag_1");
  });

  it("refuses DEMO contexts without any request", async () => {
    const calls = mockFetch([{ test: () => true, status: 200, body: taskJson() }]);
    await useLiveWrites.getState().patchTask("biz_test", "proj_1", "t_1", { title: "X" });
    expect(calls).toHaveLength(0);
    expect(useLiveWrites.getState().tasks["biz_test/proj_1/t_1"].errorCode).toBe("unauthorized");
  });
});

describe("createBrain", () => {
  it("creates via 201, reloads the list, quotes the server id", async () => {
    signIn();
    const calls = mockFetch([
      {
        test: (u, init) => u.endsWith("/brain") && init?.method === "POST",
        status: 201,
        body: brainEntryJson("br_9"),
      },
      {
        test: (u, init) => u.includes("/brain") && (init?.method ?? "GET") === "GET",
        status: 200,
        body: brainPage(["br_9"]),
      },
    ]);
    await useLiveWrites
      .getState()
      .createBrain("biz_test", { category: "goals", title: "Ship it", body: "Details" });
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("http://127.0.0.1:8000/api/v1/businesses/biz_test/brain");
    expect(JSON.parse(post?.init?.body as string)).toEqual({
      category: "goals",
      title: "Ship it",
      body: "Details",
    });
    const rec = useLiveWrites.getState().brains["biz_test"];
    expect(rec.phase).toBe("saved");
    expect(rec.message).toContain("br_9");
    // Entries come from the server reload, never fabricated.
    expect(useLiveBrain.getState().entries.map((e) => e.id)).toEqual(["br_9"]);
    expect(useLiveBrain.getState().total).toBe(1);
  });

  it("records 422 without touching the list", async () => {
    signIn();
    const calls = mockFetch([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "invalid_enum", message: "Bad category." } },
      },
    ]);
    await useLiveWrites.getState().createBrain("biz_test", { category: "nope", title: "T" });
    expect(calls).toHaveLength(1);
    const rec = useLiveWrites.getState().brains["biz_test"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("invalid_enum");
    expect(useLiveBrain.getState().entries).toEqual([]);
  });

  it("refuses DEMO contexts without any request", async () => {
    const calls = mockFetch([{ test: () => true, status: 201, body: brainEntryJson() }]);
    await useLiveWrites.getState().createBrain("biz_test", { category: "goals", title: "T" });
    expect(calls).toHaveLength(0);
    expect(useLiveWrites.getState().brains["biz_test"].errorCode).toBe("unauthorized");
  });
});

describe("form helpers", () => {
  it("parseMoneyInput accepts blank/valid and rejects the rest", () => {
    expect(parseMoneyInput("")).toEqual({ ok: true, value: undefined });
    expect(parseMoneyInput("  ")).toEqual({ ok: true, value: undefined });
    expect(parseMoneyInput("100.50")).toEqual({ ok: true, value: "100.50" });
    expect(parseMoneyInput("20")).toEqual({ ok: true, value: "20" });
    expect(parseMoneyInput("-5").ok).toBe(false);
    expect(parseMoneyInput("10.005").ok).toBe(false);
    expect(parseMoneyInput("abc").ok).toBe(false);
  });

  it("parseIntInput enforces whole numbers in range", () => {
    expect(parseIntInput("", 0, 100, "Gate at")).toEqual({ ok: true, value: undefined });
    expect(parseIntInput("80", 0, 100, "Gate at")).toEqual({ ok: true, value: 80 });
    expect(parseIntInput("101", 0, 100, "Gate at").ok).toBe(false);
    expect(parseIntInput("4.5", 0, 100, "Gate at").ok).toBe(false);
    expect(parseIntInput("-1", 0, 100, "Gate at").ok).toBe(false);
  });

  it("milestone helpers add/remove/move/toggle/rename", () => {
    const base = [
      { label: "A", done: false },
      { label: "B", done: true },
    ];
    expect(milestoneAdd(base, "  ")).toEqual(base);
    expect(milestoneAdd(base, " C ")).toEqual([...base, { label: "C", done: false }]);
    expect(milestoneRemove(base, 0)).toEqual([{ label: "B", done: true }]);
    expect(milestoneMove(base, 0, 1)).toEqual([
      { label: "B", done: true },
      { label: "A", done: false },
    ]);
    expect(milestoneMove(base, 0, -1)).toEqual(base);
    expect(milestoneMove(base, 1, 1)).toEqual(base);
    expect(milestoneToggle(base, 0)).toEqual([
      { label: "A", done: true },
      { label: "B", done: true },
    ]);
    expect(milestoneRename(base, 1, "B2")).toEqual([
      { label: "A", done: false },
      { label: "B2", done: true },
    ]);
  });
});

describe("agent lifecycle", () => {
  function seedAgents(hired = true) {
    useLiveAgentsList.setState({
      status: "ready",
      businessId: "biz_test",
      agents: [agentObj(hired)],
      total: 1,
      error: null,
    });
    useLiveAgent.setState({
      status: "ready",
      key: "biz_test/ag_1",
      agent: agentObj(hired),
      notFound: false,
      error: null,
    });
    useLiveAgents.setState({
      status: "ready",
      businessId: "biz_test",
      agents: [agentObj(hired)],
      error: null,
    });
  }

  it("refuses DEMO contexts without any request", async () => {
    const calls = mockFetch([{ test: () => true, status: 200, body: agentJson(false) }]);
    await useLiveWrites.getState().disableAgent("biz_test", "ag_1");
    expect(calls).toHaveLength(0);
    const rec = useLiveWrites.getState().agents["biz_test/ag_1"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("unauthorized");
  });

  it("disable flips both caches from the server answer", async () => {
    signIn();
    seedAgents(true);
    const calls = mockFetch([
      { test: (u, init) => u.endsWith("/disable") && init?.method === "POST", status: 200, body: agentJson(false) },
    ]);
    await useLiveWrites.getState().disableAgent("biz_test", "ag_1");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/agents/ag_1/disable",
    );
    const rec = useLiveWrites.getState().agents["biz_test/ag_1"];
    expect(rec.phase).toBe("saved");
    expect(useLiveAgentsList.getState().agents[0]?.hired).toBe(false);
    expect(useLiveAgent.getState().agent?.hired).toBe(false);
    // Display cache is invalidated so pickers refetch instead of serving stale rows.
    expect(useLiveAgents.getState().status).toBe("idle");
  });

  it("activate flips both caches from the server answer", async () => {
    signIn();
    seedAgents(false);
    mockFetch([
      { test: (u) => u.endsWith("/activate"), status: 200, body: agentJson(true) },
    ]);
    await useLiveWrites.getState().activateAgent("biz_test", "ag_1");
    const rec = useLiveWrites.getState().agents["biz_test/ag_1"];
    expect(rec.phase).toBe("saved");
    expect(useLiveAgentsList.getState().agents[0]?.hired).toBe(true);
    expect(useLiveAgent.getState().agent?.hired).toBe(true);
    expect(useLiveAgents.getState().status).toBe("idle");
  });

  it("records 404/401 honestly without touching caches", async () => {
    signIn();
    seedAgents(true);
    mockFetch([
      { test: () => true, status: 404, body: { error: { code: "not_found", message: "Nope." } } },
    ]);
    await useLiveWrites.getState().disableAgent("biz_test", "ghost");
    const rec = useLiveWrites.getState().agents["biz_test/ghost"];
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("not_found");
    expect(useLiveAgentsList.getState().agents[0]?.hired).toBe(true);
  });

  it("sends exactly one POST under concurrency", async () => {
    signIn();
    seedAgents(true);
    const calls = mockFetch([
      { test: (u) => u.endsWith("/disable"), status: 200, body: agentJson(false) },
    ]);
    const store = useLiveWrites.getState();
    await Promise.all([
      store.disableAgent("biz_test", "ag_1"),
      store.disableAgent("biz_test", "ag_1"),
    ]);
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(1);
  });

  it("clearAgent drops the record", async () => {
    signIn();
    seedAgents(true);
    mockFetch([{ test: () => true, status: 200, body: agentJson(false) }]);
    await useLiveWrites.getState().disableAgent("biz_test", "ag_1");
    expect(useLiveWrites.getState().agents["biz_test/ag_1"]).toBeDefined();
    useLiveWrites.getState().clearAgent("biz_test", "ag_1");
    expect(useLiveWrites.getState().agents["biz_test/ag_1"]).toBeUndefined();
  });
});

describe("entry buttons (closed static markup)", () => {
  it("render Edit / Add note triggers without opening forms", () => {
    const projectHtml = renderToStaticMarkup(
      createElement(ProjectEditButton, { businessId: "biz_test", project: projectObj() }),
    );
    expect(projectHtml).toContain("Edit");
    expect(projectHtml).not.toContain("Save changes");
    const taskHtml = renderToStaticMarkup(
      createElement(TaskEditButton, { businessId: "biz_test", task: taskObj() }),
    );
    expect(taskHtml).toContain("Edit");
    expect(taskHtml).not.toContain("Save changes");
    const brainHtml = renderToStaticMarkup(
      createElement(BrainCreateButton, { businessId: "biz_test" }),
    );
    expect(brainHtml).toContain("Add note");
    expect(brainHtml).not.toContain("Save to Business Brain");
  });

  it("maps toggle branches without implying success", () => {
    expect(describeAgentToggle(undefined, true).kind).toBe("active");
    expect(describeAgentToggle(undefined, false).kind).toBe("disabled");
    expect(
      describeAgentToggle({ phase: "saving", message: null, errorCode: null, updatedAt: 0 }, true)
        .kind,
    ).toBe("saving");
    const err = describeAgentToggle(
      { phase: "error", message: "Nope.", errorCode: "forbidden", updatedAt: 0 },
      true,
    );
    expect(err.kind).toBe("error");
    expect(err.showRetry).toBe(true);
  });

  it("renders Activate for disabled and Disable for active agents", () => {
    const off = renderToStaticMarkup(
      createElement(AgentToggleButton, { businessId: "biz_test", agentId: "ag_1", hired: false }),
    );
    expect(off).toContain("Activate");
    expect(off).toContain("Disabled — takes no new work.");
    const on = renderToStaticMarkup(
      createElement(AgentToggleButton, { businessId: "biz_test", agentId: "ag_1", hired: true }),
    );
    expect(on).toContain("Disable");
    expect(on).not.toContain("Activate");
  });
});
