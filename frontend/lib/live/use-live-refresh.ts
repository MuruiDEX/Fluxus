"use client";

/**
 * One visibility-aware poller per LIVE screen.
 *
 * - Exactly one timer per mount; callbacks are read through refs so a data
 *   change never tears down and re-creates the loop (no duplicate timers, no
 *   request storms while React re-renders).
 * - Paused while the document is hidden; an immediate catch-up refresh runs on
 *   `visibilitychange` and on the browser's `online` event, which is how a
 *   reconnect is picked up without a reload.
 * - Refreshes are skipped by the stores while a load or another refresh owns
 *   the slice, so a slow connection cannot pile requests up.
 * - Failures back off (see `liveIntervalMs`) and are surfaced as a warning
 *   while the last good data stays on screen.
 */

import { useEffect, useRef, useState } from "react";
import type { LiveRefreshReport } from "@/lib/store/live";
import { isSlowTick, liveIntervalMs } from "@/lib/live/live-refresh";

export interface LiveRefreshTasks {
  /** The state the screen is actually showing (tasks, agents, approvals). */
  fast: () => Promise<LiveRefreshReport>;
  /** Heavier collections, refreshed on every Nth tick. */
  slow?: () => Promise<LiveRefreshReport>;
}

export interface LiveRefreshState {
  refreshing: boolean;
  lastSyncedAt: number | null;
  error: string | null;
}

const INITIAL: LiveRefreshState = { refreshing: false, lastSyncedAt: null, error: null };

export function useLiveRefresh(
  enabled: boolean,
  active: boolean,
  tasks: LiveRefreshTasks,
): LiveRefreshState {
  const [state, setState] = useState<LiveRefreshState>(INITIAL);
  const tasksRef = useRef(tasks);
  const activeRef = useRef(active);

  useEffect(() => {
    tasksRef.current = tasks;
  });
  useEffect(() => {
    activeRef.current = active;
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let tick = 0;

    function schedule(ms: number) {
      if (cancelled) return;
      timer = setTimeout(() => void run(), ms);
    }

    async function run(): Promise<void> {
      timer = null;
      if (cancelled) return;
      // A hidden tab has nothing to render; skip the request and try again on
      // the next tick (a visibility change wakes the loop immediately).
      if (typeof document !== "undefined" && document.hidden) {
        schedule(liveIntervalMs(activeRef.current, failures));
        return;
      }
      setState((s) => (s.refreshing ? s : { ...s, refreshing: true }));
      let report: LiveRefreshReport;
      try {
        report = await tasksRef.current.fast();
        tick += 1;
        if (report.outcome !== "error" && isSlowTick(tick)) {
          const slow = tasksRef.current.slow;
          if (slow) {
            const slowReport = await slow();
            if (slowReport.outcome === "error") report = slowReport;
          }
        }
      } catch (err) {
        report = {
          outcome: "error",
          error: err instanceof Error ? err.message : "Background refresh failed.",
        };
      }
      if (cancelled) return;
      if (report.outcome === "error") failures += 1;
      else if (report.outcome === "ok") failures = 0;
      setState((s) => ({
        refreshing: false,
        lastSyncedAt: report.outcome === "ok" ? Date.now() : s.lastSyncedAt,
        error:
          report.outcome === "error"
            ? (report.error ?? "Background refresh failed.")
            : report.outcome === "ok"
              ? null
              : s.error,
      }));
      schedule(liveIntervalMs(activeRef.current, failures));
    }

    function wake(): void {
      if (cancelled) return;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      void run();
    }

    function onVisibility(): void {
      if (typeof document !== "undefined" && !document.hidden) wake();
    }

    function onOnline(): void {
      wake();
    }

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);
    schedule(liveIntervalMs(activeRef.current, failures));

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
    };
  }, [enabled]);

  return state;
}
