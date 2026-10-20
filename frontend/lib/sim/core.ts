/**
 * MUSE SPARK — orchestration CORE (pure, deterministic, store-free).
 *
 * This is the single source of truth for simulated execution. The loop
 * manager (`engine.ts`) only reads state, calls `stepScope`, and writes the
 * result back. UI components must NEVER implement their own timers or mutate
 * task state directly.
 *
 * Determinism: no wall-clock, no Math.random. Progress and cost advance by
 * fixed steps, so tests drive behavior via repeated `stepScope` calls.
 */

import type {
  ActivityEvent,
  Agent,
  Approval,
  ApprovalStatus,
  BrainEntry,
  Project,
  Task,
} from "@/lib/domain/types";

export interface Scope {
  businessId: string;
  projectId: string;
}

export interface StepOptions {
  /** Progress points added per tick to each working task. */
  progressStep?: number;
  /** Cost added per tick to each progressing task. */
  costPerTick?: number;
}

/** Mutable per-scope session (review timing, one-shot notifications). */
export interface EngineSession {
  reviewPending: string[];
  blockedNotified: string[];
  startedNotified: string[];
}

export function createSession(): EngineSession {
  return { reviewPending: [], blockedNotified: [], startedNotified: [] };
}

export interface StepInput extends Scope {
  tasks: Task[];
  agents: Agent[];
  project: Project;
  approvals: Approval[];
}

export interface StepResult {
  tasks: Task[];
  agents: Agent[];
  project: Project;
  events: Omit<ActivityEvent, "id">[];
  brainEntries: BrainEntry[];
}

export const DEFAULT_PROGRESS_STEP = 7;
export const DEFAULT_COST_PER_TICK = 0.06;
/** Failed/rejected attempts before a task is escalated as failed. */
export const MAX_ATTEMPTS = 3;

const round2 = (n: number) => Math.round(n * 100) / 100;

function depsCompleted(task: Task, byId: Map<string, Task>): boolean {
  return task.dependsOn.every((depId) => byId.get(depId)?.status === "completed");
}

function gateOf(task: Task, approvals: Approval[]): Approval | undefined {
  if (!task.approvalGateId) return undefined;
  return approvals.find((a) => a.id === task.approvalGateId);
}

function agentName(agents: Agent[], id: string): string {
  return agents.find((a) => a.id === id)?.role ?? "AI employee";
}

function once(list: string[], id: string): boolean {
  if (list.includes(id)) return false;
  list.push(id);
  return true;
}

export function stepScope(input: StepInput, session: EngineSession, opts: StepOptions = {}): StepResult {
  const { businessId, projectId, approvals } = input;
  const step = opts.progressStep ?? DEFAULT_PROGRESS_STEP;
  const events: Omit<ActivityEvent, "id">[] = [];
  const brainEntries: BrainEntry[] = [];
  const startedThisTick = new Set<string>();

  const byId = new Map(input.tasks.map((t) => [t.id, t]));
  const scopedIds = new Set(
    input.tasks.filter((t) => t.businessId === businessId && t.projectId === projectId).map((t) => t.id),
  );

  const next = new Map<string, Task>();

  for (const task of input.tasks) {
    if (!scopedIds.has(task.id)) {
      next.set(task.id, task);
      continue;
    }

    switch (task.status) {
      case "completed":
      case "failed":
      case "waiting_approval": {
        next.set(task.id, task);
        break;
      }

      case "not_started":
      case "waiting_dependency":
      case "waiting_assets": {
        if (depsCompleted(task, byId)) {
          const wasBlocked = task.status === "waiting_dependency" || task.status === "waiting_assets";
          const progress = Math.min(100, task.progress + step);
          const rate = task.costRate ?? (opts.costPerTick ?? DEFAULT_COST_PER_TICK);
          next.set(task.id, {
            ...task,
            status: progress >= 100 ? "completed" : "working",
            progress,
            costAccrued: round2(task.costAccrued + (progress > task.progress ? rate : 0)),
          });
          startedThisTick.add(task.id);
          if (progress >= 100) session.reviewPending.push(task.id);
          if (wasBlocked) {
            events.push({
              at: "just now",
              agentName: agentName(input.agents, task.ownerAgentId),
              text: `Unblocked: ${task.title} — dependencies complete, running in parallel.`,
              kind: "work",
            });
          } else if (once(session.startedNotified, task.id)) {
            events.push({
              at: "just now",
              agentName: agentName(input.agents, task.ownerAgentId),
              text: `Started: ${task.title}.`,
              kind: "work",
            });
          }
        } else {
          // Blocked: normalize to waiting_dependency, notify once per session.
          if (task.status !== "waiting_dependency") {
            next.set(task.id, { ...task, status: "waiting_dependency" });
          } else {
            next.set(task.id, task);
          }
          if (once(session.blockedNotified, task.id)) {
            const waitingOn = task.dependsOn
              .map((d) => byId.get(d)?.title ?? d)
              .join(", ");
            events.push({
              at: "just now",
              agentName: agentName(input.agents, task.ownerAgentId),
              text: `Waiting: ${task.title} is blocked by ${waitingOn}.`,
              kind: "system",
            });
          }
        }
        break;
      }

      case "working": {
        const gate = gateOf(task, approvals);
        const gateAt = task.gateAt ?? 100;
        const gatePending = gate?.status === "pending";
        const advanced = Math.min(100, task.progress + step);
        const rate = task.costRate ?? (opts.costPerTick ?? DEFAULT_COST_PER_TICK);
        const accrues = advanced > task.progress ? rate : 0;

        if (gatePending && task.progress < gateAt && advanced >= gateAt) {
          // Pause exactly at the gate. No further cost accrues while gated.
          next.set(task.id, {
            ...task,
            status: "waiting_approval",
            progress: gateAt,
            costAccrued: round2(task.costAccrued + (gateAt > task.progress ? rate : 0)),
          });
          events.push({
            at: "just now",
            agentName: agentName(input.agents, task.ownerAgentId),
            text: `Paused at approval gate: ${task.title} needs “${gate?.title}”.`,
            kind: "approval",
          });
        } else if (advanced >= 100) {
          next.set(task.id, {
            ...task,
            status: "completed",
            progress: 100,
            costAccrued: round2(task.costAccrued + accrues),
          });
          session.reviewPending.push(task.id);
          events.push({
            at: "just now",
            agentName: agentName(input.agents, task.ownerAgentId),
            text: `Completed: ${task.title}.`,
            kind: "result",
            cost: round2(accrues),
          });
        } else {
          if (gate && !gatePending && task.progress < gateAt && advanced >= gateAt && once(session.startedNotified, `${task.id}:gate`)) {
            events.push({
              at: "just now",
              agentName: agentName(input.agents, task.ownerAgentId),
              text: `${task.title} passed pre-approved gate “${gate.title}”.`,
              kind: "work",
            });
          }
          next.set(task.id, {
            ...task,
            status: "working",
            progress: advanced,
            costAccrued: round2(task.costAccrued + accrues),
          });
        }
        break;
      }
    }
  }

  const tasks = input.tasks.map((t) => next.get(t.id) ?? t);

  // ---- Agent lifecycle derivation ---------------------------------------
  const agents: Agent[] = input.agents.map((agent) => {
    if (agent.businessId !== businessId || !agent.hired) return agent;
    const owned = tasks.filter(
      (t) => t.ownerAgentId === agent.id && t.projectId === projectId && t.businessId === businessId,
    );
    if (owned.length === 0) {
      return agent.status === "available" ? agent : { ...agent, status: "available" as const };
    }
    const has = (s: Task["status"]) => owned.some((t) => t.status === s);

    if (has("waiting_approval")) return { ...agent, status: "waiting_approval" as const, currentTask: owned.find((t) => t.status === "waiting_approval")?.title };
    if (has("working")) {
      const fresh = owned.find((t) => t.status === "working" && startedThisTick.has(t.id));
      if (fresh) return { ...agent, status: "thinking" as const, currentTask: `Planning ${fresh.title}` };
      const w = owned.find((t) => t.status === "working");
      return { ...agent, status: "working" as const, currentTask: w?.title };
    }
    if (has("failed")) {
      const failed = owned.filter((t) => t.status === "failed");
      const attempts = Math.max(...failed.map((t) => t.attempts ?? 0));
      if (attempts >= MAX_ATTEMPTS) return { ...agent, status: "escalated" as const, currentTask: `Escalated: ${failed[0].title}` };
      return { ...agent, status: "failed" as const, currentTask: `Failed: ${failed[0].title}` };
    }
    if (has("waiting_dependency") || has("not_started")) {
      const w = owned.find((t) => t.status === "waiting_dependency" || t.status === "not_started");
      return { ...agent, status: "waiting_dependency" as const, currentTask: w ? `Waiting: ${w.title}` : agent.currentTask };
    }
    const inReview = owned.find((t) => session.reviewPending.includes(t.id));
    if (inReview) {
      session.reviewPending = session.reviewPending.filter((id) => id !== inReview.id);
      return { ...agent, status: "reviewing" as const, currentTask: `Reviewing ${inReview.title}` };
    }
    if (owned.every((t) => t.status === "completed")) {
      return { ...agent, status: "completed" as const, currentTask: agent.currentTask };
    }
    return { ...agent, status: "available" as const };
  });

  // ---- Project totals (derived — single source, no drift) ----------------
  const scoped = tasks.filter((t) => t.businessId === businessId && t.projectId === projectId);
  const aiSpent = round2(scoped.reduce((sum, t) => sum + t.costAccrued, 0));
  const progress =
    scoped.length === 0 ? 0 : Math.round(scoped.reduce((sum, t) => sum + t.progress, 0) / scoped.length);

  let project: Project = { ...input.project, aiSpent, progress };

  // ---- Finalization: all work done → final report → Business Brain -------
  if (scoped.length > 0 && scoped.every((t) => t.status === "completed") && input.project.status !== "completed") {
    project = { ...project, status: "completed", progress: 100 };
    const ownerNames = [...new Set(scoped.map((t) => agentName(input.agents, t.ownerAgentId)))].join(", ");
    brainEntries.push({
      id: `brain_${projectId}_final`,
      businessId,
      category: "results",
      title: `Final report — ${input.project.title}`,
      body: `All ${scoped.length} tasks completed by ${ownerNames}. Total AI spend $${aiSpent.toFixed(2)}. Outcomes, costs and lessons recorded for future projects. (Simulated execution — demo data.)`,
      updatedLabel: "just now",
    });
    events.push({
      at: "just now",
      agentName: "Team Lead",
      text: `Final report ready for ${input.project.title} — results saved to Business Brain.`,
      kind: "result",
      cost: aiSpent,
    });
  }

  return { tasks, agents, project, events, brainEntries };
}

/** Approval decision effects on the gated task (approval status itself is owned by the approvals store). */
export function applyDecision(
  tasks: Task[],
  approvals: Approval[],
  approvalId: string,
  decision: ApprovalStatus,
): { tasks: Task[]; events: Omit<ActivityEvent, "id">[] } {
  const events: Omit<ActivityEvent, "id">[] = [];
  const approval = approvals.find((a) => a.id === approvalId);
  const task = tasks.find((t) => t.approvalGateId === approvalId);
  if (!approval || !task) return { tasks, events };

  if (decision === "approved") {
    if (task.status !== "waiting_approval") return { tasks, events }; // pre-approved: gate passes naturally
    const gateAt = task.gateAt ?? 100;
    const done = gateAt >= 100;
    events.push({
      at: "just now",
      agentName: "Team Lead",
      text: done
        ? `Approved “${approval.title}” — ${task.title} complete, dependents unblocked.`
        : `Approved “${approval.title}” — ${task.title} resumed.`,
      kind: "approval",
    });
    return {
      tasks: tasks.map((t) =>
        t.id === task.id
          ? { ...t, status: done ? ("completed" as const) : ("working" as const), progress: done ? 100 : t.progress }
          : t,
      ),
      events,
    };
  }

  // Rejected → revision (rollback) until attempts run out, then failure + escalation.
  const attempts = (task.attempts ?? 0) + 1;
  if (attempts >= MAX_ATTEMPTS) {
    events.push({
      at: "just now",
      agentName: "Team Lead",
      text: `“${approval.title}” rejected ${attempts}× — ${task.title} failed and escalated to you.`,
      kind: "approval",
    });
    return { tasks: tasks.map((t) => (t.id === task.id ? { ...t, status: "failed" as const, attempts } : t)), events };
  }
  const gateAt = task.gateAt ?? 100;
  events.push({
    at: "just now",
    agentName: "Team Lead",
    text: `“${approval.title}” rejected — ${task.title} sent back for revision (attempt ${attempts}).`,
    kind: "approval",
  });
  return {
    tasks: tasks.map((t) =>
      t.id === task.id ? { ...t, status: "working" as const, progress: Math.max(0, gateAt - 30), attempts } : t,
    ),
    events,
  };
}

/** Explicit failure injection (simulated tool/model failure). */
export function failTask(tasks: Task[], taskId: string, reason: string): { tasks: Task[]; events: Omit<ActivityEvent, "id">[] } {
  const task = tasks.find((t) => t.id === taskId);
  if (!task || task.status === "completed" || task.status === "failed") return { tasks, events: [] };
  const attempts = (task.attempts ?? 0) + 1;
  const events: Omit<ActivityEvent, "id">[] = [
    {
      at: "just now",
      agentName: "Team Lead",
      text: `${task.title} failed: ${reason}${attempts >= MAX_ATTEMPTS ? " Escalated to you." : " You can retry it."}`,
      kind: "system",
    },
  ];
  return { tasks: tasks.map((t) => (t.id === taskId ? { ...t, status: "failed" as const, attempts } : t)), events };
}

/** Retry a failed task — resumes with a small progress penalty. */
export function retryTask(tasks: Task[], taskId: string): { tasks: Task[]; events: Omit<ActivityEvent, "id">[] } {
  const task = tasks.find((t) => t.id === taskId);
  if (!task || task.status !== "failed") return { tasks, events: [] };
  return {
    tasks: tasks.map((t) =>
      t.id === taskId ? { ...t, status: "working" as const, progress: Math.max(0, t.progress - 20) } : t,
    ),
    events: [{ at: "just now", agentName: "Team Lead", text: `${task.title} retried — resuming with revised inputs.`, kind: "work" }],
  };
}
