import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
  Task,
} from "@/lib/domain/types";
import { buildWorkflowModel } from "@/lib/workflow";
import type { WorkflowModel } from "@/lib/workflow";

export type LiveWorkflowSourceStatus =
  | "idle"
  | "loading"
  | "ready"
  | "error";

export type LiveWorkflowTaskScope =
  | {
      readonly kind: "project";
      readonly projectId: string;
    }
  | {
      readonly kind: "business";
      /** Exact project IDs passed to useLiveTasks.loadAll. Never inferred. */
      readonly projectIds: readonly string[];
    };

export interface LiveWorkflowProjectsSource {
  readonly status: LiveWorkflowSourceStatus;
  readonly businessId: string | null;
  readonly projects: readonly Project[];
  readonly error: string | null;
}

export interface LiveWorkflowTasksSource {
  readonly status: LiveWorkflowSourceStatus;
  readonly businessId: string | null;
  readonly scope: LiveWorkflowTaskScope | null;
  readonly tasks: readonly Task[];
  readonly error: string | null;
}

export interface LiveWorkflowAgentsSource {
  readonly status: LiveWorkflowSourceStatus;
  readonly businessId: string | null;
  readonly agents: readonly Agent[];
  readonly error: string | null;
}

export interface LiveWorkflowApprovalsSource {
  readonly status: LiveWorkflowSourceStatus;
  readonly businessId: string | null;
  readonly approvals: readonly Approval[];
  readonly error: string | null;
}

export interface LiveWorkflowActivitySource {
  readonly status: LiveWorkflowSourceStatus;
  readonly businessId: string | null;
  readonly projectId: string | null;
  readonly events: readonly ActivityEvent[];
  readonly error: string | null;
}

export interface LiveWorkflowInput {
  readonly businessId: string;
  readonly projects: LiveWorkflowProjectsSource;
  readonly tasks: LiveWorkflowTasksSource;
  readonly agents: LiveWorkflowAgentsSource;
  readonly approvals: LiveWorkflowApprovalsSource;
  readonly activity: LiveWorkflowActivitySource;
}

export type LiveWorkflowResult =
  | {
      readonly status: "loading";
      readonly model: null;
      readonly reasons: readonly string[];
    }
  | {
      readonly status: "unavailable";
      readonly model: null;
      readonly reasons: readonly string[];
    }
  | {
      readonly status: "no_project";
      readonly model: null;
      readonly reasons: readonly string[];
    }
  | {
      readonly status: "ready";
      readonly model: WorkflowModel;
    };

export const LIVE_WORKFLOW_NO_SNAPSHOT_LIMITATION =
  "LIVE workflow data is assembled from sequential paginated reads without a server snapshot; concurrent changes can produce a mixed read.";

function isPending(status: LiveWorkflowSourceStatus): boolean {
  return status === "idle" || status === "loading";
}

function scopeIncludesProject(
  scope: LiveWorkflowTaskScope,
  projectId: string,
): boolean {
  return scope.kind === "project"
    ? scope.projectId === projectId
    : scope.projectIds.includes(projectId);
}

/**
 * Preserve the existing LIVE Office policy exactly: first non-completed
 * project in store/server order, otherwise the first project. Never sort.
 */
export function selectLiveOfficeProject(
  projects: readonly Project[],
): Project | null {
  return (
    projects.find((project) => project.status !== "completed") ??
    projects[0] ??
    null
  );
}

/**
 * Select a project only after the store proves the project list belongs to
 * the active business. The stored business id is the ownership proof; a
 * Project object is not used for business scope.
 */
export function selectLiveOfficeProjectForBusiness(
  source: Pick<
    LiveWorkflowProjectsSource,
    "status" | "businessId" | "projects"
  >,
  businessId: string,
): Project | null {
  if (source.status !== "ready" || source.businessId !== businessId) {
    return null;
  }
  return selectLiveOfficeProject(source.projects);
}

export function buildLiveOfficeWorkflowModel(
  input: LiveWorkflowInput,
): LiveWorkflowResult {
  if (isPending(input.projects.status)) {
    return { status: "loading", model: null, reasons: ["Projects are loading."] };
  }
  if (input.projects.status === "error") {
    return {
      status: "unavailable",
      model: null,
      reasons: [input.projects.error ?? "Projects are unavailable."],
    };
  }
  if (input.projects.businessId !== input.businessId) {
    return {
      status: "unavailable",
      model: null,
      reasons: ["Project data does not belong to the active business."],
    };
  }

  const project = selectLiveOfficeProject(input.projects.projects);
  if (!project) {
    return {
      status: "no_project",
      model: null,
      reasons: ["No project is available."],
    };
  }

  if (isPending(input.tasks.status)) {
    return {
      status: "loading",
      model: null,
      reasons: ["Selected-project tasks are loading."],
    };
  }
  if (input.tasks.status === "error") {
    return {
      status: "unavailable",
      model: null,
      reasons: [input.tasks.error ?? "Selected-project tasks are unavailable."],
    };
  }
  if (input.tasks.businessId !== input.businessId) {
    return {
      status: "unavailable",
      model: null,
      reasons: ["Task data does not belong to the active business."],
    };
  }
  if (
    input.tasks.scope === null ||
    !scopeIncludesProject(input.tasks.scope, project.id)
  ) {
    return {
      status: "unavailable",
      model: null,
      reasons: [
        "Task data does not prove complete pagination for the selected project.",
      ],
    };
  }

  const agentsLoading = isPending(input.agents.status);
  const approvalsLoading = isPending(input.approvals.status);
  const activityLoading = isPending(input.activity.status);
  const agentsAvailable =
    input.agents.status === "ready" &&
    input.agents.businessId === input.businessId;
  const approvalsAvailable =
    input.approvals.status === "ready" &&
    input.approvals.businessId === input.businessId;
  const activityAvailable =
    input.activity.status === "ready" &&
    input.activity.businessId === input.businessId &&
    input.activity.projectId === null;

  const base = buildWorkflowModel({
    project,
    tasks: input.tasks.tasks,
    agents: agentsAvailable ? input.agents.agents : [],
    approvals: approvalsAvailable ? input.approvals.approvals : [],
    events: activityAvailable ? input.activity.events : [],
    mode: "live",
    activityScope: "business",
    approvalsCompleteness: approvalsLoading
      ? "loading"
      : approvalsAvailable
        ? "partial"
        : "unavailable",
    agentsAvailability: agentsLoading
      ? "loading"
      : agentsAvailable
        ? "available"
        : "unavailable",
    activityAvailability: activityLoading
      ? "loading"
      : activityAvailable
        ? "available"
        : "unavailable",
  });

  return {
    status: "ready",
    model: {
      ...base,
      limitations: [...base.limitations, LIVE_WORKFLOW_NO_SNAPSHOT_LIMITATION],
    },
  };
}
