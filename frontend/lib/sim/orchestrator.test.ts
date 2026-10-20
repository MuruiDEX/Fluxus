import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Approval, Project, Task } from "@/lib/domain/types";
import {
  applyDecision,
  createSession,
  failTask,
  retryTask,
  stepScope,
  type StepInput,
} from "@/lib/sim/core";
import {
  __resetEngineForTests,
  getEngineView,
  resumeLoop,
  startLoop,
  stopLoop,
  tickOnce,
} from "@/lib/sim/engine";

/* ------------------------------------------------------------------ */
/* Fixtures                                                            */
/* ------------------------------------------------------------------ */

const OPTS = { progressStep: 25, costPerTick: 0.1 };

function agent(id: string, businessId = "biz_a"): Agent {
  return {
    id,
    businessId,
    name: id,
    role: `Role ${id}`,
    responsibilities: [],
    tools: [],
    permissions: { read: [], write: [], actions: [], denied: [] },
    status: "available",
    costPerMonth: 10,
    performance: { completionRate: 99, avgMinutes: 5, approvalRate: 95, rating: 4.9 },
    hired: true,
    avatarHue: 200,
  };
}

function task(patch: Partial<Task> & { id: string }): Task {
  return {
    businessId: "biz_a",
    projectId: "proj_a",
    title: patch.id,
    ownerAgentId: "ag1",
    status: "not_started",
    progress: 0,
    dependsOn: [],
    costAccrued: 0,
    etaMinutes: 10,
    summary: "",
    attempts: 0,
    ...patch,
  };
}

function approval(patch: Partial<Approval> & { id: string }): Approval {
  return {
    businessId: "biz_a",
    projectId: "proj_a",
    title: patch.id,
    requestedBy: "Tester",
    requestedByAgentId: "ag1",
    ageLabel: "now",
    status: "pending",
    what: "",
    expectedResult: "",
    cost: "",
    affects: "",
    rationale: "",
    ...patch,
  };
}

function project(patch: Partial<Project> = {}): Project {
  return {
    id: "proj_a",
    businessId: "biz_a",
    title: "Fixture project",
    goal: "",
    status: "in_progress",
    progress: 0,
    budget: 1000,
    aiBudget: 20,
    aiSpent: 0,
    deadline: "soon",
    teamIds: [],
    milestones: [],
    ...patch,
  };
}

/** A+B parallel, C gated after A+B, D after C, plus a foreign-business task. */
function e2eInput(): StepInput {
  return {
    businessId: "biz_a",
    projectId: "proj_a",
    tasks: [
      task({ id: "tA", ownerAgentId: "ag1" }),
      task({ id: "tB", ownerAgentId: "ag2" }),
      task({ id: "tC", ownerAgentId: "ag3", dependsOn: ["tA", "tB"], approvalGateId: "ap1", gateAt: 50 }),
      task({ id: "tD", ownerAgentId: "ag1", dependsOn: ["tC"] }),
      { ...task({ id: "tX", ownerAgentId: "ag9" }), businessId: "biz_b", projectId: "proj_b", status: "working", progress: 10, costAccrued: 0.5 },
    ],
    agents: [agent("ag1"), agent("ag2"), agent("ag3"), agent("ag9", "biz_b")],
    project: project(),
    approvals: [approval({ id: "ap1", gatesTaskId: "tC" })],
  };
}

const byId = (tasks: Task[]) => new Map(tasks.map((t) => [t.id, t]));
const spent = (tasks: Task[]) =>
  Math.round(tasks.filter((t) => t.businessId === "biz_a").reduce((s, t) => s + t.costAccrued, 0) * 100) / 100;

/* ------------------------------------------------------------------ */
/* Parallel execution                                                  */
/* ------------------------------------------------------------------ */

describe("parallel execution", () => {
  it("advances independent tasks concurrently on a single tick", () => {
    const session = createSession();
    const r = stepScope(e2eInput(), session, OPTS);
    const m = byId(r.tasks);
    expect(m.get("tA")).toMatchObject({ status: "working", progress: 25, costAccrued: 0.1 });
    expect(m.get("tB")).toMatchObject({ status: "working", progress: 25, costAccrued: 0.1 });
    // Dependent stays blocked while prerequisites run.
    expect(m.get("tC")?.status).toBe("waiting_dependency");
    expect(m.get("tD")?.status).toBe("waiting_dependency");
  });

  it("runs the agent lifecycle Available -> Thinking -> Working", () => {
    const session = createSession();
    const input = e2eInput();
    const r1 = stepScope(input, session, OPTS);
    const agents1 = new Map(r1.tasks.length ? r1.agents.map((a) => [a.id, a]) : []);
    expect(agents1.get("ag1")?.status).toBe("thinking");
    expect(agents1.get("ag3")?.status).toBe("waiting_dependency");
    const r2 = stepScope({ ...input, tasks: r1.tasks, agents: r1.agents, project: r1.project }, session, OPTS);
    expect(new Map(r2.agents.map((a) => [a.id, a])).get("ag1")?.status).toBe("working");
  });
});

/* ------------------------------------------------------------------ */
/* Dependencies                                                        */
/* ------------------------------------------------------------------ */

describe("dependencies", () => {
  it("unblocks dependents only after ALL prerequisites complete", () => {
    const session = createSession();
    const input = e2eInput();
    let r = stepScope(input, session, OPTS);
    // Complete only A: C must still wait.
    r = stepScope(
      { ...input, tasks: r.tasks.map((t) => (t.id === "tA" ? { ...t, status: "completed" as const, progress: 100 } : t)) },
      session,
      OPTS,
    );
    expect(byId(r.tasks).get("tC")?.status).toBe("waiting_dependency");
    // Complete B too: C becomes runnable.
    r = stepScope(
      { ...input, tasks: r.tasks.map((t) => (t.id === "tB" ? { ...t, status: "completed" as const, progress: 100 } : t)) },
      session,
      OPTS,
    );
    expect(byId(r.tasks).get("tC")?.status).toBe("working");
    expect(r.events.some((e) => e.text.includes("Unblocked"))).toBe(true);
  });
});

/* ------------------------------------------------------------------ */
/* Approval gates                                                      */
/* ------------------------------------------------------------------ */

describe("approval gates", () => {
  function driveToGate() {
    const session = createSession();
    let input = e2eInput();
    let r = stepScope(input, session, OPTS);
    const complete = (ids: string[]) => ({
      ...input,
      tasks: r.tasks.map((t) => (ids.includes(t.id) ? { ...t, status: "completed" as const, progress: 100 } : t)),
    });
    input = complete(["tA", "tB"]);
    r = stepScope(input, session, OPTS); // C starts: 25
    input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
    r = stepScope(input, session, OPTS); // C: 50 -> gate
    return { session, input: { ...input, tasks: r.tasks, agents: r.agents, project: r.project }, r };
  }

  it("pauses the gated task at gateAt and freezes its cost", () => {
    const { r } = driveToGate();
    const c = byId(r.tasks).get("tC");
    expect(c).toMatchObject({ status: "waiting_approval", progress: 50 });
    const frozen = c!.costAccrued;
    const r2 = stepScope(
      { businessId: "biz_a", projectId: "proj_a", tasks: r.tasks, agents: r.agents, project: r.project, approvals: [approval({ id: "ap1", gatesTaskId: "tC" })] },
      createSession(),
      OPTS,
    );
    expect(byId(r2.tasks).get("tC")?.costAccrued).toBe(frozen);
    expect(byId(r2.tasks).get("tD")?.status).toBe("waiting_dependency");
  });

  it("resumes work after approval and completes the chain", () => {
    const { input, r } = driveToGate();
    const decided = applyDecision(r.tasks, input.approvals, "ap1", "approved");
    expect(byId(decided.tasks).get("tC")?.status).toBe("working");
    const resumed = stepScope(
      { ...input, tasks: decided.tasks, approvals: input.approvals.map((a) => ({ ...a, status: "approved" as const })) },
      createSession(),
      OPTS,
    );
    expect(byId(resumed.tasks).get("tC")?.progress).toBeGreaterThan(50);
  });

  it("lets pre-approved gates pass without pausing", () => {
    const session = createSession();
    let input: StepInput = {
      ...e2eInput(),
      approvals: [approval({ id: "ap1", gatesTaskId: "tC", status: "approved" })],
    };
    let r = stepScope(input, session, OPTS);
    input = { ...input, tasks: r.tasks.map((t) => (t.id === "tA" || t.id === "tB" ? { ...t, status: "completed" as const, progress: 100 } : t)) };
    r = stepScope(input, session, OPTS);
    input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
    r = stepScope(input, session, OPTS);
    expect(byId(r.tasks).get("tC")).toMatchObject({ status: "working", progress: 50 });
  });

  it("rejects with revision, then fails + escalates after 3 attempts", () => {
    const { input, r } = driveToGate();
    let tasks = r.tasks;
    // Reject 1: revision rollback to gateAt-30.
    let d = applyDecision(tasks, input.approvals, "ap1", "rejected");
    expect(byId(d.tasks).get("tC")).toMatchObject({ status: "working", progress: 20, attempts: 1 });
    // Reject 2.
    tasks = byId(d.tasks).get("tC")
      ? d.tasks.map((t) => (t.id === "tC" ? { ...t, status: "waiting_approval" as const, progress: 50 } : t))
      : d.tasks;
    d = applyDecision(tasks, input.approvals, "ap1", "rejected");
    expect(byId(d.tasks).get("tC")).toMatchObject({ status: "working", attempts: 2 });
    // Reject 3: failed.
    tasks = d.tasks.map((t) => (t.id === "tC" ? { ...t, status: "waiting_approval" as const, progress: 50 } : t));
    d = applyDecision(tasks, input.approvals, "ap1", "rejected");
    expect(byId(d.tasks).get("tC")).toMatchObject({ status: "failed", attempts: 3 });
    // Engine marks the owner escalated.
    const s2 = stepScope({ ...input, tasks: d.tasks }, createSession(), OPTS);
    expect(s2.agents.find((a) => a.id === "ag3")?.status).toBe("escalated");
    // Dependents stay blocked behind the failed task.
    expect(byId(s2.tasks).get("tD")?.status).toBe("waiting_dependency");
  });
});

/* ------------------------------------------------------------------ */
/* Failure + recovery                                                  */
/* ------------------------------------------------------------------ */

describe("failure handling", () => {
  it("fails explicitly, blocks dependents, and recovers via retry", () => {
    const session = createSession();
    const input = e2eInput();
    const r1 = stepScope(input, session, OPTS);
    const failed = failTask(r1.tasks, "tA", "model timeout");
    expect(byId(failed.tasks).get("tA")).toMatchObject({ status: "failed", attempts: 1 });
    const r2 = stepScope({ ...input, tasks: failed.tasks, agents: r1.agents }, session, OPTS);
    expect(byId(r2.tasks).get("tC")?.status).toBe("waiting_dependency");
    expect(r2.agents.find((a) => a.id === "ag1")?.status).toBe("failed");
    const retried = retryTask(r2.tasks, "tA");
    expect(byId(retried.tasks).get("tA")?.status).toBe("working");
    const r3 = stepScope({ ...input, tasks: retried.tasks, agents: r2.agents }, session, OPTS);
    expect(byId(r3.tasks).get("tA")?.progress).toBeGreaterThan(0);
  });
});

/* ------------------------------------------------------------------ */
/* Costs                                                               */
/* ------------------------------------------------------------------ */

describe("cost ledger", () => {
  it("never double-counts and reconciles task/project/analytics totals", () => {
    const session = createSession();
    let input = e2eInput();
    const reconcile = (r: { project: Project; tasks: Task[] }) => {
      // Invariant after every tick: project total == sum of scoped tasks.
      expect(r.project.aiSpent).toBe(spent(r.tasks));
    };
    for (let i = 0; i < 8; i++) {
      const r = stepScope(input, session, OPTS);
      input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
      reconcile(r);
    }
    // Mid-run approval lets the gated task finish; totals must still reconcile.
    const decided = applyDecision(input.tasks, input.approvals, "ap1", "approved");
    input = {
      ...input,
      tasks: decided.tasks,
      approvals: input.approvals.map((a) => ({ ...a, status: "approved" as const })),
    };
    for (let i = 0; i < 22; i++) {
      const r = stepScope(input, session, OPTS);
      input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
      reconcile(r);
    }
    // Terminal state is stable: extra ticks accrue nothing.
    const frozen = input.project.aiSpent;
    const extra = stepScope(input, session, OPTS);
    expect(extra.project.aiSpent).toBe(frozen);
    expect(extra.tasks.every((t) => t.status === "completed" || t.businessId !== "biz_a")).toBe(true);
  });
});

/* ------------------------------------------------------------------ */
/* Business isolation                                                  */
/* ------------------------------------------------------------------ */

describe("business isolation", () => {
  it("never mutates tasks or agents of another business", () => {
    const session = createSession();
    const input = e2eInput();
    const before = input.tasks.find((t) => t.id === "tX");
    const r = stepScope(input, session, OPTS);
    const after = r.tasks.find((t) => t.id === "tX");
    expect(after).toBe(before); // untouched reference
    expect(r.agents.find((a) => a.id === "ag9")?.status).toBe("available");
  });
});

/* ------------------------------------------------------------------ */
/* End-to-end walkthrough                                              */
/* ------------------------------------------------------------------ */

describe("end-to-end walkthrough", () => {
  it("launch -> parallel -> gate -> approve -> complete -> report -> brain", () => {
    const session = createSession();
    let input = e2eInput();
    const log: string[] = [];

    // Launch + parallel execution until the gate engages.
    let r = stepScope(input, session, OPTS);
    input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
    let guard = 0;
    while (byId(input.tasks).get("tC")?.status !== "waiting_approval" && guard++ < 20) {
      r = stepScope(input, session, OPTS);
      input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
    }
    expect(byId(input.tasks).get("tC")?.status).toBe("waiting_approval");
    expect(r.events.some((e) => e.kind === "approval")).toBe(true);
    log.push("gated");

    // Approve -> remaining work completes.
    const decided = applyDecision(input.tasks, input.approvals, "ap1", "approved");
    input = {
      ...input,
      tasks: decided.tasks,
      approvals: input.approvals.map((a) => ({ ...a, status: "approved" as const })),
    };
    guard = 0;
    while (input.project.status !== "completed" && guard++ < 30) {
      r = stepScope(input, session, OPTS);
      input = { ...input, tasks: r.tasks, agents: r.agents, project: r.project };
      expect(input.project.aiSpent).toBe(spent(input.tasks));
    }
    expect(input.project.status).toBe("completed");
    log.push("completed");

    // Final report + Business Brain update emitted exactly once.
    expect(r.brainEntries).toHaveLength(1);
    expect(r.brainEntries[0]).toMatchObject({ category: "results", businessId: "biz_a" });
    expect(r.events.some((e) => e.text.includes("Final report"))).toBe(true);
    const again = stepScope(input, session, OPTS);
    expect(again.brainEntries).toHaveLength(0);
    log.push("reported");
    expect(log).toEqual(["gated", "completed", "reported"]);
  });
});

/* ------------------------------------------------------------------ */
/* Loop lifecycle (single-loop guard)                                  */
/* ------------------------------------------------------------------ */

describe("engine loop", () => {
  afterEach(() => {
    vi.useRealTimers();
    stopLoop();
    __resetEngineForTests();
  });

  it("ignores duplicate starts and ticks exactly once per interval", () => {
    vi.useFakeTimers();
    const scope = { businessId: "biz_northstar", projectId: "proj_krakow_launch" };
    expect(startLoop(scope, 1000)).toBe(true);
    expect(startLoop(scope, 1000)).toBe(false); // duplicate start rejected
    expect(getEngineView().tickCount).toBe(0);
    vi.advanceTimersByTime(1000);
    expect(getEngineView().tickCount).toBe(1); // single loop, no overlap
    vi.advanceTimersByTime(2000);
    expect(getEngineView().tickCount).toBe(3);
    expect(stopLoop()).toBe(true);
  });

  it("pause/resume keeps one loop and tickOnce works without a loop", () => {
    vi.useFakeTimers();
    const scope = { businessId: "biz_northstar", projectId: "proj_krakow_launch" };
    expect(tickOnce(scope)).toBe(true);
    expect(getEngineView().tickCount).toBe(1);
    expect(tickOnce({ businessId: "nope", projectId: "nope" })).toBe(false);
    expect(resumeLoop()).toBe(false); // nothing paused
    startLoop(scope, 1000);
    expect(resumeLoop()).toBe(false); // already running
    stopLoop();
  });
});
