/**
 * Where "New Project" goes — one contract for every surface.
 *
 * `/projects/new` is the single entry point in both contexts. The route itself
 * decides what to render (`app/(app)/projects/new/page.tsx`):
 * - LIVE (`isLiveContext(...)`) → `LiveIntakeView`, the server-backed Team Lead
 *   intake (Stage 11: brief → answers → plan);
 * - DEMO → the simulated wizard.
 * A separate LIVE route does not exist and is not needed. The LIVE projects
 * page used to show a disabled button that claimed project creation was not
 * available in LIVE — stale, since the intake has shipped.
 *
 * The control only navigates. It never creates anything: the intake prefills
 * from `?goal=` (F1) and waits for "Ask the Team Lead", and a project appears
 * only after `POST .../briefs/{id}/plan` succeeds. Navigating twice therefore
 * has no side effect beyond re-opening the same form.
 */

import { clampGoal, goalHref } from "./intake-goal";

export const NEW_PROJECT_PATH = "/projects/new";

/**
 * The New Project destination: the bare route, or the same route seeded with a
 * goal. A blank goal falls back to the bare route so an empty `?goal=` never
 * reaches the intake (which treats it exactly like no seed at all).
 */
export function newProjectHref(goal?: string | null): string {
  const value = (goal ?? "").trim();
  return value ? goalHref(clampGoal(value)) : NEW_PROJECT_PATH;
}
