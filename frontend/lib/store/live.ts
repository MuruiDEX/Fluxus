"use client";

/**
 * LIVE read state (Stage 7.3) — server data only, never mixed with seed.
 *
 * Separate Zustand slices from the demo stores (`lib/store/app.ts`): the
 * simulation engine imports the demo stores explicitly, so it cannot write
 * here by construction (pinned by test). Each load clears its slice first
 * (no stale flash of a previous business/project), abandons the previous
 * request via AbortController, and ignores late responses via a sequence
 * guard — whichever loses, the UI never shows superseded data.
 *
 * Errors: 401 flows through the shared session handler (caller falls back
 * to DEMO); 403/404/5xx/network surface as retryable error state.
 */

import { create } from "zustand";
import {
  ApiError,
  apiGetAgent,
  apiGetProject,
  apiListAgents,
  apiListApprovals,
  apiListActivity,
  apiListBrain,
  apiListProjects,
  apiListTasks,
  isAbortError,
  type BrainCategory,
} from "@/lib/api";
import type { SessionStatus } from "@/lib/store/auth";
import {
  toDomainActivityEvent,
  toDomainAgent,
  toDomainApproval,
  toDomainBrainEntry,
  toDomainProject,
  toDomainTask,
} from "@/lib/live/mappers";
import type { ActivityEvent, Agent, Approval, BrainEntry, Project, Task } from "@/lib/domain/types";

export type LiveLoadStatus = "idle" | "loading" | "ready" | "error";

/** LIVE applies only with a proven session AND a selected member business. */
export function isLiveContext(
  status: SessionStatus,
  businessId: string | null,
): businessId is string {
  return status === "authenticated" && businessId !== null;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "Request failed.";
}

interface LoadCtl {
  seq: number;
  controller: AbortController | null;
}

function createCtl(): LoadCtl {
  return { seq: 0, controller: null };
}

type Setter<T> = (partial: Partial<T>) => void;

/**
 * Run one load generation: abandon the previous request, reset state
 * synchronously, apply the result only if still current. Aborts and
 * superseded responses are silently dropped (not errors).
 */
async function runLoad<T extends object>(
  ctl: LoadCtl,
  set: Setter<T>,
  reset: Partial<T>,
  work: (signal: AbortSignal) => Promise<Partial<T>>,
  onError: (message: string, err: unknown) => Partial<T>,
): Promise<void> {
  ctl.controller?.abort();
  const controller = new AbortController();
  ctl.controller = controller;
  const seq = ++ctl.seq;
  const alive = () => ctl.seq === seq && !controller.signal.aborted;
  set({ ...reset });
  try {
    const patch = await work(controller.signal);
    if (alive()) set({ ...patch });
  } catch (err) {
    if (isAbortError(err) || !alive()) return;
    set({ ...onError(errorMessage(err), err) });
  } finally {
    if (ctl.controller === controller) ctl.controller = null;
  }
}

// ---------------------------------------------------------------------------
// Projects list
// ---------------------------------------------------------------------------

interface LiveProjectsState {
  status: LiveLoadStatus;
  businessId: string | null;
  projects: Project[];
  total: number;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  reset: () => void;
}

const projectsCtl = createCtl();

const idleProjects = {
  status: "idle" as const,
  businessId: null,
  projects: [],
  total: 0,
  error: null,
};

export const useLiveProjects = create<LiveProjectsState>((set) => ({
  ...idleProjects,
  load: (businessId) =>
    runLoad(
      projectsCtl,
      set,
      { status: "loading", businessId, projects: [], total: 0, error: null },
      async (signal) => {
        const page = await apiListProjects(businessId, { limit: 200 }, { signal });
        return {
          status: "ready" as const,
          projects: page.items.map(toDomainProject),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    projectsCtl.controller?.abort();
    projectsCtl.seq += 1;
    set({ ...idleProjects });
  },
}));

// ---------------------------------------------------------------------------
// Single project (deep links resolve exactly, never fall back to [0])
// ---------------------------------------------------------------------------

interface LiveProjectState {
  status: LiveLoadStatus;
  key: string | null;
  project: Project | null;
  /** True when the server answered 404: unknown id, not a failure. */
  notFound: boolean;
  error: string | null;
  load: (businessId: string, projectId: string) => Promise<void>;
  reset: () => void;
}

const projectCtl = createCtl();

const idleProject = {
  status: "idle" as const,
  key: null,
  project: null,
  notFound: false,
  error: null,
};

export const useLiveProject = create<LiveProjectState>((set) => ({
  ...idleProject,
  load: (businessId, projectId) =>
    runLoad(
      projectCtl,
      set,
      {
        status: "loading",
        key: `${businessId}/${projectId}`,
        project: null,
        notFound: false,
        error: null,
      },
      async (signal) => {
        const raw = await apiGetProject(businessId, projectId, { signal });
        return { status: "ready" as const, project: toDomainProject(raw), error: null };
      },
      (message, err) => ({
        status: "error" as const,
        error: message,
        notFound: err instanceof ApiError && err.status === 404,
      }),
    ),
  reset: () => {
    projectCtl.controller?.abort();
    projectCtl.seq += 1;
    set({ ...idleProject });
  },
}));

// ---------------------------------------------------------------------------
// Tasks (one project, or aggregated across the business for the Tasks page)
// ---------------------------------------------------------------------------

interface LiveTasksState {
  status: LiveLoadStatus;
  key: string | null;
  tasks: Task[];
  error: string | null;
  load: (businessId: string, projectId: string) => Promise<void>;
  loadAll: (businessId: string, projectIds: string[]) => Promise<void>;
  /**
   * Replace one row by id (ids are global PKs, so scope cannot collide).
   * No fetch, no scope change — used to apply execution outcomes.
   */
  upsertTask: (task: Task) => void;
  reset: () => void;
}

const tasksCtl = createCtl();

const idleTasks = { status: "idle" as const, key: null, tasks: [], error: null };

export const useLiveTasks = create<LiveTasksState>((set) => ({
  ...idleTasks,
  load: (businessId, projectId) =>
    runLoad(
      tasksCtl,
      set,
      { status: "loading", key: `${businessId}/${projectId}`, tasks: [], error: null },
      async (signal) => {
        const page = await apiListTasks(businessId, projectId, { limit: 200 }, { signal });
        return { status: "ready" as const, tasks: page.items.map(toDomainTask), error: null };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  loadAll: (businessId, projectIds) =>
    runLoad(
      tasksCtl,
      set,
      { status: "loading", key: `${businessId}/*`, tasks: [], error: null },
      async (signal) => {
        const pages = await Promise.all(
          projectIds.map((projectId) =>
            apiListTasks(businessId, projectId, { limit: 200 }, { signal }),
          ),
        );
        return {
          status: "ready" as const,
          tasks: pages.flatMap((page) => page.items.map(toDomainTask)),
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    tasksCtl.controller?.abort();
    tasksCtl.seq += 1;
    set({ ...idleTasks });
  },
  /**
   * Replace one row by id, or append it (ids are global PKs, so scope cannot
   * collide). The reconciled row was just verified server-side in scope, so
   * appending is safe — the UI shows fresh state even if the list had not
   * loaded this row yet.
   */
  upsertTask: (task: Task) =>
    set((s) => ({
      tasks: s.tasks.some((t) => t.id === task.id)
        ? s.tasks.map((t) => (t.id === task.id ? task : t))
        : [...s.tasks, task].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)),
    })),
}));

// ---------------------------------------------------------------------------
// Agents display cache (names/roles inside project & task screens only)
// ---------------------------------------------------------------------------

interface LiveAgentsState {
  status: LiveLoadStatus;
  businessId: string | null;
  agents: Agent[];
  error: string | null;
  ensure: (businessId: string) => Promise<void>;
  reset: () => void;
}

const agentsCtl = createCtl();

const idleAgents = {
  status: "idle" as const,
  businessId: null,
  agents: [],
  error: null,
};

export const useLiveAgents = create<LiveAgentsState>((set, get) => ({
  ...idleAgents,
  ensure: (businessId) => {
    const s = get();
    if (s.status === "ready" && s.businessId === businessId) return Promise.resolve();
    return runLoad(
      agentsCtl,
      set,
      { status: "loading", businessId, agents: [], error: null },
      async (signal) => {
        const page = await apiListAgents(businessId, { limit: 200 }, { signal });
        return {
          status: "ready" as const,
          agents: page.items.map(toDomainAgent),
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    );
  },
  reset: () => {
    agentsCtl.controller?.abort();
    agentsCtl.seq += 1;
    set({ ...idleAgents });
  },
}));

// ---------------------------------------------------------------------------
// Approvals list (read-only: decisions stay DEMO until a later stage)
// ---------------------------------------------------------------------------

interface LiveApprovalsState {
  status: LiveLoadStatus;
  businessId: string | null;
  approvals: Approval[];
  total: number;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  /**
   * Apply a server-confirmed decision to the cached row (same id = same row).
   * Never invents rows and never guesses statuses — callers pass back exactly
   * what the backend answered.
   */
  applyDecision: (approvalId: string, status: Approval["status"]) => void;
  reset: () => void;
}

const approvalsCtl = createCtl();

const idleApprovals = {
  status: "idle" as const,
  businessId: null,
  approvals: [],
  total: 0,
  error: null,
};

export const useLiveApprovals = create<LiveApprovalsState>((set) => ({
  ...idleApprovals,
  load: (businessId) =>
    runLoad(
      approvalsCtl,
      set,
      { status: "loading", businessId, approvals: [], total: 0, error: null },
      async (signal) => {
        const page = await apiListApprovals(businessId, { limit: 200 }, { signal });
        return {
          status: "ready" as const,
          approvals: page.items.map(toDomainApproval),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    approvalsCtl.controller?.abort();
    approvalsCtl.seq += 1;
    set({ ...idleApprovals });
  },
  applyDecision: (approvalId, status) =>
    set((s) => ({
      approvals: s.approvals.map((a) => (a.id === approvalId ? { ...a, status } : a)),
    })),
}));

// ---------------------------------------------------------------------------
// Activity feed (read-only, server order: newest first)
// ---------------------------------------------------------------------------

export interface LiveActivityScope {
  businessId: string;
  projectId?: string;
}

interface LiveActivityState {
  status: LiveLoadStatus;
  businessId: string | null;
  projectId: string | null;
  events: ActivityEvent[];
  total: number;
  error: string | null;
  load: (scope: LiveActivityScope) => Promise<void>;
  reset: () => void;
}

const activityCtl = createCtl();

const idleActivity = {
  status: "idle" as const,
  businessId: null,
  projectId: null,
  events: [],
  total: 0,
  error: null,
};

export const useLiveActivity = create<LiveActivityState>((set) => ({
  ...idleActivity,
  load: (scope) =>
    runLoad(
      activityCtl,
      set,
      {
        status: "loading",
        businessId: scope.businessId,
        projectId: scope.projectId ?? null,
        events: [],
        total: 0,
        error: null,
      },
      async (signal) => {
        const page = await apiListActivity(
          scope.businessId,
          { projectId: scope.projectId, limit: 200 },
          { signal },
        );
        return {
          status: "ready" as const,
          events: page.items.map(toDomainActivityEvent),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    activityCtl.controller?.abort();
    activityCtl.seq += 1;
    set({ ...idleActivity });
  },
}));

// ---------------------------------------------------------------------------
// Agents list (Stage 7.8; the ensure() display cache above stays untouched)
// ---------------------------------------------------------------------------

interface LiveAgentsListState {
  status: LiveLoadStatus;
  businessId: string | null;
  agents: Agent[];
  total: number;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  reset: () => void;
}

const agentsListCtl = createCtl();

const idleAgentsList = {
  status: "idle" as const,
  businessId: null,
  agents: [],
  total: 0,
  error: null,
};

export const useLiveAgentsList = create<LiveAgentsListState>((set) => ({
  ...idleAgentsList,
  load: (businessId) =>
    runLoad(
      agentsListCtl,
      set,
      { status: "loading", businessId, agents: [], total: 0, error: null },
      async (signal) => {
        const page = await apiListAgents(businessId, { limit: 200 }, { signal });
        return {
          status: "ready" as const,
          agents: page.items.map(toDomainAgent),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    agentsListCtl.controller?.abort();
    agentsListCtl.seq += 1;
    set({ ...idleAgentsList });
  },
}));

// ---------------------------------------------------------------------------
// Single agent (deep links resolve exactly, never fall back to [0])
// ---------------------------------------------------------------------------

interface LiveAgentState {
  status: LiveLoadStatus;
  key: string | null;
  agent: Agent | null;
  /** True when the server answered 404: unknown id, not a failure. */
  notFound: boolean;
  error: string | null;
  load: (businessId: string, agentId: string) => Promise<void>;
  reset: () => void;
}

const agentCtl = createCtl();

const idleAgent = {
  status: "idle" as const,
  key: null,
  agent: null,
  notFound: false,
  error: null,
};

export const useLiveAgent = create<LiveAgentState>((set) => ({
  ...idleAgent,
  load: (businessId, agentId) =>
    runLoad(
      agentCtl,
      set,
      {
        status: "loading",
        key: `${businessId}/${agentId}`,
        agent: null,
        notFound: false,
        error: null,
      },
      async (signal) => {
        const raw = await apiGetAgent(businessId, agentId, { signal });
        return { status: "ready" as const, agent: toDomainAgent(raw), error: null };
      },
      (message, err) => ({
        status: "error" as const,
        error: message,
        notFound: err instanceof ApiError && err.status === 404,
      }),
    ),
  reset: () => {
    agentCtl.controller?.abort();
    agentCtl.seq += 1;
    set({ ...idleAgent });
  },
}));

// ---------------------------------------------------------------------------
// Brain entries (read-only; category filter passes straight to the API)
// ---------------------------------------------------------------------------

export interface LiveBrainScope {
  businessId: string;
  category?: BrainCategory;
}

interface LiveBrainState {
  status: LiveLoadStatus;
  businessId: string | null;
  category: BrainCategory | null;
  entries: BrainEntry[];
  total: number;
  error: string | null;
  load: (scope: LiveBrainScope) => Promise<void>;
  reset: () => void;
}

const brainCtl = createCtl();

const idleBrain = {
  status: "idle" as const,
  businessId: null,
  category: null,
  entries: [],
  total: 0,
  error: null,
};

export const useLiveBrain = create<LiveBrainState>((set) => ({
  ...idleBrain,
  load: (scope) =>
    runLoad(
      brainCtl,
      set,
      {
        status: "loading",
        businessId: scope.businessId,
        category: scope.category ?? null,
        entries: [],
        total: 0,
        error: null,
      },
      async (signal) => {
        const page = await apiListBrain(
          scope.businessId,
          { category: scope.category, limit: 200 },
          { signal },
        );
        return {
          status: "ready" as const,
          entries: page.items.map(toDomainBrainEntry),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    brainCtl.controller?.abort();
    brainCtl.seq += 1;
    set({ ...idleBrain });
  },
}));
