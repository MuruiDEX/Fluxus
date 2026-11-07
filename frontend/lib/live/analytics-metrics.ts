/**
 * LIVE analytics metric states (Phase 0).
 *
 * A failed load is "unavailable", never "ready". The analytics cards used to
 * fall back to the empty summary after an API error, rendering $0.00 — a
 * balance the server never reported. Callers render "—" for unavailable and a
 * skeleton only while pending.
 */

import type { Project } from "@/lib/domain/types";

export type LoadStatus = "idle" | "loading" | "ready" | "error";
export type MetricState = "pending" | "ready" | "unavailable";

export function metricState(status: LoadStatus): MetricState {
  if (status === "ready") return "ready";
  if (status === "error") return "unavailable";
  return "pending";
}

export interface ProjectTotals {
  budget: number;
  spent: number;
}

export function projectTotals(projects: Project[]): ProjectTotals {
  return projects.reduce(
    (acc, project) => ({
      budget: acc.budget + project.aiBudget,
      spent: acc.spent + project.aiSpent,
    }),
    { budget: 0, spent: 0 },
  );
}
