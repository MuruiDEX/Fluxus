/**
 * Background-refresh policy for LIVE screens.
 *
 * The backend has no push channel (no SSE/WebSocket endpoint — checked against
 * `backend/app/api/v1`), so live state reaches the page by re-reading the same
 * REST resources the initial load uses. This module holds only the scheduling
 * and reporting rules, so they can be unit-tested without a DOM:
 *
 * - Poll faster while work is in flight, slower when the workspace is idle.
 * - Back off on failure (instead of hammering a dead connection), and never
 *   grow past a minute; a success resets the cadence immediately.
 * - The heavier collections (activity, usage, projects, business) refresh on
 *   every Nth tick so a poll costs three list reads, not seven.
 */

import type { LiveRefreshReport } from "@/lib/store/live";

/** Cadence while tasks or agents are actually working. */
export const LIVE_ACTIVE_MS = 4000;
/** Cadence when nothing is in flight — the page is still live, just quiet. */
export const LIVE_IDLE_MS = 20000;
/** Every Nth tick also refreshes the slow collections. */
export const LIVE_SLOW_EVERY = 5;
/**
 * Backoff ceiling. Low enough that a workspace recovers within ~15 s of the
 * connection returning (an offline tab also wakes on `online`), high enough
 * that a dead tunnel is not hammered every few seconds.
 */
export const LIVE_MAX_BACKOFF_MS = 15000;

/**
 * Delay before the next poll. `failures` is the number of consecutive failed
 * refreshes (0 = healthy): each one doubles the delay up to the cap, so a
 * workspace that went offline retries at most every 15 s.
 */
export function liveIntervalMs(active: boolean, failures: number): number {
  const base = active ? LIVE_ACTIVE_MS : LIVE_IDLE_MS;
  if (!Number.isFinite(failures) || failures <= 0) return base;
  const grown = base * 2 ** Math.min(Math.trunc(failures), 4);
  return Math.min(Math.max(grown, base), LIVE_MAX_BACKOFF_MS);
}

/** True on ticks that should also refresh the slow collections. */
export function isSlowTick(tick: number): boolean {
  return Number.isInteger(tick) && tick > 0 && tick % LIVE_SLOW_EVERY === 0;
}

/**
 * Merge the per-resource reports of one poll. One failure makes the whole poll
 * a failure (so the indicator and the backoff both react), but data that did
 * apply is never discarded.
 */
export function combineRefreshReports(reports: readonly LiveRefreshReport[]): LiveRefreshReport {
  const failed = reports.find((report) => report.outcome === "error");
  if (failed) return { outcome: "error", error: failed.error ?? "Background refresh failed." };
  if (reports.some((report) => report.outcome === "ok")) return { outcome: "ok" };
  return { outcome: "skipped" };
}

/** Work that a background poll should watch closely. */
export function hasActiveWork(
  tasks: readonly { status: string }[],
  agents: readonly { status: string; hired: boolean }[],
): boolean {
  const taskBusy = tasks.some(
    (task) => task.status === "working" || task.status === "waiting_assets",
  );
  if (taskBusy) return true;
  return agents.some(
    (agent) =>
      agent.hired &&
      (agent.status === "working" || agent.status === "thinking" || agent.status === "reviewing"),
  );
}
