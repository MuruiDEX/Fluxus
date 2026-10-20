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
  apiGetBusiness,
  apiGetListing,
  apiGetProject,
  apiListAgents,
  apiListApprovals,
  apiListActivity,
  apiListBrain,
  apiListMarketplace,
  apiListPacks,
  apiListProjects,
  apiListTasks,
  apiListUsage,
  apiPreviewPack,
  isAbortError,
  type BrainCategory,
} from "@/lib/api";
import type { SessionStatus } from "@/lib/store/auth";
import {
  toDomainActivityEvent,
  toDomainAgent,
  toDomainApproval,
  toDomainBrainEntry,
  toDomainBusiness,
  toDomainMarketplaceEntry,
  toDomainPack,
  toDomainPackPreview,
  toDomainProject,
  toDomainTask,
  toDomainUsage,
} from "@/lib/live/mappers";
import type { ActivityEvent, Agent, Approval, BrainEntry, Business, MarketplaceAgent, Pack, PackPreview, Project, Task, UsageView } from "@/lib/domain/types";

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
// Business profile and metrics
// ---------------------------------------------------------------------------

interface LiveBusinessState {
  status: LiveLoadStatus;
  businessId: string | null;
  business: Business | null;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  reset: () => void;
}

const businessCtl = createCtl();

const idleBusiness = {
  status: "idle" as const,
  businessId: null,
  business: null,
  error: null,
};

export const useLiveBusiness = create<LiveBusinessState>((set) => ({
  ...idleBusiness,
  load: (businessId) =>
    runLoad(
      businessCtl,
      set,
      { status: "loading", businessId, business: null, error: null },
      async (signal) => {
        const raw = await apiGetBusiness(businessId, { signal });
        return { status: "ready" as const, business: toDomainBusiness(raw), error: null };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    businessCtl.controller?.abort();
    businessCtl.seq += 1;
    set({ ...idleBusiness });
  },
}));

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
  /**
   * Replace one row by id, or append it (ids are global PKs, so scope cannot
   * collide). The row was just verified server-side in scope — used to apply
   * management-write answers without a full reload.
   */
  upsertProject: (project: Project) => void;
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
  upsertProject: (project: Project) =>
    set((s) => ({
      projects: s.projects.some((p) => p.id === project.id)
        ? s.projects.map((p) => (p.id === project.id ? project : p))
        : [...s.projects, project].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)),
    })),
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
  /**
   * Replace the cached row with a server-confirmed row (management-write
   * answer). Never invents rows and never guesses fields — callers pass back
   * exactly what the backend answered.
   */
  replace: (project: Project) => void;
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
  replace: (project: Project) =>
    set((s) => (s.project && s.project.id === project.id ? { project } : s)),
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
  /**
   * Drop the cache for one business (hire changed the team server-side).
   * Other businesses are untouched; the next ensure() refetches.
   */
  invalidate: (businessId: string) => void;
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
  invalidate: (businessId) => {
    const s = get();
    if (s.businessId !== businessId) return;
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
// Usage ledger (Stage 16) — append-only rows + derived money summary
// ---------------------------------------------------------------------------

export interface LiveUsageScope {
  businessId: string;
  projectId?: string;
}

const EMPTY_USAGE_SUMMARY: UsageView["summary"] = {
  confirmedCost: 0,
  estimatedCost: 0,
  unknownCount: 0,
  byCallKind: {},
  reservations: { active: 0, ambiguous: 0, consumed: 0, released: 0, writtenOff: 0 },
};

interface LiveUsageState {
  status: LiveLoadStatus;
  businessId: string | null;
  projectId: string | null;
  records: UsageView["records"];
  total: number;
  summary: UsageView["summary"];
  error: string | null;
  load: (scope: LiveUsageScope) => Promise<void>;
  reset: () => void;
}

const usageCtl = createCtl();

const idleUsage = {
  status: "idle" as const,
  businessId: null,
  projectId: null,
  records: [] as UsageView["records"],
  total: 0,
  summary: EMPTY_USAGE_SUMMARY,
  error: null,
};

export const useLiveUsage = create<LiveUsageState>((set) => ({
  ...idleUsage,
  load: (scope) =>
    runLoad(
      usageCtl,
      set,
      {
        status: "loading",
        businessId: scope.businessId,
        projectId: scope.projectId ?? null,
        records: [],
        total: 0,
        summary: EMPTY_USAGE_SUMMARY,
        error: null,
      },
      async (signal) => {
        const page = await apiListUsage(
          scope.businessId,
          { projectId: scope.projectId, limit: 200 },
          { signal },
        );
        const view = toDomainUsage(page);
        return {
          status: "ready" as const,
          records: view.records,
          total: view.total,
          summary: view.summary,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    usageCtl.controller?.abort();
    usageCtl.seq += 1;
    set({ ...idleUsage });
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
  /**
   * Apply a server-confirmed lifecycle change to the cached row (same id =
   * same row). Never invents rows and never guesses flags — callers pass
   * back exactly what the backend answered.
   */
  updateAgent: (agent: Agent) => void;
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
  updateAgent: (agent: Agent) =>
    set((s) => ({
      agents: s.agents.map((a) => (a.id === agent.id ? agent : a)),
    })),
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
  /**
   * Replace the cached row with a server-confirmed row (lifecycle answer).
   * Same contract as the list updater: no invented rows, no guessed flags.
   */
  replace: (agent: Agent) => void;
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
  replace: (agent: Agent) =>
    set((s) => (s.agent && s.agent.id === agent.id ? { agent } : s)),
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

// ---------------------------------------------------------------------------
// Marketplace catalog (Stage 8.1; global rows, hired computed per business)
// ---------------------------------------------------------------------------

interface LiveMarketplaceState {
  status: LiveLoadStatus;
  businessId: string | null;
  listings: MarketplaceAgent[];
  total: number;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  reset: () => void;
}

const marketplaceCtl = createCtl();

const idleMarketplace = {
  status: "idle" as const,
  businessId: null,
  listings: [],
  total: 0,
  error: null,
};

export const useLiveMarketplace = create<LiveMarketplaceState>((set) => ({
  ...idleMarketplace,
  load: (businessId) =>
    runLoad(
      marketplaceCtl,
      set,
      { status: "loading", businessId, listings: [], total: 0, error: null },
      async (signal) => {
        const page = await apiListMarketplace(businessId, { limit: 200 }, { signal });
        return {
          status: "ready" as const,
          listings: page.items.map(toDomainMarketplaceEntry),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    marketplaceCtl.controller?.abort();
    marketplaceCtl.seq += 1;
    set({ ...idleMarketplace });
  },
}));

// ---------------------------------------------------------------------------
// Single listing (deep links resolve exactly, never fall back to [0])
// ---------------------------------------------------------------------------

interface LiveMarketplaceOneState {
  status: LiveLoadStatus;
  key: string | null;
  listing: MarketplaceAgent | null;
  /** True when the server answered 404: unknown slug, not a failure. */
  notFound: boolean;
  error: string | null;
  load: (businessId: string, slug: string) => Promise<void>;
  reset: () => void;
}

const marketplaceOneCtl = createCtl();

const idleMarketplaceOne = {
  status: "idle" as const,
  key: null,
  listing: null,
  notFound: false,
  error: null,
};

export const useLiveMarketplaceOne = create<LiveMarketplaceOneState>((set) => ({
  ...idleMarketplaceOne,
  load: (businessId, slug) =>
    runLoad(
      marketplaceOneCtl,
      set,
      {
        status: "loading",
        key: `${businessId}/${slug}`,
        listing: null,
        notFound: false,
        error: null,
      },
      async (signal) => {
        const raw = await apiGetListing(businessId, slug, { signal });
        return { status: "ready" as const, listing: toDomainMarketplaceEntry(raw), error: null };
      },
      (message, err) => ({
        status: "error" as const,
        error: message,
        notFound: err instanceof ApiError && err.status === 404,
      }),
    ),
  reset: () => {
    marketplaceOneCtl.controller?.abort();
    marketplaceOneCtl.seq += 1;
    set({ ...idleMarketplaceOne });
  },
}));

// ---------------------------------------------------------------------------
// Packs (Stage 15) - catalog list + detail/preview with install blockers
// ---------------------------------------------------------------------------

interface LivePacksState {
  status: LiveLoadStatus;
  businessId: string | null;
  packs: Pack[];
  total: number;
  error: string | null;
  load: (businessId: string) => Promise<void>;
  reset: () => void;
}

const packsCtl = createCtl();

const idlePacks = {
  status: "idle" as const,
  businessId: null,
  packs: [] as Pack[],
  total: 0,
  error: null,
};

export const useLivePacks = create<LivePacksState>((set) => ({
  ...idlePacks,
  load: (businessId) =>
    runLoad(
      packsCtl,
      set,
      { status: "loading", businessId, packs: [], total: 0, error: null },
      async (signal) => {
        const page = await apiListPacks(businessId, { signal });
        return {
          status: "ready" as const,
          packs: page.items.map(toDomainPack),
          total: page.total,
          error: null,
        };
      },
      (message) => ({ status: "error" as const, error: message }),
    ),
  reset: () => {
    packsCtl.controller?.abort();
    packsCtl.seq += 1;
    set({ ...idlePacks });
  },
}));

interface LivePackOneState {
  status: LiveLoadStatus;
  key: string | null;
  preview: PackPreview | null;
  /** True when the server answered 404: unknown slug, not a failure. */
  notFound: boolean;
  error: string | null;
  load: (businessId: string, slug: string) => Promise<void>;
  reset: () => void;
}

const packOneCtl = createCtl();

const idlePackOne = {
  status: "idle" as const,
  key: null,
  preview: null,
  notFound: false,
  error: null,
};

export const useLivePackOne = create<LivePackOneState>((set) => ({
  ...idlePackOne,
  load: (businessId, slug) =>
    runLoad(
      packOneCtl,
      set,
      {
        status: "loading",
        key: `${businessId}/${slug}`,
        preview: null,
        notFound: false,
        error: null,
      },
      async (signal) => {
        const raw = await apiPreviewPack(businessId, slug, { signal });
        return {
          status: "ready" as const,
          preview: toDomainPackPreview(raw),
          error: null,
        };
      },
      (message, err) => ({
        status: "error" as const,
        error: message,
        notFound: err instanceof ApiError && err.status === 404,
      }),
    ),
  reset: () => {
    packOneCtl.controller?.abort();
    packOneCtl.seq += 1;
    set({ ...idlePackOne });
  },
}));
