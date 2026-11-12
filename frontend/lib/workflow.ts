/**
 * AI Office workflow derivation (Slice 5B-1).
 *
 * Pure domain -> view-model mapping for the approved workflow spine. It is
 * intentionally independent of React, Zustand, API clients, stores, browser
 * APIs, the simulation engine, and wall-clock time. Inputs are never mutated.
 *
 * Two data realities this module must not paper over:
 * - LIVE approvals arrive from a business-scoped, paginated read (default 50,
 *   max 200). The input contract is only `Approval[]`, with no total/limit, so
 *   completeness cannot be verified here. Callers must pass a complete list or
 *   treat the summary as partial. The same applies to activity.
 * - `waiting_assets` exists in the backend enum but has no producer or
 *   documented human-input semantics in backend code. The demo simulator
 *   treats it as dependency-gated, so this module does the same: unmet
 *   dependencies win; once dependencies are complete the task is reported as
 *   ready with an explicit stale/unconfirmed note. It is never silently
 *   labelled "Waiting for you".
 *
 * Unknown task statuses and unknown dependency ids are preserved and made
 * visible, never dropped and never assumed complete.
 */

import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
  Task,
  TaskStatus,
} from "@/lib/domain/types";

export type WorkflowMode = "demo" | "live";
export type ActivityScope = "business" | "project";

export type WorkflowLaneKey =
  | "running"
  | "waiting_dependency"
  | "waiting_user"
  | "completed"
  | "ready"
  | "attention";

export type WorkflowGateState =
  | "none"
  | "missing"
  | "pending"
  | "approved"
  | "rejected"
  | "unknown";

/**
 * Tasks are accepted with a widened status because runtime/persisted values
 * can fall outside the domain union. Everything else is the domain `Task`.
 */
export type WorkflowTaskInput = Omit<Task, "status"> & {
  readonly status: string;
};

export interface WorkflowInput {
  readonly project: Project | null | undefined;
  readonly tasks: readonly WorkflowTaskInput[];
  readonly agents: readonly Agent[];
  readonly approvals: readonly Approval[];
  readonly events: readonly ActivityEvent[];
  readonly mode: WorkflowMode;
  readonly activityScope: ActivityScope;
}

export interface WorkflowDependencyRef {
  readonly id: string;
  readonly title: string | null;
  readonly loaded: boolean;
  readonly completed: boolean;
}

export interface WorkflowTaskView {
  readonly task: WorkflowTaskInput;
  readonly lane: WorkflowLaneKey;
  readonly rawStatus: string;
  readonly knownStatus: TaskStatus | null;
  readonly owner: Agent | null;
  readonly waitingOn: readonly WorkflowDependencyRef[];
  readonly gate: Approval | null;
  readonly gateState: WorkflowGateState;
  readonly note: string | null;
}

export interface WorkflowLane {
  readonly key: WorkflowLaneKey;
  readonly label: string;
  readonly kind: "primary" | "secondary";
  readonly description: string;
  readonly tasks: readonly WorkflowTaskView[];
}

export interface WorkflowCounts {
  readonly total: number;
  readonly completed: number;
  readonly running: number;
  readonly waitingDependency: number;
  readonly waitingUser: number;
  readonly ready: number;
  readonly attention: number;
}

export interface WorkflowProgress {
  readonly completed: number;
  readonly total: number;
  /** 0..1; zero when there are no tasks. */
  readonly ratio: number;
  /** Rounded 0..100; zero when there are no tasks. */
  readonly percent: number;
}

export const WORKFLOW_LANE_ORDER: readonly WorkflowLaneKey[] = [
  "running",
  "waiting_dependency",
  "waiting_user",
  "completed",
  "ready",
  "attention",
];

export interface WorkflowGoal {
  readonly key: "goal";
  readonly state: "available" | "unavailable";
  readonly projectId: string | null;
  readonly title: string | null;
  readonly goal: string | null;
  readonly deadline: string | null;
  readonly status: Project["status"] | null;
  readonly href: string | null;
  readonly summary: string;
}

export interface WorkflowTeamLead {
  readonly key: "team_lead";
  readonly state: "available" | "unavailable";
  readonly agent: Agent | null;
  readonly href: string;
  readonly note: string;
}

export interface WorkflowParallelWork {
  readonly key: "parallel_work";
  readonly state: "available" | "empty" | "unavailable";
  readonly lanes: readonly WorkflowLane[];
  readonly href: string | null;
  readonly note: string | null;
}

export interface WorkflowApprovals {
  readonly key: "approvals";
  readonly state: "available" | "empty" | "unavailable";
  readonly pending: readonly Approval[];
  readonly decided: readonly Approval[];
  readonly unknown: readonly Approval[];
  readonly pendingCount: number;
  readonly decidedCount: number;
  readonly unknownCount: number;
  readonly href: string;
  readonly note: string;
}

export interface WorkflowReview {
  readonly key: "review";
  readonly state: "completed" | "in_review" | "ready" | "open" | "empty";
  readonly label: string;
  readonly openTaskCount: number;
  readonly derived: boolean;
  readonly href: string | null;
  readonly note: string;
}

export interface WorkflowBrain {
  readonly key: "brain";
  readonly state: "destination";
  readonly label: string;
  readonly href: string;
  readonly note: string;
}

export type WorkflowStage =
  | WorkflowGoal
  | WorkflowTeamLead
  | WorkflowParallelWork
  | WorkflowApprovals
  | WorkflowReview
  | WorkflowBrain;

export interface WorkflowActivity {
  readonly scope: ActivityScope;
  readonly scopedToProject: boolean;
  /** First 8 events in the supplied order; no time-based sorting. */
  readonly preview: readonly ActivityEvent[];
  /** Count of events supplied by the caller, not a server total. */
  readonly providedCount: number;
  readonly label: string;
  readonly note: string;
}

export interface WorkflowModel {
  readonly mode: WorkflowMode;
  readonly project: Project | null;
  readonly goal: WorkflowGoal;
  readonly teamLead: WorkflowTeamLead;
  readonly parallelWork: WorkflowParallelWork;
  readonly approvals: WorkflowApprovals;
  readonly review: WorkflowReview;
  readonly brain: WorkflowBrain;
  readonly stages: readonly WorkflowStage[];
  readonly lanes: readonly WorkflowLane[];
  readonly tasks: readonly WorkflowTaskView[];
  readonly counts: WorkflowCounts;
  readonly progress: WorkflowProgress;
  readonly activity: WorkflowActivity;
  readonly limitations: readonly string[];
}

const LANE_META: Record<WorkflowLaneKey, Omit<WorkflowLane, "tasks">> = {
  running: {
    key: "running",
    label: "Running",
    kind: "primary",
    description: "Tasks actively executing now.",
  },
  waiting_dependency: {
    key: "waiting_dependency",
    label: "Waiting on dependencies",
    kind: "primary",
    description: "Blocked by unfinished or missing dependencies.",
  },
  waiting_user: {
    key: "waiting_user",
    label: "Waiting for you",
    kind: "primary",
    description: "Pending approval gates that need a human decision.",
  },
  completed: {
    key: "completed",
    label: "Completed",
    kind: "primary",
    description: "Finished work.",
  },
  ready: {
    key: "ready",
    label: "Ready to run",
    kind: "secondary",
    description: "Unblocked tasks waiting to start.",
  },
  attention: {
    key: "attention",
    label: "Needs attention",
    kind: "secondary",
    description: "Failed, rejected, unknown, or inconsistent states.",
  },
};

const TEAM_LEAD_ROLE = /^(team\s+lead|lead|coordinator)$/i;

const WAITING_ASSETS_NOTE =
  "Status is waiting_assets; the backend defines no producer or human-input semantics for it.";

const KNOWN_TASK_STATUSES: readonly TaskStatus[] = [
  "not_started",
  "working",
  "waiting_dependency",
  "waiting_assets",
  "waiting_approval",
  "completed",
  "failed",
];

function compareStrings(a: string, b: string): number {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

function compareTaskViews(a: WorkflowTaskView, b: WorkflowTaskView): number {
  return (
    compareStrings(a.task.title, b.task.title) ||
    compareStrings(a.task.id, b.task.id)
  );
}

function compareApprovals(a: Approval, b: Approval): number {
  return compareStrings(a.title, b.title) || compareStrings(a.id, b.id);
}

function joinNotes(...notes: Array<string | null | undefined>): string | null {
  const parts = notes.filter(
    (note): note is string => typeof note === "string" && note.length > 0,
  );
  return parts.length > 0 ? parts.join(" ") : null;
}

function isTaskStatus(status: string): status is TaskStatus {
  return KNOWN_TASK_STATUSES.some((known) => known === status);
}

function teamLeadRank(role: string): number | null {
  const trimmed = role.trim();
  if (!TEAM_LEAD_ROLE.test(trimmed)) return null;
  const normalized = trimmed.toLowerCase().replace(/\s+/g, " ");
  if (normalized === "team lead") return 0;
  if (normalized === "lead") return 1;
  if (normalized === "coordinator") return 2;
  return 3;
}

function findTeamLead(project: Project, agents: readonly Agent[]): Agent | null {
  const teamIds = new Set(project.teamIds);
  const candidates = agents
    .filter((agent) => teamIds.has(agent.id))
    .map((agent) => ({ agent, rank: teamLeadRank(agent.role) }))
    .filter(
      (entry): entry is { agent: Agent; rank: number } => entry.rank !== null,
    )
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        compareStrings(a.agent.name, b.agent.name) ||
        compareStrings(a.agent.id, b.agent.id),
    );
  return candidates[0]?.agent ?? null;
}

function resolveDependencies(
  task: WorkflowTaskInput,
  tasksById: ReadonlyMap<string, WorkflowTaskInput>,
): readonly WorkflowDependencyRef[] {
  const seen = new Set<string>();
  const refs: WorkflowDependencyRef[] = [];
  for (const id of task.dependsOn) {
    if (seen.has(id)) continue;
    seen.add(id);
    const dependency = tasksById.get(id);
    refs.push({
      id,
      title: dependency?.title ?? null,
      loaded: dependency !== undefined,
      completed: dependency?.status === "completed",
    });
  }
  return refs;
}

function dependencyNote(
  refs: readonly WorkflowDependencyRef[],
): string | null {
  const missing = refs.filter((ref) => !ref.loaded).map((ref) => ref.id);
  return missing.length > 0
    ? `Dependency not loaded: ${missing.join(", ")}.`
    : null;
}

interface GateResolution {
  gate: Approval | null;
  state: WorkflowGateState;
  rawStatus: string | null;
}

function classifyGate(gate: Approval): GateResolution {
  if (gate.status === "pending") {
    return { gate, state: "pending", rawStatus: null };
  }
  if (gate.status === "approved") {
    return { gate, state: "approved", rawStatus: null };
  }
  if (gate.status === "rejected") {
    return { gate, state: "rejected", rawStatus: null };
  }
  return { gate, state: "unknown", rawStatus: String(gate.status) };
}

/**
 * The engine links a gate directly with `task.approvalGateId`. Approvals also
 * carry `gatesTaskId`, so that side of the link is used as a fallback when the
 * task has no direct id. Both fields are scoped to the same project.
 */
function resolveGate(
  gateId: string | undefined,
  taskId: string,
  approvalsById: ReadonlyMap<string, Approval>,
  gateByTaskId: ReadonlyMap<string, Approval>,
): GateResolution {
  if (gateId) {
    const gate = approvalsById.get(gateId);
    if (!gate) return { gate: null, state: "missing", rawStatus: null };
    return classifyGate(gate);
  }
  const linked = gateByTaskId.get(taskId);
  if (linked) return classifyGate(linked);
  return { gate: null, state: "none", rawStatus: null };
}

function gateNote(
  state: WorkflowGateState,
  rawStatus: string | null,
): string | null {
  switch (state) {
    case "none":
      return null;
    case "missing":
      return "Approval gate is not loaded.";
    case "pending":
      return "Approval gate is pending.";
    case "approved":
      return "Approval gate is approved.";
    case "rejected":
      return "Approval gate is rejected.";
    case "unknown":
      return `Approval gate has unknown status "${rawStatus ?? "unknown"}".`;
  }
}

function classifyTask(
  task: WorkflowTaskInput,
  waitingOn: readonly WorkflowDependencyRef[],
  gateState: WorkflowGateState,
  gateRawStatus: string | null,
): { lane: WorkflowLaneKey; note: string | null } {
  if (task.status === "completed") return { lane: "completed", note: null };
  if (task.status === "failed") {
    return { lane: "attention", note: "Task failed." };
  }
  if (gateState === "rejected") {
    return { lane: "attention", note: "Approval gate rejected." };
  }
  if (task.status === "working") return { lane: "running", note: null };

  const hasUnmetDependencies = waitingOn.some((ref) => !ref.completed);
  const depNote = dependencyNote(waitingOn);
  const gateStateNote = gateNote(gateState, gateRawStatus);

  switch (task.status) {
    case "waiting_approval": {
      if (hasUnmetDependencies) {
        return {
          lane: "waiting_dependency",
          note: joinNotes(depNote, gateStateNote),
        };
      }
      switch (gateState) {
        case "approved":
          return {
            lane: "ready",
            note: "Status is waiting_approval but the gate is approved; the task can be run.",
          };
        case "pending":
          return { lane: "waiting_user", note: null };
        case "missing":
          return {
            lane: "attention",
            note: "Status is waiting_approval but its approval gate is not loaded.",
          };
        case "unknown":
          return {
            lane: "attention",
            note: `Status is waiting_approval but its approval gate has unknown status "${gateRawStatus ?? "unknown"}".`,
          };
        case "none":
          return {
            lane: "attention",
            note: "Status is waiting_approval but no approval gate is linked.",
          };
      }
      return { lane: "attention", note: gateStateNote };
    }

    case "waiting_assets": {
      if (hasUnmetDependencies) {
        return {
          lane: "waiting_dependency",
          note: joinNotes(depNote, WAITING_ASSETS_NOTE, gateStateNote),
        };
      }
      switch (gateState) {
        case "pending":
          return {
            lane: "waiting_user",
            note: joinNotes(WAITING_ASSETS_NOTE, "Approval gate is pending."),
          };
        case "approved":
        case "none":
          return {
            lane: "ready",
            note: joinNotes(WAITING_ASSETS_NOTE, "Dependencies are complete."),
          };
        case "missing":
          return {
            lane: "attention",
            note: joinNotes(WAITING_ASSETS_NOTE, "Approval gate is not loaded."),
          };
        case "unknown":
          return {
            lane: "attention",
            note: joinNotes(WAITING_ASSETS_NOTE, gateStateNote),
          };
      }
      return { lane: "attention", note: gateStateNote };
    }

    case "waiting_dependency": {
      if (hasUnmetDependencies) {
        return {
          lane: "waiting_dependency",
          note: joinNotes(depNote, gateStateNote),
        };
      }
      switch (gateState) {
        case "pending":
          return {
            lane: "waiting_user",
            note: "Status is waiting_dependency but dependencies are complete; the approval gate is pending.",
          };
        case "approved":
        case "none":
          return {
            lane: "ready",
            note: "Status is waiting_dependency but all dependencies are complete.",
          };
        case "missing":
          return {
            lane: "attention",
            note: "Status is waiting_dependency but its approval gate is not loaded.",
          };
        case "unknown":
          return {
            lane: "attention",
            note: `Status is waiting_dependency but its approval gate has unknown status "${gateRawStatus ?? "unknown"}".`,
          };
      }
      return { lane: "attention", note: gateStateNote };
    }

    case "not_started": {
      if (hasUnmetDependencies) {
        return {
          lane: "waiting_dependency",
          note: joinNotes(depNote, gateStateNote),
        };
      }
      switch (gateState) {
        case "pending":
          return { lane: "waiting_user", note: null };
        case "approved":
        case "none":
          return { lane: "ready", note: null };
        case "missing":
          return {
            lane: "attention",
            note: "Approval gate is not loaded; cannot verify approval.",
          };
        case "unknown":
          return {
            lane: "attention",
            note: `Approval gate has unknown status "${gateRawStatus ?? "unknown"}".`,
          };
      }
      return { lane: "attention", note: gateStateNote };
    }

    default:
      return {
        lane: "attention",
        note: joinNotes(
          `Unknown task status "${String(task.status)}".`,
          depNote,
        ),
      };
  }
}

export function buildWorkflowModel(input: WorkflowInput): WorkflowModel {
  const project = input.project ?? null;
  const projectApprovals = project
    ? input.approvals.filter((approval) => approval.projectId === project.id)
    : [];

  const approvalsById = new Map(
    projectApprovals.map((approval) => [approval.id, approval]),
  );
  const gateByTaskId = new Map<string, Approval>();
  for (const approval of [...projectApprovals].sort(compareApprovals)) {
    if (!approval.gatesTaskId) continue;
    if (!gateByTaskId.has(approval.gatesTaskId)) {
      gateByTaskId.set(approval.gatesTaskId, approval);
    }
  }
  const agentsById = new Map(input.agents.map((agent) => [agent.id, agent]));

  const tasksById = new Map<string, WorkflowTaskInput>();
  const projectTasks = project
    ? input.tasks.filter((task) => task.projectId === project.id)
    : [];
  for (const task of projectTasks) {
    if (!tasksById.has(task.id)) tasksById.set(task.id, task);
  }

  const taskViews: WorkflowTaskView[] = [];
  for (const task of projectTasks) {
    const waitingOn = resolveDependencies(task, tasksById);
    const {
      gate,
      state: gateState,
      rawStatus: gateRawStatus,
    } = resolveGate(
      task.approvalGateId,
      task.id,
      approvalsById,
      gateByTaskId,
    );
    const { lane, note } = classifyTask(
      task,
      waitingOn,
      gateState,
      gateRawStatus,
    );
    taskViews.push({
      task,
      lane,
      rawStatus: String(task.status),
      knownStatus: isTaskStatus(task.status) ? task.status : null,
      owner: agentsById.get(task.ownerAgentId) ?? null,
      waitingOn,
      gate,
      gateState,
      note,
    });
  }
  taskViews.sort(compareTaskViews);

  const laneTasks: Record<WorkflowLaneKey, WorkflowTaskView[]> = {
    running: [],
    waiting_dependency: [],
    waiting_user: [],
    completed: [],
    ready: [],
    attention: [],
  };
  for (const view of taskViews) laneTasks[view.lane].push(view);

  const lanes: WorkflowLane[] = WORKFLOW_LANE_ORDER.map((key) => ({
    ...LANE_META[key],
    tasks: laneTasks[key],
  }));

  const counts: WorkflowCounts = {
    total: taskViews.length,
    completed: laneTasks.completed.length,
    running: laneTasks.running.length,
    waitingDependency: laneTasks.waiting_dependency.length,
    waitingUser: laneTasks.waiting_user.length,
    ready: laneTasks.ready.length,
    attention: laneTasks.attention.length,
  };

  const ratio = counts.total === 0 ? 0 : counts.completed / counts.total;
  const progress: WorkflowProgress = {
    completed: counts.completed,
    total: counts.total,
    ratio,
    percent: Math.round(ratio * 100),
  };

  const goal: WorkflowGoal = project
    ? {
        key: "goal",
        state: "available",
        projectId: project.id,
        title: project.title,
        goal: project.goal,
        deadline: project.deadline,
        status: project.status,
        href: `/projects/${project.id}`,
        summary:
          project.goal.trim() || project.title.trim() || "No goal provided.",
      }
    : {
        key: "goal",
        state: "unavailable",
        projectId: null,
        title: null,
        goal: null,
        deadline: null,
        status: null,
        href: null,
        summary: "No active project.",
      };

  const lead = project ? findTeamLead(project, input.agents) : null;
  const teamLead: WorkflowTeamLead = {
    key: "team_lead",
    state: lead ? "available" : "unavailable",
    agent: lead,
    href: "/agents",
    note: !project
      ? "No project selected."
      : lead
        ? `Matched project role "${lead.role}".`
        : "No Team Lead role in this project's roster.",
  };

  const pending: Approval[] = [];
  const decided: Approval[] = [];
  const unknown: Approval[] = [];
  for (const approval of projectApprovals) {
    if (approval.status === "pending") pending.push(approval);
    else if (
      approval.status === "approved" ||
      approval.status === "rejected"
    ) {
      decided.push(approval);
    } else unknown.push(approval);
  }
  pending.sort(compareApprovals);
  decided.sort(compareApprovals);
  unknown.sort(compareApprovals);

  const approvals: WorkflowApprovals = {
    key: "approvals",
    state: project
      ? projectApprovals.length === 0
        ? "empty"
        : "available"
      : "unavailable",
    pending,
    decided,
    unknown,
    pendingCount: pending.length,
    decidedCount: decided.length,
    unknownCount: unknown.length,
    href: "/approvals",
    note: !project
      ? "No project selected."
      : projectApprovals.length === 0
        ? "No approvals in the provided list for this project."
        : pending.length > 0
          ? `${pending.length} pending approval${pending.length === 1 ? "" : "s"} for this project.`
          : `${decided.length} decided approval${decided.length === 1 ? "" : "s"}; none pending.`,
  };

  const parallelWork: WorkflowParallelWork = {
    key: "parallel_work",
    state: project
      ? counts.total === 0
        ? "empty"
        : "available"
      : "unavailable",
    lanes,
    href: project ? `/projects/${project.id}` : null,
    note: !project
      ? "No project selected."
      : counts.total === 0
        ? "No tasks in this project yet."
        : null,
  };

  const openTaskCount = counts.total - counts.completed;
  let review: WorkflowReview;
  if (!project) {
    review = {
      key: "review",
      state: "empty",
      label: "No tasks yet",
      openTaskCount: 0,
      derived: false,
      href: null,
      note: "No project selected.",
    };
  } else if (project.status === "completed") {
    review = {
      key: "review",
      state: "completed",
      label: "Completed",
      openTaskCount,
      derived: false,
      href: `/projects/${project.id}`,
      note: "Server project status is completed.",
    };
  } else if (project.status === "review") {
    review = {
      key: "review",
      state: "in_review",
      label: "In review (server)",
      openTaskCount,
      derived: false,
      href: `/projects/${project.id}`,
      note: "Server project status is review.",
    };
  } else if (counts.total > 0 && openTaskCount === 0) {
    review = {
      key: "review",
      state: "ready",
      label: "Ready for review (derived)",
      openTaskCount: 0,
      derived: true,
      href: `/projects/${project.id}`,
      note: "Derived: every task is completed; server status was not changed.",
    };
  } else if (counts.total === 0) {
    review = {
      key: "review",
      state: "empty",
      label: "No tasks yet",
      openTaskCount: 0,
      derived: false,
      href: `/projects/${project.id}`,
      note: "No tasks; review readiness requires at least one task.",
    };
  } else {
    review = {
      key: "review",
      state: "open",
      label: `${openTaskCount} task${openTaskCount === 1 ? "" : "s"} still open`,
      openTaskCount,
      derived: false,
      href: `/projects/${project.id}`,
      note: "Derived: open tasks remain.",
    };
  }

  const brain: WorkflowBrain = {
    key: "brain",
    state: "destination",
    label: "Open Business Brain",
    href: "/brain",
    note: "Business-wide destination; no project-scoped Brain link is available.",
  };

  const activity: WorkflowActivity = {
    scope: input.activityScope,
    scopedToProject: input.activityScope === "project",
    preview: input.events.slice(0, 8),
    providedCount: input.events.length,
    label:
      input.activityScope === "project"
        ? "Project activity"
        : input.mode === "demo"
          ? "Business activity (demo)"
          : "Business activity",
    note:
      input.activityScope === "project"
        ? "Project-scoped activity provided by the caller."
        : input.mode === "demo"
          ? "Demo activity is business-wide and cannot be attributed to one project."
          : "Business-wide activity; not project-scoped.",
  };

  const stages: readonly WorkflowStage[] = [
    goal,
    teamLead,
    parallelWork,
    approvals,
    review,
    brain,
  ];

  const limitations = new Set<string>();
  if (!project) limitations.add("No project selected.");
  if (input.mode === "live") {
    limitations.add(
      "Live approvals and activity are caller-supplied pages; completeness is not verifiable here.",
    );
  }
  if (input.activityScope === "business") {
    limitations.add(
      input.mode === "demo"
        ? "Demo activity is business-wide and cannot be attributed to one project."
        : "Activity is business-wide; it is not project-scoped.",
    );
  }
  if (taskViews.some((view) => view.gateState === "missing")) {
    limitations.add(
      "One or more approval gates were not present in the supplied approvals list.",
    );
  }
  if (taskViews.some((view) => view.waitingOn.some((ref) => !ref.loaded))) {
    limitations.add(
      "One or more task dependencies were not present in the supplied task list.",
    );
  }

  return {
    mode: input.mode,
    project,
    goal,
    teamLead,
    parallelWork,
    approvals,
    review,
    brain,
    stages,
    lanes,
    tasks: taskViews,
    counts,
    progress,
    activity,
    limitations: Array.from(limitations),
  };
}
