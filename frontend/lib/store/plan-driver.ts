"use client";

/**
 * Client-driven plan execution (Stage 8.4) — advances one persisted project
 * through the EXISTING execution APIs. The browser coordinates; nothing runs
 * here after it closes (no worker, queue, SSE, or server-side continuation).
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - Backend state is authoritative: every step re-lists tasks + approvals
 *   before deciding. Transient React state never gates a POST.
 * - Sequential, one flight: a single driver loop per project scope; tasks run
 *   one at a time through `useLiveExecution.run`, which already does
 *   read-before-write plus reconcile — the server CAS has the final word.
 * - Eligibility is computed from fresh state only: runnable status
 *   (not_started/failed with attempts budget left) + all dependsOn completed
 *   + gate approved-or-absent. Anything else waits, polls, or blocks — the
 *   driver never POSTs a task the server would refuse.
 * - No blind retry: after an ambiguous outcome (network/timeout around the
 *   POST) the driver reconciles with GETs first; the next POST (if any) only
 *   follows a fresh runnable read, inside `run()` itself.
 * - Stuck `working` rows get ONE server-adjudicated recovery probe after
 *   several unchanged polls (`recovered` → continue, `not_stale` → keep
 *   waiting). The driver never force-resets anything.
 * - Approvals are waited on, never bypassed: a pending gate parks the driver
 *   in `waiting_approval` until a human decides (on the Approvals page).
 * - Stop is honest: it halts the CLIENT loop and the client wait only. A
 *   server run already started keeps going; the label says so.
 * - Brief lifecycle is read-only here: a `completed` brief executes nothing;
 *   all tasks `completed` ends in `awaiting_finalization` — finalization
 *   (report/Brain) is a later stage, never fabricated here.
 * - DEMO ban: runs only with an authenticated session whose membership list
 *   contains the target business. The DEMO sim engine is untouched.
 */

import { create } from "zustand";
import {
  ApiError,
  apiGetBrief,
  apiGetProject,
  apiGetTask,
  apiListApprovals,
  apiListTasks,
  apiRecoverTask,
  isAbortError,
  recoveryOutcomeOf,
  type ApiApproval,
  type BriefStatus,
} from "@/lib/api";
import { toDomainApproval, toDomainProject, toDomainTask } from "@/lib/live/mappers";
import { useLiveExecution } from "@/lib/store/execution";
import { useLiveProject, useLiveTasks } from "@/lib/store/live";
import { useSession } from "@/lib/store/auth";
import type { Approval, Task } from "@/lib/domain/types";

/** Mirror of the server's attempt budget (executor MAX_ATTEMPTS) — the server adjudicates anyway. */
export const PLAN_MAX_ATTEMPTS = 3;
/** Consecutive polls seeing the same task `working` before one recovery probe. */
export const STUCK_WORKING_POLLS = 3;
const TASK_POLL_MS = 3000;
const APPROVAL_POLL_MS = 4000;
const LIST_RETRIES = 3;
const BACKOFF_MS = [1000, 2000, 4000];

export type DriverPhase =
  | "idle"
  | "loading"
  | "ready"
  | "running"
  | "waiting_approval"
  | "blocked"
  | "awaiting_finalization"
  | "failed"
  | "stopped"
  | "error";

export interface DriverRecord {
  phase: DriverPhase;
  briefStatus: BriefStatus | null;
  total: number;
  completed: number;
  currentTaskId: string | null;
  waitingApprovalId: string | null;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface DriverStore {
  drivers: Record<string, DriverRecord>;
  start: (businessId: string, projectId: string, opts?: { briefId?: string }) => Promise<void>;
  stop: (businessId: string, projectId: string) => void;
  clear: (businessId: string, projectId: string) => void;
}

const idleDriver: DriverRecord = {
  phase: "idle",
  briefStatus: null,
  total: 0,
  completed: 0,
  currentTaskId: null,
  waitingApprovalId: null,
  message: null,
  errorCode: null,
  updatedAt: 0,
};

export function driverKeyOf(businessId: string, projectId: string): string {
  return `${businessId}/${projectId}`;
}

const flights: Record<string, Promise<void> | undefined> = {};
const seqs: Record<string, number> = {};
const wakers: Record<string, (() => void) | undefined> = {};

function setDriver(key: string, patch: Partial<DriverRecord>): void {
  usePlanDriver.setState((s) => ({
    drivers: {
      ...s.drivers,
      [key]: { ...s.drivers[key], ...idleDriver, ...patch, updatedAt: Date.now() },
    },
  }));
}

function sleep(key: string, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (wakers[key]) delete wakers[key];
      resolve();
    }, ms);
    wakers[key] = () => {
      clearTimeout(timer);
      delete wakers[key];
      resolve();
    };
  });
}

function wake(key: string): void {
  wakers[key]?.();
}

// ---------------------------------------------------------------------------
// Pure eligibility (unit-tested): fresh server state in, decision out.
// ---------------------------------------------------------------------------

export interface BlockedTask {
  task: Task;
  reason: string;
}

export interface ScopeClassification {
  total: number;
  completed: number;
  eligible: Task | null;
  waitingApproval: Approval | null;
  working: Task[];
  blocked: BlockedTask[];
  /** Incomplete tasks with no path forward and nothing in flight. */
  stalled: boolean;
}

export function classifyScope(tasks: Task[], approvals: Approval[]): ScopeClassification {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const approvalById = new Map(approvals.map((a) => [a.id, a]));
  const completed = tasks.filter((t) => t.status === "completed").length;
  const working = tasks.filter((t) => t.status === "working");
  const blocked: BlockedTask[] = [];
  let eligible: Task | null = null;
  let waitingApproval: Approval | null = null;

  for (const task of tasks) {
    if (task.status === "completed" || task.status === "working") continue;
    if (task.status !== "not_started" && task.status !== "failed") continue;
    if (task.status === "failed" && (task.attempts ?? 0) >= PLAN_MAX_ATTEMPTS) {
      blocked.push({ task, reason: `Attempt budget exhausted (${task.attempts}/${PLAN_MAX_ATTEMPTS}) — human decision required.` });
      continue;
    }
    const openDep = task.dependsOn.find((depId) => byId.get(depId)?.status !== "completed");
    if (openDep !== undefined) {
      if (!byId.has(openDep)) {
        blocked.push({ task, reason: `Depends on unknown task "${openDep}" — plan reference is stale.` });
      }
      continue;
    }
    if (task.approvalGateId) {
      const gate = approvalById.get(task.approvalGateId);
      if (!gate) {
        blocked.push({ task, reason: "Approval gate record is missing — refusing to run blind." });
        continue;
      }
      if (gate.status === "pending") {
        waitingApproval ??= gate;
        continue;
      }
      if (gate.status !== "approved") {
        blocked.push({ task, reason: `Approval gate is ${gate.status} — human decision required.` });
        continue;
      }
    }
    eligible ??= task;
  }

  const incomplete = tasks.length - completed;
  const stalled =
    incomplete > 0 && eligible === null && waitingApproval === null && working.length === 0;
  return { total: tasks.length, completed, eligible, waitingApproval, working, blocked, stalled };
}

// ---------------------------------------------------------------------------
// Authoritative snapshot (fresh lists every step; transient errors back off).
// ---------------------------------------------------------------------------

interface Snapshot {
  tasks: Task[];
  approvals: Approval[];
}

function isTransient(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || err.status >= 500);
}

async function listSnapshot(businessId: string, projectId: string): Promise<Snapshot> {
  let last: unknown = null;
  for (let attempt = 0; attempt < LIST_RETRIES; attempt += 1) {
    try {
      const [taskPage, approvalPage] = await Promise.all([
        apiListTasks(businessId, projectId, { limit: 200 }),
        apiListApprovals(businessId, { limit: 200 }),
      ]);
      for (const t of taskPage.items) {
        useLiveTasks.getState().upsertTask(toDomainTask(t));
      }
      const proj = useLiveProject.getState();
      if (proj.key === `${businessId}/${projectId}`) {
        // Background refresh must NOT reset the slice: load() clears the
        // project and unmounts the mounted detail view (including this
        // driver). Apply the server-confirmed row in place instead.
        try {
          proj.replace(toDomainProject(await apiGetProject(businessId, projectId)));
        } catch {
          // Best effort: the tasks below are the snapshot payload; the
          // header refreshes on the next poll.
        }
      }
      return {
        tasks: taskPage.items.map(toDomainTask),
        approvals: approvalPage.items
          .filter((a: ApiApproval) => a.projectId === projectId)
          .map(toDomainApproval),
      };
    } catch (err) {
      last = err;
      if (!isTransient(err)) throw err;
      await new Promise((r) => setTimeout(r, BACKOFF_MS[attempt] ?? 4000));
    }
  }
  throw last;
}

// ---------------------------------------------------------------------------
// Driver loop.
// ---------------------------------------------------------------------------

async function driveWork(
  key: string,
  businessId: string,
  projectId: string,
  briefId: string | undefined,
  seq: number,
): Promise<void> {
  const alive = () => seqs[key] === seq;
  const finish = (patch: Partial<DriverRecord>) => {
    if (alive()) setDriver(key, patch);
  };

  // --- Gate 1: proven session + membership (DEMO ban). ---
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    finish({ phase: "error", message: "Sign in to run a project.", errorCode: "unauthorized" });
    return;
  }
  if (!session.businesses.includes(businessId)) {
    finish({ phase: "error", message: "No access to this business.", errorCode: "forbidden" });
    return;
  }

  // --- Gate 2: persisted brief reference resolves; completed executes nothing. ---
  let briefStatus: BriefStatus | null = null;
  if (briefId !== undefined) {
    finish({ phase: "loading", message: "Loading persisted plan…", errorCode: null });
    try {
      const brief = await apiGetBrief(businessId, briefId);
      if (!alive()) return;
      briefStatus = brief.status;
      if (brief.status === "completed") {
        finish({
          phase: "awaiting_finalization",
          briefStatus,
          message: "Brief already completed — nothing to execute.",
          errorCode: null,
        });
        return;
      }
      if (brief.status !== "planned" && brief.status !== "running") {
        finish({
          phase: "error",
          briefStatus,
          message: `Brief is ${brief.status} — plan it before running.`,
          errorCode: "conflict",
        });
        return;
      }
    } catch (err) {
      if (isAbortError(err) || !alive()) return;
      finish({
        phase: "error",
        message: err instanceof ApiError && err.status === 404
          ? "Brief not found in this business."
          : "Couldn't load the brief — nothing was started.",
        errorCode: err instanceof ApiError ? err.code : null,
      });
      return;
    }
  }

  finish({ phase: "ready", briefStatus, message: "Starting from server state…", errorCode: null });
  const stuck = new Map<string, number>();

  for (;;) {
    if (!alive()) return;
    let snap: Snapshot;
    try {
      snap = await listSnapshot(businessId, projectId);
    } catch (err) {
      if (isAbortError(err) || !alive()) return;
      finish({
        phase: "error",
        briefStatus,
        message: err instanceof ApiError && err.status === 404
          ? "Project not found in this business."
          : "Lost contact while reading project state — stopped. Resume to continue.",
        errorCode: err instanceof ApiError ? err.code : null,
      });
      return;
    }
    if (!alive()) return;

    const c = classifyScope(snap.tasks, snap.approvals);
    const base = { briefStatus, total: c.total, completed: c.completed, currentTaskId: null as string | null, waitingApprovalId: null as string | null };

    if (c.total === 0) {
      finish({ ...base, phase: "blocked", message: "No tasks in this project.", errorCode: null });
      return;
    }
    if (c.completed === c.total) {
      finish({
        ...base,
        phase: "awaiting_finalization",
        message: "All tasks complete — finalization is pending and not done here.",
        errorCode: null,
      });
      return;
    }
    if (c.blocked.length > 0 && c.eligible === null && c.waitingApproval === null && c.working.length === 0) {
      finish({ ...base, phase: "blocked", message: c.blocked[0].reason, errorCode: null });
      return;
    }
    if (c.stalled) {
      finish({
        ...base,
        phase: "blocked",
        message: "No runnable tasks and nothing in flight — the plan is stalled.",
        errorCode: null,
      });
      return;
    }

    if (c.eligible !== null) {
      const task = c.eligible;
      stuck.delete(task.id);
      finish({ ...base, phase: "running", currentTaskId: task.id, message: `Running ${task.title}…`, errorCode: null });
      await useLiveExecution.getState().run(businessId, projectId, task.id);
      // `run()` reconciles uncertain outcomes with GETs internally; the loop
      // re-fetches authoritative state before any next POST — never blind.
      if (!alive()) return;
      // Fail fast on a clean failure: failures are never retried
      // automatically — the user resumes deliberately, if at all.
      try {
        const fresh = await apiGetTask(businessId, projectId, task.id);
        if (!alive()) return;
        if (fresh.status === "failed") {
          const domain = toDomainTask(fresh);
          useLiveTasks.getState().upsertTask(domain);
          finish({
            ...base,
            phase: "failed",
            currentTaskId: null,
            message: `${task.title} failed${fresh.attempts ? ` (attempt ${fresh.attempts})` : ""} — not retried automatically.`,
            errorCode: null,
          });
          return;
        }
      } catch {
        // Best effort: the loop's fresh list below is authoritative anyway.
      }
      continue;
    }

    if (c.waitingApproval !== null) {
      const gate = c.waitingApproval;
      finish({
        ...base,
        phase: "waiting_approval",
        waitingApprovalId: gate.id,
        message: `Waiting for approval: ${gate.title}. Decide it on the Approvals page, then the driver resumes.`,
        errorCode: null,
      });
      await sleep(key, APPROVAL_POLL_MS);
      continue;
    }

    // Nothing eligible but work in flight: watch, probe once if stuck, else wait.
    const stuckTask = c.working.find((t) => (stuck.get(t.id) ?? 0) >= STUCK_WORKING_POLLS);
    if (stuckTask !== undefined) {
      stuck.delete(stuckTask.id);
      try {
        await apiRecoverTask(businessId, projectId, stuckTask.id);
        if (!alive()) return;
        finish({ ...base, phase: "running", message: `Recovered ${stuckTask.title} — re-checking…`, errorCode: null });
      } catch (err) {
        if (isAbortError(err) || !alive()) return;
        const outcome = err instanceof ApiError ? recoveryOutcomeOf(err.payload) : null;
        if (outcome === "not_stale" || outcome === "not_found") {
          finish({ ...base, phase: "running", message: `${stuckTask.title} is still live on the server — waiting…`, errorCode: null });
        } else if (err instanceof ApiError) {
          finish({ ...base, phase: "running", message: `Recovery probe failed (${err.code}) — waiting…`, errorCode: err.code });
        } else {
          finish({ ...base, phase: "running", message: "Recovery probe failed — waiting…", errorCode: null });
        }
      }
      continue;
    }
    for (const t of c.working) stuck.set(t.id, (stuck.get(t.id) ?? 0) + 1);
    finish({
      ...base,
      phase: "running",
      currentTaskId: c.working[0]?.id ?? null,
      message: "Tasks running on the server — watching…",
      errorCode: null,
    });
    await sleep(key, TASK_POLL_MS);
  }
}

export const usePlanDriver = create<DriverStore>((set) => ({
  drivers: {},
  start: (businessId, projectId, opts) => {
    const key = driverKeyOf(businessId, projectId);
    const flight = flights[key];
    if (flight) return flight;
    const seq = (seqs[key] ?? 0) + 1;
    seqs[key] = seq;
    const promise = driveWork(key, businessId, projectId, opts?.briefId, seq).finally(() => {
      if (flights[key] === promise) delete flights[key];
    });
    flights[key] = promise;
    return promise;
  },
  stop: (businessId, projectId) => {
    const key = driverKeyOf(businessId, projectId);
    seqs[key] = (seqs[key] ?? 0) + 1;
    wake(key);
    const current = usePlanDriver.getState().drivers[key]?.currentTaskId;
    if (current) {
      // Abort the CLIENT wait only — a started server run keeps going.
      useLiveExecution.getState().stopWaiting(businessId, projectId, current);
    }
    set((s) => {
      const prev = s.drivers[key];
      if (!prev || prev.phase === "idle") return s;
      return {
        drivers: {
          ...s.drivers,
          [key]: {
            ...prev,
            phase: "stopped",
            currentTaskId: null,
            waitingApprovalId: null,
            message: "Driver stopped. A server run already started keeps going — re-check before resuming.",
            errorCode: null,
            updatedAt: Date.now(),
          },
        },
      };
    });
  },
  clear: (businessId, projectId) => {
    const key = driverKeyOf(businessId, projectId);
    seqs[key] = (seqs[key] ?? 0) + 1;
    wake(key);
    usePlanDriver.setState((s) => {
      if (!(key in s.drivers)) return s;
      const drivers = { ...s.drivers };
      delete drivers[key];
      return { drivers };
    });
  },
}));

/** Test-only: drop flights/sequences without touching records. */
export function __resetPlanDriverForTests(): void {
  for (const key of Object.keys(flights)) delete flights[key];
  for (const key of Object.keys(seqs)) delete seqs[key];
  for (const key of Object.keys(wakers)) delete wakers[key];
  usePlanDriver.setState({ drivers: {} });
}
