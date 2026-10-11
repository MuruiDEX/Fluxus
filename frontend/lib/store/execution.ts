"use client";

/**
 * LIVE task execution runner (Stage 7.4) — drives the existing backend
 * `POST .../executions` endpoint and reconciles through `GET task`.
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - DEMO ban: runs only with an authenticated session whose membership list
 *   contains the target business. Anything else records an error, sends nothing.
 * - Single flight per task: concurrent run() calls (double click, StrictMode,
 *   remount) share one promise — exactly one POST per generation.
 * - Reconcile-before-POST: every run first reads the task. Completed tasks
 *   show the saved result, running tasks are never re-fired; only
 *   not_started/failed tasks reach the POST, and the server CAS has the
 *   final word (already_running/already_completed just re-read state).
 * - Uncertain, never "failed", on lost contact: abort/network/timeout after
 *   sending leaves the outcome unknown — the record says so and offers
 *   re-check. `AbortController.abort()` stops the CLIENT wait only; it is
 *   never presented as cancelling the server run.
 * - Stale generations are dropped by sequence guard; leaving the page does
 *   NOT abort the request (no false "cancelled"), and returning re-reads
 *   state through the normal page loads.
 *
 * Provider honesty: the wire carries no provider marker (verified against
 * app/llm + ExecutionResponse), so results are never labeled live AI. The
 * UI shows a standing provider notice instead (see ProviderNotice).
 */

import { create } from "zustand";
import {
  ApiError,
  apiGetTask,
  apiRunExecution,
  executionErrorOf,
  executionOutcomeOf,
  isAbortError,
  type ApiExecution,
} from "@/lib/api";
import { toDomainTask } from "@/lib/live/mappers";
import { useLiveProject, useLiveTasks } from "@/lib/store/live";
import { useSession } from "@/lib/store/auth";
import type { Task } from "@/lib/domain/types";

export type RunPhase = "idle" | "running" | "done" | "uncertain";

export interface TaskRun {
  phase: RunPhase;
  /** Execution outcome word, or the reconciled server task status. */
  outcome: string | null;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface ExecutionState {
  runs: Record<string, TaskRun>;
  run: (businessId: string, projectId: string, taskId: string) => Promise<void>;
  reconcile: (businessId: string, projectId: string, taskId: string) => Promise<void>;
  stopWaiting: (businessId: string, projectId: string, taskId: string) => void;
  clear: (businessId: string, projectId: string, taskId: string) => void;
}

const idleRun: TaskRun = { phase: "idle", outcome: null, message: null, errorCode: null, updatedAt: 0 };

function keyOf(businessId: string, projectId: string, taskId: string): string {
  return `${businessId}/${projectId}/${taskId}`;
}

const flights: Record<string, Promise<void> | undefined> = {};
const controllers: Record<string, AbortController | undefined> = {};
const seqs: Record<string, number> = {};

function setRun(key: string, patch: Partial<TaskRun>): void {
  useLiveExecution.setState((s) => ({
    runs: { ...s.runs, [key]: { ...s.runs[key], ...idleRun, ...patch, updatedAt: Date.now() } },
  }));
}

/** Refresh the task row in the shared LIVE cache (same id = same row, any scope). */
function upsertTask(task: Task): void {
  useLiveTasks.getState().upsertTask(task);
}

/** Refresh the open project header (spend/progress move after a terminal run). */
function refreshProjectIfOpen(businessId: string, projectId: string): void {
  const proj = useLiveProject.getState();
  if (proj.key === `${businessId}/${projectId}`) {
    void proj.load(businessId, projectId);
  }
}

async function readTask(businessId: string, projectId: string, taskId: string): Promise<Task> {
  const raw = await apiGetTask(businessId, projectId, taskId);
  return toDomainTask(raw);
}

function approvalMessage(): string {
  return (
    "Waiting for approval — a human must approve this step before it can run. " +
    "Decide it on the Approvals page."
  );
}

function refusedMessage(code: string, fallback: string): string {
  if (code === "approval_required") return approvalMessage();
  return fallback;
}

/**
 * Map a reconciled server task into a run record. Returns true when the
 * outcome is terminally known (completed/failed/gone), false when the task
 * is still in flight server-side.
 */
function applyReconciled(key: string, task: Task): boolean {
  upsertTask(task);
  if (task.status === "completed") {
    setRun(key, {
      phase: "done",
      outcome: "completed",
      message: "Completed — showing the saved result.",
      errorCode: null,
    });
    return true;
  }
  if (task.status === "failed") {
    setRun(key, {
      phase: "done",
      outcome: "failed",
      message: `Failed${task.attempts ? ` (attempt ${task.attempts})` : ""} — not retried automatically.`,
      errorCode: null,
    });
    return true;
  }
  if (task.status === "working") {
    setRun(key, {
      phase: "done",
      outcome: "running",
      message: "Still running on the server — not started again. Re-check for updates.",
      errorCode: null,
    });
    return false;
  }
  setRun(key, {
    phase: "done",
    outcome: task.status,
    message: `Server reports status "${task.status}". Safe to run from here.`,
    errorCode: null,
  });
  return true;
}

async function reconcileWork(
  key: string,
  businessId: string,
  projectId: string,
  taskId: string,
  isCurrent: () => boolean,
): Promise<void> {
  try {
    const task = await readTask(businessId, projectId, taskId);
    if (!isCurrent()) return;
    applyReconciled(key, task);
  } catch (err) {
    if (isAbortError(err) || !isCurrent()) return;
    if (err instanceof ApiError && err.status === 404) {
      setRun(key, {
        phase: "done",
        outcome: "not_found",
        message: "Task no longer exists in this scope.",
        errorCode: "not_found",
      });
      return;
    }
    setRun(key, {
      phase: "uncertain",
      outcome: null,
      message: "Couldn't re-read the task — outcome still unknown. Try re-checking.",
      errorCode: err instanceof ApiError ? err.code : null,
    });
  }
}

async function runWork(
  key: string,
  businessId: string,
  projectId: string,
  taskId: string,
  seq: number,
): Promise<void> {
  const alive = () => seqs[key] === seq;
  const finish = (patch: Partial<TaskRun>) => {
    if (alive()) setRun(key, patch);
  };

  // --- Gate 1: proven session + membership (DEMO ban). ---
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    finish({ phase: "done", message: "Sign in to run tasks.", errorCode: "unauthorized" });
    return;
  }
  if (!session.businesses.includes(businessId)) {
    finish({ phase: "done", message: "No access to this business.", errorCode: "forbidden" });
    return;
  }

  // --- Gate 2: read-before-write. Never POST blind. ---
  let current: Task;
  try {
    current = await readTask(businessId, projectId, taskId);
  } catch (err) {
    if (isAbortError(err) || !alive()) return;
    if (err instanceof ApiError && err.status === 404) {
      finish({
        phase: "done",
        outcome: "not_found",
        message: "Task no longer exists in this scope.",
        errorCode: "not_found",
      });
      return;
    }
    finish({
      phase: "uncertain",
      outcome: null,
      message: "Couldn't read the task before running — nothing was sent. Try again.",
      errorCode: err instanceof ApiError ? err.code : null,
    });
    return;
  }
  if (!alive()) return;
  upsertTask(current);
  if (current.status === "completed") {
    finish({ phase: "done", outcome: "completed", message: "Already completed — showing the saved result.", errorCode: null });
    return;
  }
  if (current.status === "working") {
    finish({ phase: "done", outcome: "running", message: "Still running on the server — not started again.", errorCode: null });
    return;
  }
  if (current.status !== "not_started" && current.status !== "failed") {
    finish({ phase: "done", outcome: current.status, message: `Server reports status "${current.status}".`, errorCode: null });
    return;
  }

  // --- POST: exactly one flight; the server CAS adjudicates races. ---
  const controller = new AbortController();
  controllers[key] = controller;
  setRun(key, { phase: "running", outcome: null, message: "Running on the server…", errorCode: null });
  let execution: ApiExecution;
  try {
    execution = await apiRunExecution(businessId, projectId, taskId, { signal: controller.signal });
  } catch (err) {
    if (!alive()) return;
    if (isAbortError(err)) {
      finish({
        phase: "uncertain",
        outcome: null,
        message: "Stopped waiting — the server may still be running it. Re-check before retrying.",
        errorCode: null,
      });
      return;
    }
    if (!(err instanceof ApiError)) {
      finish({ phase: "uncertain", outcome: null, message: "Unexpected client error.", errorCode: null });
      return;
    }
    if (err.status === 0) {
      // Network died around the POST: the claim may or may not have landed.
      finish({
        phase: "uncertain",
        outcome: null,
        message: "Lost contact with the server — outcome unknown. Re-check before retrying.",
        errorCode: err.code,
      });
      return;
    }
    const outcome = executionOutcomeOf(err.payload);
    if (outcome === "already_completed") {
      await reconcileWork(key, businessId, projectId, taskId, alive);
      return;
    }
    if (outcome === "already_running") {
      await reconcileWork(key, businessId, projectId, taskId, alive);
      return;
    }
    if (outcome === "refused") {
      const info = executionErrorOf(err.payload);
      const code = info?.code ?? err.code;
      try {
        const task = await readTask(businessId, projectId, taskId);
        upsertTask(task);
      } catch {
        // Best effort: the refusal message below is the source of truth.
      }
      if (!alive()) return;
      finish({
        phase: "done",
        outcome: "refused",
        message: refusedMessage(code, info?.message ?? err.message),
        errorCode: code,
      });
      return;
    }
    if (outcome === "unknown") {
      const info = executionErrorOf(err.payload);
      finish({
        phase: "uncertain",
        outcome: "unknown",
        message: `${info?.message ?? err.message} Re-check before retrying.`,
        errorCode: info?.code ?? err.code,
      });
      return;
    }
    finish({ phase: "done", outcome: null, message: err.message, errorCode: err.code });
    return;
  } finally {
    if (controllers[key] === controller) delete controllers[key];
  }
  if (!alive()) return;

  // --- 2xx answers: completed / failed / already_completed. ---
  if (execution.outcome === "already_completed") {
    await reconcileWork(key, businessId, projectId, taskId, alive);
    return;
  }
  if (execution.outcome === "failed") {
    try {
      const task = await readTask(businessId, projectId, taskId);
      upsertTask(task);
      refreshProjectIfOpen(businessId, projectId);
    } catch {
      // Fall through to the inline error below.
    }
    if (!alive()) return;
    const code = execution.error?.code ?? "internal";
    finish({
      phase: "done",
      outcome: "failed",
      message: `Run failed (${code}): ${execution.error?.message ?? "no details"}.`,
      errorCode: code,
    });
    return;
  }
  // completed
  try {
    const task = await readTask(businessId, projectId, taskId);
    upsertTask(task);
    refreshProjectIfOpen(businessId, projectId);
  } catch {
    // The completion itself stands; the re-read is best effort.
  }
  if (!alive()) return;
  finish({ phase: "done", outcome: "completed", message: "Completed.", errorCode: null });
}

export const useLiveExecution = create<ExecutionState>(() => ({
  runs: {},
  run: (businessId, projectId, taskId) => {
    const key = keyOf(businessId, projectId, taskId);
    const flight = flights[key];
    if (flight) return flight;
    const seq = (seqs[key] ?? 0) + 1;
    seqs[key] = seq;
    const promise = runWork(key, businessId, projectId, taskId, seq).finally(() => {
      if (flights[key] === promise) delete flights[key];
    });
    flights[key] = promise;
    return promise;
  },
  reconcile: (businessId, projectId, taskId) => {
    const key = keyOf(businessId, projectId, taskId);
    const flight = flights[key];
    if (flight) return flight;
    const seq = (seqs[key] ?? 0) + 1;
    seqs[key] = seq;
    const promise = reconcileWork(key, businessId, projectId, taskId, () => seqs[key] === seq).finally(
      () => {
        if (flights[key] === promise) delete flights[key];
      },
    );
    flights[key] = promise;
    return promise;
  },
  stopWaiting: (businessId, projectId, taskId) => {
    controllers[keyOf(businessId, projectId, taskId)]?.abort();
  },
  clear: (businessId, projectId, taskId) => {
    const key = keyOf(businessId, projectId, taskId);
    seqs[key] = (seqs[key] ?? 0) + 1;
    useLiveExecution.setState((s) => {
      if (!(key in s.runs)) return s;
      const runs = { ...s.runs };
      delete runs[key];
      return { runs };
    });
  },
}));
