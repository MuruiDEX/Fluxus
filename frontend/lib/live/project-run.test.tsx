import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectRunControls, ProjectRunStatus } from "@/components/system/ProjectRun";
import { ProjectWorkflowPanel, type useProjectWorkflow } from "@/components/system/PlanDriver";
import { ApiError, apiCommandProjectRun, apiGetProjectRun, setCsrfToken, type ApiProjectRun } from "@/lib/api";
import type { Approval, Task } from "@/lib/domain/types";
import {
  acceptsRunResponse, isLegacyRunRoute, reconcileRunCommandError, retryTarget, runMode, scopedRunValue,
  summarizeRunTasks,
} from "@/lib/live/project-run";

const run: ApiProjectRun = {
  runId: "run-1", state: "running", version: 4, schedulingMode: "managed", reasonCode: null,
  blockerId: null, activeTaskId: null, activeTaskGeneration: null, recoveredTaskGeneration: null,
  retryTaskId: null, allowedActions: ["pause", "cancel"], updatedAt: null, contract: "v1",
};
const idle: ApiProjectRun = {
  ...run, runId: null, state: "idle", version: 0, schedulingMode: "manual", allowedActions: [],
};

function task(id: string, status: Task["status"], fields: Partial<Task> = {}) {
  return { id, title: id, status, dependsOn: [], ...fields } as Task;
}

afterEach(() => {
  setCsrfToken(null);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("persisted project workflow", () => {
  it("uses managed controls for an explicitly startable idle project and keeps genuine legacy mode", () => {
    expect(runMode(null)).toBe("unknown");
    expect(runMode(null, true)).toBe("manual");
    expect(runMode(idle)).toBe("manual");
    expect(runMode({ ...idle, allowedActions: ["start"] })).toBe("server");
    expect(runMode(run)).toBe("server");
  });

  it("requires the exact absent-route response before falling back to browser execution", () => {
    const error = new ApiError({
      status: 404, code: "not_found", message: "No route for /api/v1/businesses/b/projects/p/run.",
    });
    expect(isLegacyRunRoute(error, "b", "p")).toBe(true);
    expect(isLegacyRunRoute(error, "other", "p")).toBe(false);
    expect(isLegacyRunRoute(new ApiError({
      status: 404, code: "not_found", message: "Project not found in scope.",
    }), "b", "p")).toBe(false);
  });

  it("rejects old responses after switching businesses or aborting a superseded read", () => {
    const oldScope = {};
    const newScope = {};
    const request = new AbortController();
    expect(acceptsRunResponse(oldScope, oldScope, request.signal)).toBe(true);
    expect(acceptsRunResponse(newScope, oldScope, request.signal)).toBe(false);
    expect(acceptsRunResponse(null, oldScope, request.signal)).toBe(false);
    request.abort();
    expect(acceptsRunResponse(oldScope, oldScope, request.signal)).toBe(false);
    expect(scopedRunValue({ key: "business-a/project", value: run }, "business-b/project")).toBeNull();
  });

  it("only sends Retry for a task with a matching server-provided generation", () => {
    const blocked: ApiProjectRun = {
      ...run, state: "needs_attention", blockerId: "task-1", allowedActions: ["retry"],
    };
    expect(retryTarget(blocked)).toBeNull();
    expect(retryTarget({ ...blocked, activeTaskId: "task-2", activeTaskGeneration: 2 })).toBeNull();
    expect(retryTarget({
      ...blocked, activeTaskId: "task-1", activeTaskGeneration: 2, recoveredTaskGeneration: 3,
    })).toEqual({ taskId: "task-1", expectedTaskGeneration: 3 });
  });

  it("counts committed completion, failures, pending gates, and unfinished dependencies", () => {
    const tasks = [
      task("done", "completed"), task("retry", "failed"),
      task("gate", "not_started", { approvalGateId: "ap" }),
      task("clear", "not_started", { dependsOn: ["done"] }),
      task("blocked", "not_started", { dependsOn: ["retry"] }),
    ];
    expect(summarizeRunTasks(tasks, [{ id: "ap", status: "pending" } as Approval]))
      .toEqual({ completed: 1, failed: 1, waiting: 1, blocked: 1, total: 5 });
  });

  it("renders waiting, last known state, and reduced-motion progress", () => {
    const markup = renderToStaticMarkup(<ProjectRunStatus
      run={{ ...run, state: "waiting_approval", reasonCode: "approval_pending" }}
      tasks={[task("waiting", "waiting_approval"), task("done", "completed")]}
      approvals={[]} agents={[]} stale
    />);
    expect(markup).toContain("Waiting for approval");
    expect(markup).toContain('href="/approvals"');
    expect(markup).toContain("Showing last known status");
    expect(markup).toContain('aria-valuenow="50"');
    expect(markup).toContain("motion-reduce:transition-none");
  });

  it("renders authorization-bound actions and requires a separate cancel confirmation", () => {
    const issue = vi.fn();
    const props = {
      run, pending: null, disabled: false, onConfirm: vi.fn(), onDismiss: vi.fn(), issue,
    };
    const initial = renderToStaticMarkup(<ProjectRunControls {...props} confirmation={null} />);
    expect(initial).toContain("Pause");
    expect(initial).toContain("Cancel run");
    expect(initial).not.toContain("Confirm cancellation");
    expect(initial).not.toContain("Resume");
    const confirm = renderToStaticMarkup(<ProjectRunControls {...props} confirmation="cancel" />);
    expect(confirm).toContain("Confirm cancellation");
    expect(confirm).toContain("An active task may still finish.");
    const pausing = renderToStaticMarkup(<ProjectRunControls {...props} confirmation={null} pending="pause" />);
    expect(pausing.match(/disabled=""/g)).toHaveLength(2);
    expect(issue).not.toHaveBeenCalled();
  });

  it("keeps the legacy driver out of managed rendering, loading, and unavailable states", () => {
    const refresh = vi.fn();
    const issue = vi.fn();
    const render = (mode: "server" | "manual" | "unknown", error: string | null = null) => {
      const execution: ReturnType<typeof useProjectWorkflow> = {
        run: { run: mode === "server" ? run : null, mode, error,
          legacyRoute: mode === "manual", pending: null, commandError: null, refresh, issue },
        workflow: { briefId: "brief", phase: "ready", message: null, finalization: null, contract: "v1" },
        error, refresh, saveReport: vi.fn(),
      };
      return renderToStaticMarkup(<ProjectWorkflowPanel businessId="b" projectId="p"
        execution={execution} tasks={[]} approvals={[]} agents={[]} />);
    };
    expect(render("server")).toContain("Pause");
    expect(render("server")).not.toContain('data-testid="plan-driver"');
    expect(render("manual")).toContain('data-testid="plan-driver"');
    expect(render("unknown")).toContain("Loading workflow");
    expect(render("unknown", "Run unavailable")).toContain("Retry refresh");
    expect(issue).not.toHaveBeenCalled();
  });
});

describe("run command transport", () => {
  it("uses the persisted version and retry generation with the existing headers and AbortSignal", async () => {
    setCsrfToken("csrf");
    const signal = new AbortController().signal;
    const fetch = vi.fn().mockResolvedValue(Response.json(run));
    vi.stubGlobal("fetch", fetch);
    await apiCommandProjectRun("business:a", "p-1", 4, {
      action: "retry", taskId: "task-1", expectedTaskGeneration: 3,
    }, { signal });
    const [url, request] = fetch.mock.calls[0];
    expect(url).toContain("/businesses/business%3Aa/projects/p-1/run/retry");
    expect(request).toMatchObject({
      method: "POST", credentials: "include", signal,
      headers: { "X-CSRF-Token": "csrf", "X-Client-Contract": "1" },
    });
    expect(JSON.parse(request.body)).toEqual({
      expectedVersion: 4, taskId: "task-1", expectedTaskGeneration: 3,
    });
  });

  it("refreshes a conflicted command without repeating the POST", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json({
      error: { code: "run_version_conflict", message: "The run has changed." },
    }, { status: 409 })).mockResolvedValueOnce(Response.json(run));
    vi.stubGlobal("fetch", fetch);
    const refresh = vi.fn(() => apiGetProjectRun("b", "p"));
    const result = await apiCommandProjectRun("b", "p", 1, { action: "pause" })
      .catch((error: unknown) => reconcileRunCommandError(error, refresh));
    expect(result).toMatchObject({ message: expect.stringContaining("refreshed state") });
    expect(refresh).toHaveBeenCalledOnce();
    expect(fetch.mock.calls.map(([, options]) => options.method)).toEqual(["POST", "GET"]);
  });

  it("cannot send run commands in DEMO", async () => {
    vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
    vi.stubEnv("NODE_ENV", "development");
    vi.resetModules();
    const { apiCommandProjectRun: commandInDemo } = await import("@/lib/api");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(commandInDemo("b", "p", 0, { action: "start" }))
      .rejects.toMatchObject({ code: "demo_mode_blocked" });
    expect(fetch).not.toHaveBeenCalled();
  });
});
