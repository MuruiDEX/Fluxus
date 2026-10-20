"use client";

/**
 * LIVE management-write runner (Stage 7.2) — drives the existing backend
 * `POST .../brain`, `PATCH .../projects/{id}` and
 * `PATCH .../projects/{id}/tasks/{task_id}` endpoints (Stage 7.1).
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - DEMO ban: writes only with an authenticated session whose membership
 *   list contains the target business. Anything else records an error and
 *   sends nothing.
 * - Single flight per record: concurrent save() calls (double click,
 *   StrictMode, remount) share one promise — exactly one request per key.
 * - No optimistic updates: caches change only after the server answers 2xx,
 *   applying exactly the returned row (mapped through the shared mappers).
 *   Patches send only caller-supplied allowlist fields; executor-owned and
 *   derived fields have no representation in the input types by construction.
 * - Honest errors: 401 flows through the shared session handler (the caller
 *   falls back to DEMO) and still resolves the record as error — forms never
 *   hang in "saving". 403/404/409/422 surface the server message with retry;
 *   network loss (status 0) is reported as unknown-outcome, never success.
 * - Sync: patched rows replace their LIVE cache entries; a created Brain
 *   entry reloads the list in its current scope (server truth, never an
 *   invented row). Stale generations are dropped by sequence guard.
 */

import { create } from "zustand";
import {
  ApiError,
  apiActivateAgent,
  apiCreateBrain,
  apiDisableAgent,
  apiHireAgent,
  apiInstallPack,
  apiPatchProject,
  apiPatchTask,
  isAbortError,
  type ApiPackInstall,
  type BrainCreateInput,
  type ProjectPatchInput,
  type TaskPatchInput,
} from "@/lib/api";
import { toDomainAgent, toDomainProject, toDomainTask } from "@/lib/live/mappers";
import {
  useLiveAgent,
  useLiveAgents,
  useLiveAgentsList,
  useLiveBrain,
  useLiveMarketplace,
  useLivePackOne,
  useLivePacks,
  useLiveProject,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";
import { useSession } from "@/lib/store/auth";

export type WritePhase = "idle" | "saving" | "saved" | "error";

export interface WriteRecord {
  phase: WritePhase;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface WritesState {
  projects: Record<string, WriteRecord>;
  tasks: Record<string, WriteRecord>;
  brains: Record<string, WriteRecord>;
  hires: Record<string, WriteRecord>;
  agents: Record<string, WriteRecord>;
  patchProject: (
    businessId: string,
    projectId: string,
    patch: ProjectPatchInput,
  ) => Promise<void>;
  patchTask: (
    businessId: string,
    projectId: string,
    taskId: string,
    patch: TaskPatchInput,
  ) => Promise<void>;
  createBrain: (businessId: string, input: BrainCreateInput) => Promise<void>;
  hireAgent: (businessId: string, slug: string) => Promise<void>;
  installPack: (businessId: string, slug: string) => Promise<void>;
  activateAgent: (businessId: string, agentId: string) => Promise<void>;
  disableAgent: (businessId: string, agentId: string) => Promise<void>;
  clearProject: (businessId: string, projectId: string) => void;
  clearTask: (businessId: string, projectId: string, taskId: string) => void;
  clearBrain: (businessId: string) => void;
  clearHire: (businessId: string, slug: string) => void;
  clearPack: (businessId: string, slug: string) => void;
  clearAgent: (businessId: string, agentId: string) => void;
}

const idleRecord: WriteRecord = {
  phase: "idle",
  message: null,
  errorCode: null,
  updatedAt: 0,
};

function projectKey(businessId: string, projectId: string): string {
  return `${businessId}/${projectId}`;
}

function hireKey(businessId: string, slug: string): string {
  return `${businessId}/${slug}`;
}

function agentKey(businessId: string, agentId: string): string {
  return `${businessId}/${agentId}`;
}

function taskKey(businessId: string, projectId: string, taskId: string): string {
  return `${businessId}/${projectId}/${taskId}`;
}

const flights: Record<string, Promise<void> | undefined> = {};
const seqs: Record<string, number> = {};

function setRecord(
  slice: "projects" | "tasks" | "brains" | "hires" | "agents",
  key: string,
  patch: Partial<WriteRecord>,
): void {
  useLiveWrites.setState((s) => ({
    [slice]: {
      ...s[slice],
      [key]: { ...s[slice][key], ...idleRecord, ...patch, updatedAt: Date.now() },
    },
  }));
}

function clearRecord(slice: "projects" | "tasks" | "brains" | "hires" | "agents", key: string): void {
  seqs[`writes/${slice}/${key}`] = (seqs[`writes/${slice}/${key}`] ?? 0) + 1;
  useLiveWrites.setState((s) => {
    if (!(key in s[slice])) return s;
    const rest = { ...s[slice] };
    delete rest[key];
    return { [slice]: rest };
  });
}

/** Gate 1 for every mutation: proven session + membership (DEMO ban). */
function gateError(businessId: string): { message: string; errorCode: string } | null {
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    return { message: "Sign in to save changes.", errorCode: "unauthorized" };
  }
  if (!session.businesses.includes(businessId)) {
    return { message: "No access to this business.", errorCode: "forbidden" };
  }
  return null;
}

function finishError(
  slice: "projects" | "tasks" | "brains" | "hires" | "agents",
  key: string,
  seq: number,
  err: unknown,
): void {
  const alive = () => seqs[`writes/${slice}/${key}`] === seq;
  if (!alive() || isAbortError(err)) return;
  if (!(err instanceof ApiError)) {
    setRecord(slice, key, {
      phase: "error",
      message: "Unexpected client error.",
      errorCode: null,
    });
    return;
  }
  if (err.status === 0) {
    setRecord(slice, key, {
      phase: "error",
      message: "Lost contact with the server — nothing was saved. Retry to reconcile.",
      errorCode: err.code,
    });
    return;
  }
  // 401 already cleared CSRF and notified the session layer (DEMO fallback);
  // the record still resolves as error so the form leaves "saving".
  setRecord(slice, key, { phase: "error", message: err.message, errorCode: err.code });
}

function runWrite(
  slice: "projects" | "tasks" | "brains" | "hires" | "agents",
  key: string,
  businessId: string,
  work: (seq: number) => Promise<void>,
): Promise<void> {
  const flightKey = `writes/${slice}/${key}`;
  const flight = flights[flightKey];
  if (flight) return flight;
  const seq = (seqs[flightKey] ?? 0) + 1;
  seqs[flightKey] = seq;
  const promise = work(seq).finally(() => {
    if (flights[flightKey] === promise) delete flights[flightKey];
  });
  flights[flightKey] = promise;
  return promise;
}

async function patchProjectWork(
  key: string,
  businessId: string,
  projectId: string,
  patch: ProjectPatchInput,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/projects/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("projects", key, { phase: "error", ...gated });
    return;
  }
  setRecord("projects", key, { phase: "saving", message: null, errorCode: null });
  let project;
  try {
    project = toDomainProject(await apiPatchProject(businessId, projectId, patch));
  } catch (err) {
    finishError("projects", key, seq, err);
    return;
  }
  if (!alive()) return;
  // Server truth only: replace the cached rows with the answered row.
  useLiveProject.getState().replace(project);
  useLiveProjects.getState().upsertProject(project);
  if (!alive()) return;
  setRecord("projects", key, {
    phase: "saved",
    message: "Saved — server confirmed.",
    errorCode: null,
  });
}

async function patchTaskWork(
  key: string,
  businessId: string,
  projectId: string,
  taskId: string,
  patch: TaskPatchInput,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/tasks/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("tasks", key, { phase: "error", ...gated });
    return;
  }
  setRecord("tasks", key, { phase: "saving", message: null, errorCode: null });
  let task;
  try {
    task = toDomainTask(await apiPatchTask(businessId, projectId, taskId, patch));
  } catch (err) {
    finishError("tasks", key, seq, err);
    return;
  }
  if (!alive()) return;
  useLiveTasks.getState().upsertTask(task);
  if (!alive()) return;
  setRecord("tasks", key, {
    phase: "saved",
    message: "Saved — server confirmed.",
    errorCode: null,
  });
}

async function createBrainWork(
  key: string,
  businessId: string,
  input: BrainCreateInput,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/brains/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("brains", key, { phase: "error", ...gated });
    return;
  }
  setRecord("brains", key, { phase: "saving", message: null, errorCode: null });
  let createdId: string;
  try {
    createdId = (await apiCreateBrain(businessId, input)).id;
  } catch (err) {
    finishError("brains", key, seq, err);
    return;
  }
  if (!alive()) return;
  // Reload the list in its current scope: the new row arrives as server
  // truth (never fabricated), and the total updates with it.
  const brain = useLiveBrain.getState();
  try {
    await brain.load({ businessId, category: brain.category ?? undefined });
  } catch {
    // Best effort: the entry exists server-side (201); the message below
    // says so and the next load will show it.
  }
  if (!alive()) return;
  setRecord("brains", key, {
    phase: "saved",
    message: `Saved to Business Brain as ${createdId}.`,
    errorCode: null,
  });
}

/** Reload team + catalog only when already open for this business. */
async function refreshTeamIfLoaded(businessId: string): Promise<void> {
  const agentsList = useLiveAgentsList.getState();
  if (agentsList.businessId === businessId && agentsList.status !== "idle") {
    await agentsList.load(businessId);
  }
  useLiveAgents.getState().invalidate(businessId);
  const catalog = useLiveMarketplace.getState();
  if (catalog.businessId === businessId && catalog.status !== "idle") {
    await catalog.load(businessId);
  }
}

async function hireAgentWork(
  key: string,
  businessId: string,
  slug: string,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/hires/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("hires", key, { phase: "error", ...gated });
    return;
  }
  setRecord("hires", key, { phase: "saving", message: null, errorCode: null });
  let agentName: string;
  try {
    agentName = toDomainAgent(await apiHireAgent(businessId, slug)).name;
  } catch (err) {
    if (alive() && err instanceof ApiError && err.status === 409 && err.code === "already_hired") {
      // Someone hired meanwhile: reload so the UI shows Hired, then report.
      try {
        await refreshTeamIfLoaded(businessId);
      } catch {
        // Best effort: the message below still describes the conflict.
      }
      if (!alive()) return;
      setRecord("hires", key, {
        phase: "error",
        message: "Already on your team — list refreshed.",
        errorCode: "already_hired",
      });
      return;
    }
    finishError("hires", key, seq, err);
    return;
  }
  if (!alive()) return;
  // Awaited: hireAgent() resolves only after the team + catalog reflect the
  // confirmed hire, so the UI never shows a Hire button for a hired listing.
  await refreshTeamIfLoaded(businessId);
  if (!alive()) return;
  setRecord("hires", key, {
    phase: "saved",
    message: `${agentName} joined your team.`,
    errorCode: null,
  });
}

/**
 * Install one pack (Stage 15). Server decides installed vs already_installed;
 * the client only reflects the confirmed answer. On success the team, the
 * marketplace catalog, the packs list/detail and (when loaded) projects are
 * refreshed so every surface agrees without an optimistic write.
 */
async function installPackWork(
  key: string,
  businessId: string,
  slug: string,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/hires/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("hires", key, { phase: "error", ...gated });
    return;
  }
  setRecord("hires", key, { phase: "saving", message: null, errorCode: null });
  let result: ApiPackInstall;
  try {
    result = await apiInstallPack(businessId, slug);
  } catch (err) {
    finishError("hires", key, seq, err);
    return;
  }
  if (!alive()) return;

  await refreshTeamIfLoaded(businessId);

  const packs = useLivePacks.getState();
  if (packs.businessId === businessId && packs.status !== "idle") {
    try {
      await packs.load(businessId);
    } catch {
      // Best effort: the receipt exists server-side; the message below says so.
    }
  }
  const packOne = useLivePackOne.getState();
  if (packOne.key === `${businessId}/${slug}` && packOne.status !== "idle") {
    try {
      await packOne.load(businessId, slug);
    } catch {
      // Best effort as above.
    }
  }
  if (result.project) {
    const project = toDomainProject(result.project);
    useLiveProject.getState().replace(project);
    const projects = useLiveProjects.getState();
    if (projects.businessId === businessId && projects.status !== "idle") {
      try {
        await projects.load(businessId);
      } catch {
        // Best effort as above.
      }
    }
  }
  if (!alive()) return;
  setRecord("hires", key, {
    phase: "saved",
    message:
      result.outcome === "already_installed"
        ? `${result.pack.name} is already installed.`
        : `${result.pack.name} installed — ${result.agents.length} agents and a workflow are ready.`,
    errorCode: null,
  });
}

async function toggleAgentWork(
  key: string,
  businessId: string,
  agentId: string,
  hired: boolean,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`writes/agents/${key}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord("agents", key, { phase: "error", ...gated });
    return;
  }
  setRecord("agents", key, { phase: "saving", message: null, errorCode: null });
  let agent;
  try {
    const raw = hired
      ? await apiActivateAgent(businessId, agentId)
      : await apiDisableAgent(businessId, agentId);
    agent = toDomainAgent(raw);
  } catch (err) {
    finishError("agents", key, seq, err);
    return;
  }
  if (!alive()) return;
  // Server truth only: replace the cached rows with the answered row, so
  // the list, the detail view and every owner picker agree instantly. The
  // display cache (task cards, owner pickers) is invalidated so the next
  // ensure() refetches instead of serving the pre-toggle row.
  useLiveAgentsList.getState().updateAgent(agent);
  useLiveAgent.getState().replace(agent);
  useLiveAgents.getState().invalidate(businessId);
  if (!alive()) return;
  setRecord("agents", key, {
    phase: "saved",
    message: hired ? `${agent.name} is active.` : `${agent.name} is disabled.`,
    errorCode: null,
  });
}

export const useLiveWrites = create<WritesState>(() => ({
  projects: {},
  tasks: {},
  brains: {},
  hires: {},
  agents: {},
  patchProject: (businessId, projectId, patch) =>
    runWrite("projects", projectKey(businessId, projectId), businessId, (seq) =>
      patchProjectWork(projectKey(businessId, projectId), businessId, projectId, patch, seq),
    ),
  patchTask: (businessId, projectId, taskId, patch) =>
    runWrite("tasks", taskKey(businessId, projectId, taskId), businessId, (seq) =>
      patchTaskWork(
        taskKey(businessId, projectId, taskId),
        businessId,
        projectId,
        taskId,
        patch,
        seq,
      ),
    ),
  createBrain: (businessId, input) =>
    runWrite("brains", businessId, businessId, (seq) =>
      createBrainWork(businessId, businessId, input, seq),
    ),
  hireAgent: (businessId, slug) =>
    runWrite("hires", hireKey(businessId, slug), businessId, (seq) =>
      hireAgentWork(hireKey(businessId, slug), businessId, slug, seq),
    ),
  installPack: (businessId, slug) =>
    runWrite("hires", hireKey(businessId, slug), businessId, (seq) =>
      installPackWork(hireKey(businessId, slug), businessId, slug, seq),
    ),
  activateAgent: (businessId, agentId) =>
    runWrite("agents", agentKey(businessId, agentId), businessId, (seq) =>
      toggleAgentWork(agentKey(businessId, agentId), businessId, agentId, true, seq),
    ),
  disableAgent: (businessId, agentId) =>
    runWrite("agents", agentKey(businessId, agentId), businessId, (seq) =>
      toggleAgentWork(agentKey(businessId, agentId), businessId, agentId, false, seq),
    ),
  clearProject: (businessId, projectId) =>
    clearRecord("projects", projectKey(businessId, projectId)),
  clearTask: (businessId, projectId, taskId) =>
    clearRecord("tasks", taskKey(businessId, projectId, taskId)),
  clearBrain: (businessId) => clearRecord("brains", businessId),
  clearHire: (businessId, slug) => clearRecord("hires", hireKey(businessId, slug)),
  clearPack: (businessId, slug) => clearRecord("hires", hireKey(businessId, slug)),
  clearAgent: (businessId, agentId) => clearRecord("agents", agentKey(businessId, agentId)),
}));

