"use client";

/**
 * MUSE SPARK — orchestration loop manager (client).
 *
 * Owns the ONE interval that drives simulated execution. All progression
 * flows through `stepScope` in `core.ts`; this module only schedules ticks,
 * applies results to stores, and enforces lifecycle + isolation.
 */

import { useSyncExternalStore } from "react";
import type { Agent, Approval, ApprovalStatus, Project, Task } from "@/lib/domain/types";
import {
  applyDecision,
  createSession,
  failTask,
  retryTask,
  stepScope,
  type EngineSession,
  type Scope,
  type StepOptions,
} from "@/lib/sim/core";
import { useActivity, useAgents, useApprovals, useBrain, useProjects } from "@/lib/store/app";

export const TICK_MS = 2000;

interface Snapshot {
  tasks: Task[];
  agents: Agent[];
  project: Project;
  approvals: Approval[];
}

let interval: ReturnType<typeof setInterval> | null = null;
let running = false;
let paused = false;
let tickCount = 0;
let activeScope: Scope | null = null;
let viewCache: EngineView | null = null;
const sessions = new Map<string, EngineSession>();
const snapshots = new Map<string, Snapshot>();
const listeners = new Set<() => void>();

const scopeKey = (s: Scope) => `${s.businessId}::${s.projectId}`;

export interface EngineView {
  running: boolean;
  paused: boolean;
  tickCount: number;
  scope: Scope | null;
}

export function getEngineView(): EngineView {
  if (!viewCache) {
    viewCache = { running, paused, tickCount, scope: activeScope };
  }
  return viewCache;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function emit() {
  viewCache = null; // invalidate cached snapshot before notifying
  listeners.forEach((l) => l());
}

export function useEngine(): EngineView {
  return useSyncExternalStore(subscribe, getEngineView, getEngineView);
}

function snapshotScope(s: Scope): void {
  const key = scopeKey(s);
  if (snapshots.has(key)) return;
  const { tasks, projects } = useProjects.getState();
  const { agents } = useAgents.getState();
  const { approvals } = useApprovals.getState();
  const project = projects.find((p) => p.id === s.projectId);
  if (!project) return;
  snapshots.set(key, {
    tasks: structuredClone(tasks.filter((t) => t.businessId === s.businessId && t.projectId === s.projectId)),
    agents: structuredClone(agents.filter((a) => a.businessId === s.businessId)),
    project: structuredClone(project),
    approvals: structuredClone(approvals.filter((a) => a.businessId === s.businessId && a.projectId === s.projectId)),
  });
}

/**
 * Advance one deterministic tick for a scope. Returns false when there is
 * no scope (or no matching project) — otherwise applies the step and true.
 */
export function tickOnce(s: Scope | null = activeScope, opts: StepOptions = {}): boolean {
  if (!s) return false;
  const ps = useProjects.getState();
  const project = ps.projects.find((p) => p.id === s.projectId && p.businessId === s.businessId);
  if (!project || project.status === "completed") {
    if (project?.status === "completed") return true; // terminal: nothing left to do
    return false;
  }
  snapshotScope(s);
  const key = scopeKey(s);
  let session = sessions.get(key);
  if (!session) {
    session = createSession();
    sessions.set(key, session);
  }

  const result = stepScope(
    {
      businessId: s.businessId,
      projectId: s.projectId,
      tasks: ps.tasks,
      agents: useAgents.getState().agents,
      project,
      approvals: useApprovals.getState().approvals,
    },
    session,
    opts,
  );

  ps.replaceTasks(result.tasks);
  useAgents.getState().replaceAgents(result.agents);
  ps.updateProject(project.id, { aiSpent: result.project.aiSpent, progress: result.project.progress, status: result.project.status });
  for (const e of result.events) useActivity.getState().log(e);
  for (const b of result.brainEntries) {
    const exists = useBrain.getState().entries.some((x) => x.id === b.id);
    if (!exists) useBrain.getState().addEntry(b);
  }

  tickCount += 1;
  emit();
  return true;
}

/** Start the single execution loop. Second call while running is a no-op returning false. */
export function startLoop(s: Scope, ms: number = TICK_MS): boolean {
  if (running) return false;
  snapshotScope(s);
  activeScope = s;
  running = true;
  paused = false;
  interval = setInterval(() => {
    tickOnce();
  }, ms);
  useActivity.getState().log({
    at: "just now",
    agentName: "Team Lead",
    text: "Execution started — independent tasks running in parallel.",
    kind: "system",
  });
  emit();
  return true;
}

export function pauseLoop(): boolean {
  if (!running || !interval) return false;
  clearInterval(interval);
  interval = null;
  running = false;
  paused = true;
  useActivity.getState().log({ at: "just now", agentName: "Team Lead", text: "Execution paused by you.", kind: "system" });
  emit();
  return true;
}

export function resumeLoop(ms: number = TICK_MS): boolean {
  if (running || !paused || !activeScope) return false;
  running = true;
  paused = false;
  const s = activeScope;
  interval = setInterval(() => {
    tickOnce(s);
  }, ms);
  useActivity.getState().log({ at: "just now", agentName: "Team Lead", text: "Execution resumed.", kind: "system" });
  emit();
  return true;
}

export function stopLoop(): boolean {
  if (!running && !paused) return false;
  if (interval) clearInterval(interval);
  interval = null;
  running = false;
  paused = false;
  useActivity.getState().log({ at: "just now", agentName: "Team Lead", text: "Execution stopped.", kind: "system" });
  emit();
  return true;
}

/** Restore the scope to its pre-execution snapshot. */
export function resetScope(s: Scope | null = activeScope): boolean {
  if (!s) return false;
  stopLoop();
  const key = scopeKey(s);
  const snap = snapshots.get(key);
  if (!snap) return false;
  const ps = useProjects.getState();
  const otherTasks = ps.tasks.filter((t) => !(t.businessId === s.businessId && t.projectId === s.projectId));
  ps.replaceTasks([...otherTasks, ...structuredClone(snap.tasks)]);
  const ag = useAgents.getState();
  const otherAgents = ag.agents.filter((a) => a.businessId !== s.businessId);
  ag.replaceAgents([...otherAgents, ...structuredClone(snap.agents)]);
  ps.updateProject(s.projectId, {
    aiSpent: snap.project.aiSpent,
    progress: snap.project.progress,
    status: snap.project.status,
  });
  const ap = useApprovals.getState();
  const otherApprovals = ap.approvals.filter((a) => !(a.businessId === s.businessId && a.projectId === s.projectId));
  ap.replaceApprovals([...otherApprovals, ...structuredClone(snap.approvals)]);
  sessions.delete(key);
  tickCount = 0;
  useActivity.getState().log({ at: "just now", agentName: "Team Lead", text: "Plan reset to its initial state.", kind: "system" });
  emit();
  return true;
}

/** Route an approval decision into the gated task. Call AFTER the approvals store records the decision. */
export function applyApprovalToExecution(approvalId: string, decision: ApprovalStatus): void {
  const ap = useApprovals.getState();
  const ps = useProjects.getState();
  const { tasks, events } = applyDecision(ps.tasks, ap.approvals, approvalId, decision);
  ps.replaceTasks(tasks);
  for (const e of events) useActivity.getState().log(e);
  emit();
}

export function failTaskInExecution(taskId: string, reason: string): void {
  const ps = useProjects.getState();
  const { tasks, events } = failTask(ps.tasks, taskId, reason);
  ps.replaceTasks(tasks);
  for (const e of events) useActivity.getState().log(e);
  emit();
}

export function retryTaskInExecution(taskId: string): void {
  const ps = useProjects.getState();
  const { tasks, events } = retryTask(ps.tasks, taskId);
  ps.replaceTasks(tasks);
  for (const e of events) useActivity.getState().log(e);
  emit();
}

/** Test-only: clear loop + sessions without touching stores. */
export function __resetEngineForTests(): void {
  if (interval) clearInterval(interval);
  interval = null;
  running = false;
  paused = false;
  tickCount = 0;
  activeScope = null;
  sessions.clear();
  snapshots.clear();
}
