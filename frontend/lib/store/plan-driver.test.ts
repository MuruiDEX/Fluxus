/**
 * Plan-driver tests (Stage 8.4) — mocked fetch, no network.
 *
 * Covers: starting a planned project, dependency-aware progression from
 * authoritative state, approval gates (wait, never bypass), resume after
 * refresh, stop honesty, duplicate-start dedupe, ambiguous timeout followed
 * by reconciliation (not blind re-execution), terminal tasks without brief
 * completion, recovery probing, error/backoff handling, DEMO ban (+ DEMO
 * stores untouched), and the panel view mapping (static render).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setCsrfToken } from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import { useLiveExecution } from "@/lib/store/execution";
import { useLiveProject, useLiveTasks } from "@/lib/store/live";
import { useProjects } from "@/lib/store/app";
import {
  __resetPlanDriverForTests,
  classifyScope,
  driverKeyOf,
  usePlanDriver,
} from "@/lib/store/plan-driver";
import { describeDriver, PlanDriverPanel } from "@/components/system/PlanDriver";
import type { Approval, Project, Task } from "@/lib/domain/types";

const BIZ = "biz_test";
const PROJ = "proj_1";
const KEY = driverKeyOf(BIZ, PROJ);

// ---------------------------------------------------------------------------
// Fake server: authoritative state lives here, fetch routes serve it.
// ---------------------------------------------------------------------------

interface FakeTask {
  id: string;
  status: string;
  attempts: number;
  dependsOn: string[];
  approvalGateId: string | null;
}

interface FakeApproval {
  id: string;
  status: string;
  title: string;
}

function taskJson(t: FakeTask) {
  return {
    id: t.id,
    businessId: BIZ,
    projectId: PROJ,
    title: t.id,
    ownerAgentId: "ag_1",
    status: t.status,
    progress: t.status === "completed" ? 100 : 0,
    dependsOn: [...t.dependsOn],
    costAccrued: "0.00",
    etaMinutes: 0,
    summary: t.status === "completed" ? "Saved result" : "",
    attempts: t.attempts,
    approvalGateId: t.approvalGateId,
    gateAt: 100,
    costRate: null,
    contract: "v1",
  };
}

function projectJson(title: string) {
  return {
    id: PROJ,
    businessId: BIZ,
    title,
    goal: "",
    status: "in_progress",
    progress: 0,
    budget: "0",
    aiBudget: "0",
    aiSpent: "0",
    deadline: "",
    teamIds: [],
    milestones: [],
    contract: "v1",
  };
}

function approvalJson(a: FakeApproval) {
  return {
    id: a.id,
    businessId: BIZ,
    projectId: PROJ,
    title: a.title,
    requestedBy: "",
    requestedByAgentId: "ag_1",
    ageLabel: "",
    status: a.status,
    what: "",
    expectedResult: "",
    cost: "",
    affects: "",
    rationale: "",
    gatesTaskId: null,
    contract: "v1",
  };
}

function execJson(taskId: string) {
  return {
    outcome: "completed",
    taskId,
    status: "completed",
    attempts: 1,
    llmAttempts: 1,
    usage: {},
    error: null,
    contract: "v1",
  };
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface FakeServer {
  tasks: Map<string, FakeTask>;
  approvals: Map<string, FakeApproval>;
  briefStatus: string;
  /** Title served by GET project (proves in-place refresh). */
  projectTitle: string;
  /** executions POST behavior override per test. */
  onExecute?: (taskId: string) => Response | Promise<Response> | never;
  /** recover POST behavior override per test. */
  onRecover?: (taskId: string) => Response;
  approvalListCalls: number;
  calls: { url: string; method: string }[];
}

function serve(server: FakeServer) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      server.calls.push({ url, method });
      const signal = init?.signal;
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      const respond = (): Response | Promise<Response> => {
        const singleProject = url.match(/\/projects\/([^/?]+)(?:\?|$)/);
        if (singleProject && method === "GET" && !url.includes("/tasks")) {
          if (singleProject[1] !== PROJ) {
            return jsonResponse(404, { error: { code: "not_found", message: "no" } });
          }
          return jsonResponse(200, projectJson(server.projectTitle));
        }
        if (method === "GET" && /\/briefs\/[^/]+$/.test(url)) {
          return jsonResponse(200, {
            id: "brief_1",
            businessId: BIZ,
            goal: "Grow",
            status: server.briefStatus,
            questions: [],
            answers: [],
            summary: {},
            projectId: PROJ,
            contract: "v1",
          });
        }
        const singleTask = url.match(/\/tasks\/([^/?]+)(?:\?|$)/);
        if (singleTask && method === "GET" && !url.includes("/executions") && !url.includes("/recover")) {
          const task = server.tasks.get(singleTask[1]);
          if (!task) return jsonResponse(404, { error: { code: "not_found", message: "no" } });
          return jsonResponse(200, taskJson(task));
        }
        if (method === "GET" && url.includes(`/projects/${PROJ}/tasks`)) {
          return jsonResponse(200, {
            items: [...server.tasks.values()].map(taskJson),
            total: server.tasks.size,
            limit: 200,
            offset: 0,
            contract: "v1",
          });
        }
        if (method === "GET" && url.includes("/approvals")) {
          server.approvalListCalls += 1;
          return jsonResponse(200, {
            items: [...server.approvals.values()].map(approvalJson),
            total: server.approvals.size,
            limit: 200,
            offset: 0,
            contract: "v1",
          });
        }
        const exec = url.match(/\/tasks\/([^/]+)\/executions$/);
        if (exec && method === "POST") {
          if (server.onExecute) return server.onExecute(exec[1]);
          const task = server.tasks.get(exec[1]);
          if (task) task.status = "completed";
          return jsonResponse(200, execJson(exec[1]));
        }
        const recover = url.match(/\/tasks\/([^/]+)\/recover$/);
        if (recover && method === "POST") {
          if (server.onRecover) return server.onRecover(recover[1]);
          return jsonResponse(409, { outcome: "not_stale", taskId: recover[1], status: "working", contract: "v1" });
        }
        return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
      };
      return new Promise<Response>((resolve, reject) => {
        const onAbort = () => reject(new DOMException("aborted", "AbortError"));
        signal?.addEventListener("abort", onAbort, { once: true });
        Promise.resolve()
          .then(() => respond())
          .then(
            (res) => {
              signal?.removeEventListener("abort", onAbort);
              resolve(res);
            },
            (err) => {
              signal?.removeEventListener("abort", onAbort);
              reject(err);
            },
          );
      });
    }),
  );
}

function newServer(): FakeServer {
  return {
    tasks: new Map([
      ["t_1", { id: "t_1", status: "not_started", attempts: 0, dependsOn: [], approvalGateId: null }],
      ["t_2", { id: "t_2", status: "not_started", attempts: 0, dependsOn: ["t_1"], approvalGateId: null }],
    ]),
    approvals: new Map(),
    briefStatus: "planned",
    projectTitle: "Server Title",
    approvalListCalls: 0,
    calls: [],
  };
}

function signIn(businesses: string[] = [BIZ]) {
  setCsrfToken("csrf-test");
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

function record() {
  return usePlanDriver.getState().drivers[KEY];
}

function execPosts(server: FakeServer) {
  return server.calls.filter((c) => c.method === "POST" && c.url.includes("/executions"));
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  __resetPlanDriverForTests();
  useLiveExecution.setState({ runs: {} });
  useLiveTasks.setState({ status: "idle", key: null, tasks: [], error: null });
  useLiveProject.getState().reset();
});

// ---------------------------------------------------------------------------
// Pure classification.
// ---------------------------------------------------------------------------

function domainTask(over: Partial<Task> & { id: string }): Task {
  return {
    businessId: BIZ,
    projectId: PROJ,
    title: over.id,
    ownerAgentId: "ag_1",
    status: "not_started",
    progress: 0,
    dependsOn: [],
    costAccrued: 0,
    etaMinutes: 0,
    summary: "",
    ...over,
  } as Task;
}

function domainApproval(over: Partial<Approval> & { id: string }): Approval {
  return {
    businessId: BIZ,
    projectId: PROJ,
    title: over.id,
    requestedBy: "",
    requestedByAgentId: "ag_1",
    ageLabel: "",
    status: "pending",
    what: "",
    expectedResult: "",
    cost: "",
    affects: "",
    rationale: "",
    ...over,
  } as Approval;
}

describe("classifyScope", () => {
  it("picks the runnable task and counts completion", () => {
    const c = classifyScope(
      [domainTask({ id: "t_1", status: "completed" }), domainTask({ id: "t_2" })],
      [],
    );
    expect(c.total).toBe(2);
    expect(c.completed).toBe(1);
    expect(c.eligible?.id).toBe("t_2");
    expect(c.stalled).toBe(false);
  });

  it("holds tasks behind incomplete or unknown dependencies", () => {
    const waiting = classifyScope(
      [domainTask({ id: "t_1" }), domainTask({ id: "t_2", dependsOn: ["t_1"] })],
      [],
    );
    expect(waiting.eligible?.id).toBe("t_1");
    const stale = classifyScope([domainTask({ id: "t_2", dependsOn: ["ghost"] })], []);
    expect(stale.eligible).toBeNull();
    expect(stale.blocked).toHaveLength(1);
  });

  it("waits on pending gates and blocks on decided-against ones", () => {
    const gate = domainApproval({ id: "ap_1", status: "pending" });
    const waiting = classifyScope(
      [domainTask({ id: "t_1", approvalGateId: "ap_1" })],
      [gate],
    );
    expect(waiting.eligible).toBeNull();
    expect(waiting.waitingApproval?.id).toBe("ap_1");
    const rejected = classifyScope(
      [domainTask({ id: "t_1", approvalGateId: "ap_1" })],
      [domainApproval({ id: "ap_1", status: "rejected" })],
    );
    expect(rejected.blocked).toHaveLength(1);
    const missing = classifyScope([domainTask({ id: "t_1", approvalGateId: "ap_9" })], []);
    expect(missing.blocked).toHaveLength(1);
  });

  it("blocks attempts-exhausted tasks instead of re-running them", () => {
    const c = classifyScope([domainTask({ id: "t_1", status: "failed", attempts: 3 })], []);
    expect(c.eligible).toBeNull();
    expect(c.blocked).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Driver runs.
// ---------------------------------------------------------------------------

describe("plan driver", () => {
  it("starts a planned project, follows dependencies, ends awaiting finalization", async () => {
    const server = newServer();
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ, { briefId: "brief_1" });

    const posts = execPosts(server);
    expect(posts.map((p) => p.url)).toEqual([
      expect.stringContaining("/tasks/t_1/executions"),
      expect.stringContaining("/tasks/t_2/executions"),
    ]);
    const rec = record();
    expect(rec.phase).toBe("awaiting_finalization");
    expect(rec.completed).toBe(2);
    // Terminal without brief completion: brief touched by GET only.
    expect(server.calls.some((c) => c.method === "POST" && c.url.includes("/briefs"))).toBe(false);
    expect(server.briefStatus).toBe("planned");
  });

  it("resumes after refresh: completed tasks are never re-run", async () => {
    const server = newServer();
    server.tasks.get("t_1")!.status = "completed";
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ);

    expect(execPosts(server).map((p) => p.url)).toEqual([
      expect.stringContaining("/tasks/t_2/executions"),
    ]);
    expect(record().phase).toBe("awaiting_finalization");
  });

  it("snapshot refresh applies the server row without resetting the slice", async () => {
    // Regression: listSnapshot used to call proj.load(), which clears the
    // project and unmounts the mounted detail view (killing the driver via
    // its unmount cleanup). Background refresh must apply in place.
    const server = newServer();
    server.tasks.get("t_1")!.status = "completed";
    server.tasks.get("t_2")!.status = "completed";
    serve(server);
    signIn();
    const local: Project = {
      id: PROJ,
      businessId: BIZ,
      title: "Local Title",
      goal: "",
      status: "in_progress",
      progress: 0,
      budget: 0,
      aiBudget: 0,
      aiSpent: 0,
      deadline: "",
      teamIds: [],
      milestones: [],
    };
    useLiveProject.setState({
      status: "ready",
      key: `${BIZ}/${PROJ}`,
      project: local,
      notFound: false,
      error: null,
    });

    await usePlanDriver.getState().start(BIZ, PROJ);

    expect(record().phase).toBe("awaiting_finalization");
    const proj = useLiveProject.getState();
    expect(proj.status).toBe("ready");
    expect(proj.key).toBe(`${BIZ}/${PROJ}`);
    expect(proj.project?.title).toBe("Server Title");
  });

  it("waits for a pending approval without bypassing the gate", async () => {
    const server = newServer();
    server.tasks.get("t_2")!.approvalGateId = "ap_1";
    server.tasks.get("t_2")!.dependsOn = [];
    server.approvals.set("ap_1", { id: "ap_1", status: "pending", title: "Gate" });
    serve(server);
    signIn();

    const promise = usePlanDriver.getState().start(BIZ, PROJ);
    // Let the driver reach the gate (t_1 runs, then the gate parks it).
    await vi.waitFor(
      () => {
        expect(record()?.phase).toBe("waiting_approval");
      },
      { timeout: 10000 },
    );
    expect(execPosts(server).every((p) => !p.url.includes("/tasks/t_2/executions"))).toBe(true);
    // Human approves; the driver resumes on its own poll.
    server.approvals.get("ap_1")!.status = "approved";
    await promise;
    expect(execPosts(server).some((p) => p.url.includes("/tasks/t_2/executions"))).toBe(true);
    expect(record().phase).toBe("awaiting_finalization");
  }, 20000);

  it("stops honestly: no further requests, server work not claimed cancelled", async () => {
    const server = newServer();
    let release!: (v: Response) => void;
    server.onExecute = () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      });
    serve(server);
    signIn();

    const promise = usePlanDriver.getState().start(BIZ, PROJ);
    await vi.waitFor(() => {
      expect(execPosts(server)).toHaveLength(1);
    });
    usePlanDriver.getState().stop(BIZ, PROJ);
    const callsAfterStop = server.calls.length;
    release(jsonResponse(200, execJson("t_1")));
    await promise;
    // Settle any stragglers, then prove silence.
    await new Promise((r) => setTimeout(r, 50));
    expect(server.calls.length).toBe(callsAfterStop);
    const rec = record();
    expect(rec.phase).toBe("stopped");
    expect(rec.message).toMatch(/keeps going/);
    expect(server.calls.some((c) => c.url.includes("/recover"))).toBe(false);
  });

  it("shares one flight on duplicate start", async () => {
    const server = newServer();
    serve(server);
    signIn();

    const store = usePlanDriver.getState();
    await Promise.all([store.start(BIZ, PROJ), store.start(BIZ, PROJ)]);
    for (const t of ["t_1", "t_2"]) {
      expect(execPosts(server).filter((p) => p.url.includes(`/tasks/${t}/executions`))).toHaveLength(1);
    }
  });

  it("reconciles after an ambiguous timeout instead of re-executing blindly", async () => {
    const server = newServer();
    server.tasks.delete("t_2");
    let attempts = 0;
    // The claim landed despite the lost contact: the server shows completed.
    server.onExecute = (taskId) => {
      attempts += 1;
      if (attempts === 1) {
        server.tasks.get(taskId)!.status = "completed";
        throw new TypeError("socket hang up");
      }
      const task = server.tasks.get(taskId);
      if (task) task.status = "completed";
      return jsonResponse(200, execJson(taskId));
    };
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ);
    // Exactly one POST: the driver reconciled via GET and never re-fired.
    expect(execPosts(server)).toHaveLength(1);
    expect(record().phase).toBe("awaiting_finalization");
  });

  it("fails fast on a clean failure without auto-retry", async () => {
    const server = newServer();
    server.tasks.delete("t_2");
    server.onExecute = (taskId) => {
      const task = server.tasks.get(taskId);
      if (task) {
        task.status = "failed";
        task.attempts = 1;
      }
      return jsonResponse(200, { ...execJson(taskId), outcome: "failed", error: { code: "internal", message: "boom" } });
    };
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ);
    expect(execPosts(server)).toHaveLength(1);
    const rec = record();
    expect(rec.phase).toBe("failed");
    expect(rec.message).toMatch(/not retried automatically/);
  });

  it("probes a stuck working task with recovery, then continues", async () => {
    const server = newServer();
    server.tasks.delete("t_2");
    server.tasks.get("t_1")!.status = "working";
    let recovered = false;
    server.onRecover = (taskId) => {
      recovered = true;
      server.tasks.get(taskId)!.status = "not_started";
      return jsonResponse(200, { outcome: "recovered", taskId, status: "not_started", contract: "v1" });
    };
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ);
    expect(recovered).toBe(true);
    expect(server.calls.filter((c) => c.url.includes("/recover"))).toHaveLength(1);
    expect(execPosts(server)).toHaveLength(1);
    expect(record().phase).toBe("awaiting_finalization");
  }, 30000);

  it("retries transient list errors with backoff, then proceeds", async () => {
    const server = newServer();
    serve(server);
    signIn();
    let listCalls = 0;
    const realFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if ((init?.method ?? "GET") === "GET" && url.includes(`/projects/${PROJ}/tasks`)) {
          listCalls += 1;
          if (listCalls <= 2) {
            return jsonResponse(500, { error: { code: "internal", message: "blip" } });
          }
        }
        return (realFetch as unknown as (u: string, i?: RequestInit) => Promise<Response>)(url, init);
      }),
    );

    await usePlanDriver.getState().start(BIZ, PROJ);
    expect(listCalls).toBeGreaterThanOrEqual(3);
    expect(record().phase).toBe("awaiting_finalization");
  }, 20000);

  it("surfaces a missing project without sending anything", async () => {
    const server = newServer();
    serve(server);
    signIn();
    const realFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if ((init?.method ?? "GET") === "GET" && url.includes(`/projects/${PROJ}/tasks`)) {
          return jsonResponse(404, { error: { code: "not_found", message: "no" } });
        }
        return (realFetch as unknown as (u: string, i?: RequestInit) => Promise<Response>)(url, init);
      }),
    );

    await usePlanDriver.getState().start(BIZ, PROJ);
    const rec = record();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("not_found");
    expect(execPosts(server)).toHaveLength(0);
  });

  it("executes nothing for an already-completed brief", async () => {
    const server = newServer();
    server.briefStatus = "completed";
    serve(server);
    signIn();

    await usePlanDriver.getState().start(BIZ, PROJ, { briefId: "brief_1" });
    expect(execPosts(server)).toHaveLength(0);
    expect(record().phase).toBe("awaiting_finalization");
  });

  it("refuses DEMO contexts and leaves DEMO stores untouched", async () => {
    const server = newServer();
    serve(server);
    signOut();
    const demoTasksBefore = useProjects.getState().tasks.length;

    await usePlanDriver.getState().start(BIZ, PROJ);
    expect(server.calls).toHaveLength(0);
    const rec = record();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("unauthorized");
    expect(useProjects.getState().tasks).toHaveLength(demoTasksBefore);

    signIn(["other_biz"]);
    await usePlanDriver.getState().start(BIZ, PROJ);
    expect(server.calls).toHaveLength(0);
    expect(record().errorCode).toBe("forbidden");
  });
});

// ---------------------------------------------------------------------------
// Panel presentation.
// ---------------------------------------------------------------------------

describe("describeDriver + panel", () => {
  it("maps phases to honest Start/Stop controls", () => {
    expect(describeDriver(undefined).canStart).toBe(true);
    const runningView = describeDriver({
      phase: "running",
      briefStatus: "planned",
      total: 2,
      completed: 0,
      currentTaskId: "t_1",
      waitingApprovalId: null,
      message: "Running t_1…",
      errorCode: null,
      updatedAt: 0,
    });
    expect(runningView.canStop).toBe(true);
    expect(runningView.canStart).toBe(false);
    const waiting = describeDriver({
      phase: "waiting_approval",
      briefStatus: null,
      total: 2,
      completed: 1,
      currentTaskId: null,
      waitingApprovalId: "ap_1",
      message: "Waiting for approval: Gate.",
      errorCode: null,
      updatedAt: 0,
    });
    expect(waiting.canStop).toBe(true);
    expect(waiting.headline).toMatch(/Waiting for approval/);
    const stopped = describeDriver({
      phase: "stopped",
      briefStatus: null,
      total: 2,
      completed: 1,
      currentTaskId: null,
      waitingApprovalId: null,
      message: "Driver stopped.",
      errorCode: null,
      updatedAt: 0,
    });
    expect(stopped.canStart).toBe(true);
    expect(stopped.canStop).toBe(false);
  });

  it("renders Start idle and Stop running states statically", () => {
    const idle = renderToStaticMarkup(
      createElement(PlanDriverPanel, { businessId: BIZ, projectId: PROJ }),
    );
    expect(idle).toContain("Start");
    usePlanDriver.setState({
      drivers: {
        [KEY]: {
          phase: "running",
          briefStatus: "planned",
          total: 2,
          completed: 0,
          currentTaskId: "t_1",
          waitingApprovalId: null,
          message: "Running t_1…",
          errorCode: null,
          updatedAt: 0,
        },
      },
    });
    const running = renderToStaticMarkup(
      createElement(PlanDriverPanel, { businessId: BIZ, projectId: PROJ }),
    );
    expect(running).toContain("Stop");
  });
});
