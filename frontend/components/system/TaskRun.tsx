"use client";

import { Loader2, Play } from "lucide-react";
import { useLiveExecution, type TaskRun } from "@/lib/store/execution";
import { Button } from "@/components/system/controls";
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
      className="text-micro text-fg-subtle"
    >
      server-run · provider unverified
    </span>
  );
}

function RunMessage({ tone, children }: { tone: "ok" | "bad" | "warn" | "info"; children: React.ReactNode }) {
  return (
    <p
      className={cn(
        "text-label leading-5",
        tone === "ok" && "text-success-fg",
        tone === "bad" && "text-danger-fg",
        tone === "warn" && "text-warning-fg",
        tone === "info" && "text-fg-muted",
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
          <span className="flex h-7 items-center gap-1.5 rounded-full bg-info-soft px-2.5 text-caption font-semibold text-info-fg">
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> Running…
          </span>
          {view.showStop && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => stopWaiting(businessId, projectId, taskId)}
              title="Stop waiting for the answer. The server may still be running the task — this does not cancel it."
              className="h-7 px-2.5 text-caption"
            >
              Stop waiting
            </Button>
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
            <Button
              size="sm"
              onClick={() => void reconcile(businessId, projectId, taskId)}
              className="h-7 px-2.5 text-caption"
            >
              {view.kind === "uncertain" ? "Re-check result" : "Re-check"}
            </Button>
          )}
          {view.showRunAgain && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void runTask(businessId, projectId, taskId)}
              className="h-7 px-2.5 text-caption"
            >
              Run again
            </Button>
          )}
          <ProviderTag />
        </div>
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          onClick={() => void runTask(businessId, projectId, taskId)}
          className="h-7 gap-1 px-2.5 text-caption"
        >
          <Play aria-hidden className="h-3.5 w-3.5" /> Run
        </Button>
      </div>
      <ProviderTag />
    </div>
  );
}
