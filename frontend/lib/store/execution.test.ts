/**
 * LIVE execution tests (Stage 7.4) — mocked fetch, no network.
 *
 * Covers: client call shape, outcome-payload recovery from ApiError,
 * success + store refresh, single POST under concurrency, DEMO ban,
 * read-before-write (completed/working never re-POST), already_running /
 * already_completed / approval_required, uncertain outcome on lost contact,
 * stop-waiting honesty (never "server cancelled"), reconcile paths,
 * 401/403/404/422/429/5xx, stale-generation drop, and the run-button UI
 * states (static render — no DOM test infra in this repo).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  ApiError,
  apiRunExecution,
  executionErrorOf,
  executionOutcomeOf,
  setCsrfToken,
} from "@/lib/api";
import { TaskRunButton } from "@/components/system/TaskRun";
import { useSession } from "@/lib/store/auth";
import { useLiveExecution } from "@/lib/store/execution";
import { useLiveTasks } from "@/lib/store/live";

function taskJson(status: string, extra?: Record<string, unknown>) {
  return {
    id: "t_1",
    businessId: "biz_test",
    projectId: "proj_1",
    title: "T",
    ownerAgentId: "ag_1",
    status,
    progress: status === "completed" ? 100 : 0,
    costAccrued: "0.00",
    etaMinutes: 0,
    summary: status === "completed" ? "Saved result" : "",
    attempts: status === "failed" ? 1 : 0,
    approvalGateId: null,
    gateAt: null,
    costRate: null,
    contract: "v1",
    ...extra,
  };
}

function execJson(
  outcome: string,
  extra?: { status?: string | null; code?: string; message?: string },
) {
  return {
    outcome,
    taskId: "t_1",
    status: extra?.status ?? null,
    attempts: 1,
    llmAttempts: 1,
    usage: {},
    error:
      extra?.code !== undefined
        ? { code: extra.code, message: extra.message ?? "m" }
        : null,
    contract: "v1",
  };
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** GET-task responder serving statuses in order (pre-read first, then reconciliations). */
function taskSequence(...statuses: string[]) {
  let i = 0;
  return () => jsonResponse(200, taskJson(statuses[Math.min(i++, statuses.length - 1)]));
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

function runOf(taskId = "t_1") {
  return useLiveExecution.getState().runs[`biz_test/proj_1/${taskId}`];
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveExecution.setState({ runs: {} });
  useLiveTasks.setState({ status: "idle", key: null, tasks: [], error: null });
});

describe("apiRunExecution client", () => {
  it("POSTs the executions path with CSRF, contract header, and optional timeout", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, execJson("completed"));
      }),
    );
    setCsrfToken("csrf-1");
    await apiRunExecution("biz_test", "proj_1", "t_1");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/projects/proj_1/tasks/t_1/executions",
    );
    expect(calls[0].init?.method).toBe("POST");
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect((calls[0].init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");

    await apiRunExecution("biz_test", "proj_1", "t_1", { timeoutS: 60 });
    expect(calls[1].init?.body).toBe(JSON.stringify({ timeout_s: 60 }));
  });

  it("preserves the outcome payload on ApiError and parses it safely", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(409, { ...execJson("already_running", { code: "conflict", message: "busy" }) }),
      ),
    );
    const err = await apiRunExecution("biz_test", "proj_1", "t_1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect(executionOutcomeOf((err as ApiError).payload)).toBe("already_running");
    expect(executionErrorOf((err as ApiError).payload)).toEqual({ code: "conflict", message: "busy" });
    expect(executionOutcomeOf(null)).toBeNull();
    expect(executionOutcomeOf({ outcome: "made_up" })).toBeNull();
    expect(executionErrorOf({ error: { code: 1 } })).toBeNull();
  });
});

describe("execution runner", () => {
  function mockFlow(routes: { test: (url: string) => boolean; respond: () => Response | Promise<Response> }[]) {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if ((init?.method ?? "GET") === "POST" && url.includes("/executions")) posts.push(url);
        const route = routes.find((r) => r.test(url));
        if (!route) return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
        // Mirror real fetch: an aborted signal rejects, even mid-flight.
        const signal = init?.signal;
        if (signal?.aborted) throw new DOMException("aborted", "AbortError");
        return new Promise<Response>((resolve, reject) => {
          const onAbort = () => reject(new DOMException("aborted", "AbortError"));
          signal?.addEventListener("abort", onAbort, { once: true });
          Promise.resolve()
            .then(() => route.respond())
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
    return { posts };
  }

  it("runs once end-to-end and refreshes the cached task", async () => {
    const { posts } = mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () => jsonResponse(200, execJson("completed", { status: "completed" })),
      },
      {
        test: (u) => u.includes("/tasks/t_1") && !u.includes("/executions"),
        respond: (() => {
          let first = true;
          return () => {
            const body = first ? taskJson("not_started") : taskJson("completed");
            first = false;
            return jsonResponse(200, body);
          };
        })(),
      },
    ]);
    signIn();
    useLiveTasks.setState({
      status: "ready",
      key: "biz_test/proj_1",
      tasks: [],
      error: null,
    });
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(1);
    const run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.outcome).toBe("completed");
    const cached = useLiveTasks.getState().tasks.find((t) => t.id === "t_1");
    expect(cached?.status).toBe("completed");
    expect(cached?.summary).toBe("Saved result");
  });

  it("sends exactly one POST on concurrent run calls", async () => {
    const gate = deferred<Response>();
    const { posts } = mockFlow([
      { test: (u) => u.includes("/executions"), respond: () => gate.promise },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    signIn();
    const store = useLiveExecution.getState();
    const first = store.run("biz_test", "proj_1", "t_1");
    const second = store.run("biz_test", "proj_1", "t_1");
    gate.resolve(jsonResponse(200, execJson("completed")));
    await Promise.all([first, second]);
    // reconcile GET after completion adds no POST.
    expect(posts).toHaveLength(1);
  });

  it("refuses to run from DEMO or foreign business without any request", async () => {
    const { posts } = mockFlow([{ test: () => true, respond: () => jsonResponse(200, {}) }]);
    signOut();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(0);
    expect(runOf()?.message).toMatch(/sign in/i);

    signIn(["biz_other"]);
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(0);
    expect(runOf()?.message).toMatch(/no access/i);
  });

  it("never re-POSTs completed or running tasks", async () => {
    const { posts } = mockFlow([
      { test: () => true, respond: () => jsonResponse(200, taskJson("completed")) },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(0);
    expect(runOf()?.outcome).toBe("completed");

    mockFlow([{ test: () => true, respond: () => jsonResponse(200, taskJson("working")) }]);
    useLiveExecution.getState().clear("biz_test", "proj_1", "t_1");
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(0);
    expect(runOf()?.outcome).toBe("running");
  });

  it("treats already_running as a re-read signal, not a failure", async () => {
    const { posts } = mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () =>
          jsonResponse(409, execJson("already_running", { code: "conflict", message: "claimed" })),
      },
      { test: () => true, respond: taskSequence("not_started", "working") },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(1);
    const run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.outcome).toBe("running");
  });

  it("treats already_completed as a saved-result signal", async () => {
    mockFlow([
      { test: (u) => u.includes("/executions"), respond: () => jsonResponse(200, execJson("already_completed")) },
      { test: () => true, respond: () => jsonResponse(200, taskJson("completed")) },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(runOf()?.outcome).toBe("completed");
  });

  it("shows approval_required without bypassing the gate", async () => {
    const { posts } = mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () =>
          jsonResponse(
            409,
            execJson("refused", { code: "approval_required", message: "Gate 'X' is pending; human approval required." }),
          ),
      },
      { test: () => true, respond: taskSequence("not_started", "waiting_approval") },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(posts).toHaveLength(1);
    const run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.errorCode).toBe("approval_required");
    expect(run?.message).toMatch(/human must approve/i);
  });

  it("marks lost contact as uncertain and reconciles afterwards", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () => {
          throw new TypeError("fetch failed");
        },
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    let run = runOf();
    expect(run?.phase).toBe("uncertain");
    expect(run?.message).toMatch(/re-check/i);

    mockFlow([{ test: () => true, respond: () => jsonResponse(200, taskJson("completed")) }]);
    await useLiveExecution.getState().reconcile("biz_test", "proj_1", "t_1");
    run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.outcome).toBe("completed");
  });

  it("stop-waiting never claims a server cancellation", async () => {
    const gate = deferred<Response>();
    const { posts } = mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () =>
          new Promise<Response>((resolve, reject) => {
            void gate.promise.then(resolve, reject);
          }),
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    signIn();
    const pending = useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    // Wait until the POST is actually in flight before abandoning the wait.
    await vi.waitFor(() => {
      expect(posts.length).toBe(1);
    });
    useLiveExecution.getState().stopWaiting("biz_test", "proj_1", "t_1");
    await pending;
    const run = runOf();
    expect(run?.phase).toBe("uncertain");
    expect(run?.message ?? "").not.toMatch(/cancel/i);
    expect(run?.message).toMatch(/re-check/i);
    gate.resolve(jsonResponse(200, execJson("completed")));
  });

  it("reconcile maps working and gone states", async () => {
    mockFlow([{ test: () => true, respond: () => jsonResponse(200, taskJson("working")) }]);
    signIn();
    await useLiveExecution.getState().reconcile("biz_test", "proj_1", "t_1");
    expect(runOf()?.outcome).toBe("running");

    mockFlow([
      {
        test: () => true,
        respond: () => jsonResponse(404, { error: { code: "not_found", message: "gone" } }),
      },
    ]);
    await useLiveExecution.getState().reconcile("biz_test", "proj_1", "t_1");
    const run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.outcome).toBe("not_found");
  });

  it("maps 401/403/404/422/429/5xx to honest records", async () => {
    signIn();
    const cases: [number, unknown, string][] = [
      [403, { error: { code: "forbidden", message: "CSRF validation failed." } }, "forbidden"],
      [404, { error: { code: "not_found", message: "Task not found in scope." } }, "not_found"],
      [422, { error: { code: "validation_error", message: "Request failed validation." } }, "validation_error"],
      [429, { error: { code: "rate_limited", message: "Slow down." } }, "rate_limited"],
      [500, { error: { code: "internal", message: "Unexpected server error." } }, "internal"],
    ];
    for (const [status, body, code] of cases) {
      mockFlow([
        { test: (u) => u.includes("/executions"), respond: () => jsonResponse(status, body) },
        { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
      ]);
      useLiveExecution.getState().clear("biz_test", "proj_1", "t_1");
      await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
      expect(runOf()?.errorCode).toBe(code);
    }

    mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () =>
          jsonResponse(
            500,
            execJson("unknown", { code: "compensation_failed", message: "state unknown" }),
          ),
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    useLiveExecution.getState().clear("biz_test", "proj_1", "t_1");
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(runOf()?.phase).toBe("uncertain");
  });

  it("401 flips the session to anonymous via the shared flow", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () => jsonResponse(401, { error: { code: "unauthorized", message: "Authentication required." } }),
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    signIn();
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    expect(useSession.getState().status).toBe("anonymous");
  });

  it("drops late completions after clear", async () => {
    const gate = deferred<Response>();
    mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () => gate.promise,
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("not_started")) },
    ]);
    signIn();
    const pending = useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    await Promise.resolve();
    useLiveExecution.getState().clear("biz_test", "proj_1", "t_1");
    gate.resolve(jsonResponse(200, execJson("completed")));
    await pending;
    expect(runOf()).toBeUndefined();
  });

  it("failed runs record the provider code and refresh the task", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/executions"),
        respond: () =>
          jsonResponse(200, execJson("failed", { code: "bad_response", message: "not JSON" })),
      },
      { test: () => true, respond: () => jsonResponse(200, taskJson("failed")) },
    ]);
    signIn();
    useLiveTasks.setState({ status: "ready", key: "biz_test/proj_1", tasks: [], error: null });
    await useLiveExecution.getState().run("biz_test", "proj_1", "t_1");
    const run = runOf();
    expect(run?.phase).toBe("done");
    expect(run?.outcome).toBe("failed");
    expect(run?.message).toContain("bad_response");
    expect(useLiveTasks.getState().tasks.find((t) => t.id === "t_1")?.status).toBe("failed");
  });
});

describe("TaskRunButton states", () => {
  const ids = { businessId: "biz_test", projectId: "proj_1", taskId: "t_1" };

  function show() {
    return renderToStaticMarkup(createElement(TaskRunButton, { ...ids }));
  }

  it("idle offers Run plus the provider notice", () => {
    const html = show();
    expect(html).toContain("Run");
    expect(html).toContain("provider unverified");
  });

  it("maps every run phase to controls without implying approval", async () => {
    const { describeRun } = await import("@/components/system/TaskRun");
    const view = (run: Parameters<typeof describeRun>[0]) => describeRun(run);

    expect(view(undefined)).toMatchObject({ kind: "run", showStop: false });
    expect(
      view({ phase: "running", outcome: null, message: "m", errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "running", showStop: true, showRunAgain: false });
    expect(
      view({ phase: "uncertain", outcome: null, message: "m", errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "uncertain", tone: "warn", showRecheck: true, showRunAgain: false });
    expect(
      view({ phase: "done", outcome: "completed", message: "m", errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "result", tone: "ok", showRunAgain: true, showRecheck: false });
    expect(
      view({ phase: "done", outcome: "running", message: "m", errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "result", showRecheck: true, showRunAgain: false });
    expect(
      view({
        phase: "done",
        outcome: "failed",
        message: "m",
        errorCode: "bad_response",
        updatedAt: 1,
      }),
    ).toMatchObject({ kind: "result", tone: "bad", showRunAgain: true });
    expect(
      view({
        phase: "done",
        outcome: "refused",
        message: "Waiting for approval — must approve.",
        errorCode: "approval_required",
        updatedAt: 1,
      }),
    ).toMatchObject({ kind: "result", tone: "warn", showRunAgain: true });
  });
});
