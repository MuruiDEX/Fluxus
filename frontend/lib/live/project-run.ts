import { ApiError, type ApiProjectRun } from "@/lib/api";
import type { Approval, Task } from "@/lib/domain/types";

export function isLegacyRunRoute(error: unknown, businessId: string, projectId: string): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === "not_found"
    && error.message === `No route for /api/v1/businesses/${businessId}/projects/${projectId}/run.`;
}

export function runMode(run: ApiProjectRun | null, legacyRoute = false) {
  if (run?.schedulingMode === "managed" || run?.allowedActions.includes("start")) return "server";
  if (legacyRoute || (run?.state === "idle" && run.schedulingMode === "manual")) return "manual";
  return "unknown";
}

export function scopedRunValue<T>(value: { key: string; value: T } | null, key: string): T | null {
  return value?.key === key ? value.value : null;
}

export function acceptsRunResponse(activeScope: object | null, requestScope: object, signal: AbortSignal) {
  return activeScope === requestScope && !signal.aborted;
}

export function retryTarget(run: ApiProjectRun) {
  if (!run.allowedActions.includes("retry") || !run.retryTargetTaskId ||
    run.retryTargetGeneration == null) {
    return null;
  }
  return { taskId: run.retryTargetTaskId, expectedTaskGeneration: run.retryTargetGeneration };
}

export function runCommandError(error: unknown) {
  if (error instanceof ApiError && error.status === 409) {
    return {
      message: error.code === "run_version_conflict"
        ? "This run changed in another session. Review the refreshed state before trying again."
        : error.message,
      assumeResponsibility: error.code === "run_authorization_required",
    };
  }
  return {
    message: error instanceof ApiError && error.status > 0
      ? error.message
      : "The command could not be confirmed. Check the run state before trying again.",
    assumeResponsibility: false,
  };
}

export async function reconcileRunCommandError(error: unknown, refresh: () => Promise<unknown>) {
  // Even an interrupted response may represent a committed command.
  await refresh();
  return runCommandError(error);
}

export function summarizeRunTasks(tasks: readonly Task[], approvals: readonly Approval[]) {
  const byId = new Map(tasks.map((task) => [task.id, task.status]));
  const gates = new Map(approvals.map((approval) => [approval.id, approval.status]));
  let completed = 0;
  let failed = 0;
  let blocked = 0;
  let waiting = 0;
  for (const task of tasks) {
    if (task.status === "completed") completed += 1;
    else if (task.status === "failed") failed += 1;
    else if (task.status !== "working") {
      const gate = task.approvalGateId ? gates.get(task.approvalGateId) : undefined;
      if (task.status === "waiting_approval" || gate === "pending") waiting += 1;
      else if (gate === "rejected" || task.status === "waiting_assets" ||
        task.status === "waiting_dependency" ||
        task.dependsOn.some((id) => byId.get(id) !== "completed")) blocked += 1;
    }
  }
  return { completed, failed, blocked, waiting, total: tasks.length };
}
