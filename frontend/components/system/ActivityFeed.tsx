"use client";

import { cn } from "@/lib/cn";
import type { ActivityEvent } from "@/lib/domain/types";

/**
 * Read-only activity feed (shared by DEMO and LIVE surfaces).
 *
 * Pure presentational: takes display-ready events (`at` is already a human
 * string in DEMO or a formatted server timestamp in LIVE — see
 * lib/live/datetime.ts). No fetching, no mutations, no store access.
 */
export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) {
    return <p className="py-4 text-center text-[13px] text-ink-400">No activity yet.</p>;
  }
  return (
    <div className="flex flex-col">
      {events.map((e) => (
        <div
          key={e.id}
          className="flex items-start gap-2 border-b border-mist-100 py-2 text-[12.5px] last:border-0 dark:border-white/5"
        >
          <span
            className={cn(
              "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
              e.kind === "approval"
                ? "bg-orange-400"
                : e.kind === "result"
                  ? "bg-emerald-400"
                  : e.kind === "system"
                    ? "bg-violet-400"
                    : "bg-sky-400",
            )}
          />
          <div className="min-w-0">
            <p className="leading-5">
              <span className="font-semibold">{e.agentName}</span>
              <span className="text-ink-400"> · {e.at}</span>
            </p>
            <p className="leading-5 text-ink-700 dark:text-slate-300">{e.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
