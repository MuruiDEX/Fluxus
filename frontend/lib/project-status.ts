/**
 * Project status display mapping (Phase 0).
 *
 * The projects grid used to render "In Progress" for every row regardless of
 * the actual status, claiming moving work on completed and planning projects
 * alike. Every surface now derives its label from the real row, and an
 * unknown wire value is shown verbatim instead of guessed.
 */

export type ProjectStatusValue = "planning" | "in_progress" | "review" | "completed";

export type ProjectStatusTone = "planning" | "active" | "review" | "done";

export interface ProjectStatusView {
  label: string;
  tone: ProjectStatusTone;
}

const VIEWS: Record<ProjectStatusValue, ProjectStatusView> = {
  planning: { label: "Planning", tone: "planning" },
  in_progress: { label: "In Progress", tone: "active" },
  review: { label: "In Review", tone: "review" },
  completed: { label: "Completed", tone: "done" },
};

/** Unknown statuses render as-is (never silently coerced to "In Progress"). */
export function projectStatusView(status: string): ProjectStatusView {
  return VIEWS[status as ProjectStatusValue] ?? { label: status || "Unknown", tone: "planning" };
}
