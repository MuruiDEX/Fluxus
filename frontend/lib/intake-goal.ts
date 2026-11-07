/**
 * Goal deep-link handling for the LIVE intake (`?goal=…`).
 *
 * The command bar, the CEO briefing and onboarding all hand a goal to
 * `/projects/new` through the query string, but LIVE intake only read
 * `?brief=` — the typed goal was dropped and the user faced an empty field.
 *
 * Rules kept here (pure, unit-tested):
 * - Decode exactly once. `useSearchParams()` already percent-decodes, so this
 *   module never touches `decodeURIComponent` (a double decode would turn the
 *   literal text "%20" into a space).
 * - Missing, empty or whitespace-only values mean "no seed" — the intake then
 *   behaves exactly as before.
 * - Repeated `goal` parameters resolve predictably: the first non-blank value.
 * - The backend caps `goal` at 2000 characters (`BriefCreate.goal` in
 *   `backend/app/api/v1/briefs.py`), so the
 *   seed is clamped to the same limit by code points (matching Python's len)
 *   instead of letting a huge URL produce a request the server must reject.
 * - A new parameter never overwrites text the user already edited.
 */

export const GOAL_PARAM = "goal";

/** Mirrors `BriefCreate.goal = Field(min_length=1, max_length=2000)`. */
export const GOAL_MAX_LENGTH = 2000;

/** Structural type: Next's ReadonlyURLSearchParams and URLSearchParams both fit. */
export interface GoalSearchParams {
  getAll(name: string): string[];
}

/** Clamp by code points so a surrogate pair (emoji) is never split in half. */
export function clampGoal(value: string): string {
  const points = Array.from(value);
  return points.length > GOAL_MAX_LENGTH ? points.slice(0, GOAL_MAX_LENGTH).join("") : value;
}

/**
 * The goal carried by the URL, or "" when there is nothing usable.
 * The value is returned verbatim (outer spaces included) so the field shows
 * exactly what was linked; only a blank value is treated as absent.
 */
export function readGoalParam(params: GoalSearchParams): string {
  for (const raw of params.getAll(GOAL_PARAM)) {
    const value = clampGoal(raw);
    if (value.trim().length > 0) return value;
  }
  return "";
}

/** `/projects/new?goal=…` with one shared encoder for every producer. */
export function goalHref(goal: string): string {
  return `/projects/new?${GOAL_PARAM}=${encodeURIComponent(goal)}`;
}

export interface GoalSeed {
  /** Last value adopted from the URL. */
  seeded: string;
  /** What the textarea should show. */
  value: string;
}

/**
 * Decide what a URL update means for a field the user may be typing in.
 *
 * - Same seed as before → keep the current text (rerenders are no-ops).
 * - New seed + untouched field (empty, or still the previously seeded text) →
 *   adopt the new goal.
 * - New seed + edited field → keep the user's text, only remember the seed.
 */
export function adoptGoalParam(current: string, seeded: string, param: string): GoalSeed {
  if (param === seeded) return { seeded, value: current };
  if (current === "" || current === seeded) return { seeded: param, value: param };
  return { seeded: param, value: current };
}
