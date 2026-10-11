"use client";

import { Loader2, Play } from "lucide-react";
import { useLiveExecution, type TaskRun } from "@/lib/store/execution";
import { cn } from "@/lib/cn";

export type RunTone = "ok" | "bad" | "warn" | "info";

export interface RunView {
  /** Which control set to render. */
  kind: "run" | "running" | "uncertain" | "result";
  message: string | null;
  tone: RunTone;
  showRecheck: boolean;
  showRunAgain: boolean;
  showStop: boolean;
}

/**
 * Pure presentation mapping for a task run record (unit-tested; the thin
 * component below only renders it — React Server rendering cannot observe
 * client store state, so branches are pinned here instead of via SSR).
 */
export function describeRun(run: TaskRun | undefined): RunView {
  const phase = run?.phase ?? "idle";
  if (phase === "running") {
    return { kind: "running", message: null, tone: "info", showRecheck: false, showRunAgain: false, showStop: true };
  }
  if (phase === "uncertain") {
    return {
      kind: "uncertain",
      message: run?.message ?? "Outcome unknown.",
      tone: "warn",
      showRecheck: true,
      showRunAgain: false,
      showStop: false,
    };
  }
  if (phase === "done") {
    const tone: RunTone =
      run?.outcome === "completed"
        ? "ok"
        : run?.errorCode === "approval_required"
          ? "warn"
          : run?.outcome === "failed" || run?.errorCode
            ? "bad"
            : "info";
    return {
      kind: "result",
      message: run?.message ?? null,
      tone,
      showRecheck: run?.outcome === "running",
      showRunAgain: run?.outcome !== "running" && run?.outcome !== "not_found",
      showStop: false,
    };
  }
  return { kind: "run", message: null, tone: "info", showRecheck: false, showRunAgain: false, showStop: false };
}

function ProviderTag() {
  return (
    <span
      title="Results are produced server-side. This client cannot verify which LLM provider ran the task — the server default is the offline mock (APP_LLM_PROVIDER). Treat results as a pipeline check, not confirmed live AI."
      className="text-[10.5px] text-ink-400 dark:text-slate-500"
    >
      server-run · provider unverified
    </span>
  );
}

function RunMessage({ tone, children }: { tone: "ok" | "bad" | "warn" | "info"; children: React.ReactNode }) {
  return (
    <p
      className={cn(
        "text-[11.5px] leading-5",
        tone === "ok" && "text-emerald-700 dark:text-emerald-300",
        tone === "bad" && "text-red-600 dark:text-red-300",
        tone === "warn" && "text-amber-700 dark:text-amber-300",
        tone === "info" && "text-ink-500 dark:text-slate-400",
      )}
    >
      {children}
    </p>
  );
}

/**
 * LIVE per-task run control (Stage 7.4). Rendered only in LIVE branches —
 * never in DEMO (the runner itself also refuses non-member contexts).
 *
 * - One POST per generation: disabled while running + store-level flight
 *   dedupe survive double clicks, StrictMode, and remounts.
 * - "Stop waiting" aborts the client wait only; the label says so and the
 *   record goes uncertain, never "cancelled".
 */
export function TaskRunButton({
  businessId,
  projectId,
  taskId,
}: {
  businessId: string;
  projectId: string;
  taskId: string;
}) {
  const key = `${businessId}/${projectId}/${taskId}`;
  const run = useLiveExecution((s) => s.runs[key]);
  const runTask = useLiveExecution((s) => s.run);
  const reconcile = useLiveExecution((s) => s.reconcile);
  const stopWaiting = useLiveExecution((s) => s.stopWaiting);
  const view = describeRun(run);

  if (view.kind === "running") {
    return (
      <div className="mt-2 flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="flex h-7 items-center gap-1.5 rounded-lg bg-brand-500/10 px-2.5 text-[12px] font-semibold text-brand-600 dark:text-sky-300">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…
          </span>
          {view.showStop && (
            <button
              onClick={() => stopWaiting(businessId, projectId, taskId)}
              title="Stop waiting for the answer. The server may still be running the task — this does not cancel it."
              className="h-7 rounded-lg border border-mist-200 px-2.5 text-[12px] font-medium text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:hover:bg-white/5"
            >
              Stop waiting
            </button>
          )}
        </div>
        <ProviderTag />
      </div>
    );
  }

  if (view.kind === "uncertain" || view.kind === "result") {
    return (
      <div className="mt-2 flex flex-col gap-1.5">
        <RunMessage tone={view.tone}>{view.message}</RunMessage>
        <div className="flex items-center gap-2">
          {view.showRecheck && (
            <button
              onClick={() => void reconcile(businessId, projectId, taskId)}
              className="h-7 rounded-lg bg-brand-500 px-2.5 text-[12px] font-semibold text-white hover:opacity-90"
            >
              {view.kind === "uncertain" ? "Re-check result" : "Re-check"}
            </button>
          )}
          {view.showRunAgain && (
            <button
              onClick={() => void runTask(businessId, projectId, taskId)}
              className="h-7 rounded-lg border border-mist-200 px-2.5 text-[12px] font-medium text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:hover:bg-white/5"
            >
              Run again
            </button>
          )}
          <ProviderTag />
        </div>
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <button
          onClick={() => void runTask(businessId, projectId, taskId)}
          className="flex h-7 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-2.5 text-[12px] font-semibold text-white hover:opacity-90"
        >
          <Play className="h-3.5 w-3.5" /> Run
        </button>
      </div>
      <ProviderTag />
    </div>
  );
}
