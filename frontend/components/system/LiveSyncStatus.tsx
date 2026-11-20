"use client";

import { cn } from "@/lib/cn";

/**
 * Non-blocking freshness line for a LIVE screen.
 *
 * It reports the background refresh rather than the page load: a failed poll
 * keeps the last good data on screen and says so, and a successful one names
 * the time it landed. The row is always rendered at one line height so
 * switching between "updating", "live" and "reconnecting" cannot shift the
 * layout below it.
 */
export function LiveSyncStatus({
  refreshing,
  lastSyncedAt,
  error,
  className,
}: {
  refreshing: boolean;
  lastSyncedAt: number | null;
  error: string | null;
  className?: string;
}) {
  const at = lastSyncedAt
    ? new Date(lastSyncedAt).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : null;

  return (
    <p
      role="status"
      aria-live="polite"
      className={cn(
        "flex min-h-5 flex-wrap items-center gap-1.5 text-label leading-5",
        error ? "text-warning-fg" : "text-fg-subtle",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full motion-reduce:animate-none",
          error
            ? "bg-warning-fg"
            : refreshing
              ? "animate-pulse-dot bg-brand"
              : "bg-success-fg",
        )}
      />
      {error ? (
        <>
          <span className="font-semibold">Live updates paused.</span>
          <span className="text-fg-muted">{error}</span>
          <span>Showing the last data loaded; retrying automatically.</span>
        </>
      ) : refreshing ? (
        <span>Updating…</span>
      ) : at ? (
        <span>Live · updated {at}</span>
      ) : (
        <span>Live updates on</span>
      )}
    </p>
  );
}
