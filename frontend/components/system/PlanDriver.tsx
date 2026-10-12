"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Play, Square } from "lucide-react";
import {
  ApiError,
  apiFinalizeBrief,
  type ApiFinalize,
} from "@/lib/api";
import { driverKeyOf, usePlanDriver, type DriverPhase, type DriverRecord } from "@/lib/store/plan-driver";
import { cn } from "@/lib/cn";

export type DriverTone = "ok" | "bad" | "warn" | "info";

export interface DriverView {
  running: boolean;
  canStart: boolean;
  canStop: boolean;
  headline: string | null;
  detail: string | null;
  tone: DriverTone;
}

/**
 * Pure presentation mapping for a driver record (unit-tested; the component
 * below only renders it).
 */
export function describeDriver(record: DriverRecord | undefined): DriverView {
  const phase: DriverPhase = record?.phase ?? "idle";
  const progress =
    record && record.total > 0 ? ` (${record.completed}/${record.total} tasks complete)` : "";
  switch (phase) {
    case "running":
    case "loading":
      return {
        running: true,
        canStart: false,
        canStop: true,
        headline: record?.message ?? "Working…",
        detail: "The browser is driving — close or stop and the server keeps only what already started.",
        tone: "info",
      };
    case "waiting_approval":
      return {
        running: true,
        canStart: false,
        canStop: true,
        headline: record?.message ?? "Waiting for approval.",
        detail: "Decide it on the Approvals page — the driver resumes on its own.",
        tone: "warn",
      };
    case "awaiting_finalization":
      return {
        running: false,
        canStart: false,
        canStop: false,
        headline: record?.message ?? `Done${progress}.`,
        detail: "All tasks reached a terminal state.",
        tone: "ok",
      };
    case "blocked":
      return {
        running: false,
        canStart: true,
        canStop: false,
        headline: record?.message ?? "Blocked.",
        detail: "Fix the blocker, then resume — completed tasks are never re-run.",
        tone: "warn",
      };
    case "failed":
      return {
        running: false,
        canStart: true,
        canStop: false,
        headline: record?.message ?? "A task failed.",
        detail: "Failures are never retried automatically — resume when ready.",
        tone: "bad",
      };
    case "stopped":
      return {
        running: false,
        canStart: true,
        canStop: false,
        headline: record?.message ?? "Driver stopped.",
        detail: null,
        tone: "info",
      };
    case "error":
      return {
        running: false,
        canStart: true,
        canStop: false,
        headline: record?.message ?? "Couldn't drive this project.",
        detail: null,
        tone: "bad",
      };
    case "ready":
      return {
        running: true,
        canStart: false,
        canStop: true,
        headline: record?.message ?? "Starting…",
        detail: null,
        tone: "info",
      };
    case "idle":
    default:
      return {
        running: false,
        canStart: true,
        canStop: false,
        headline: null,
        detail: "Runs the planned tasks in dependency order through the server. Stopping halts this driver only — a started server run keeps going.",
        tone: "info",
      };
  }
}

/**
 * LIVE plan-driver panel (Stage 8.4). Rendered only in LIVE branches — never
 * in DEMO (the driver itself also refuses non-member contexts).
 *
 * - One driver per mounted instance: Start while active is a no-op (shared
 *   promise), unmount stops the loop but keeps the record for resume.
 * - "Stop" halts the client loop and the client wait; the label says the
 *   server may still be running the task.
 */
export function PlanDriverPanel({
  businessId,
  projectId,
  briefId,
}: {
  businessId: string;
  projectId: string;
  briefId?: string;
}) {
  const key = driverKeyOf(businessId, projectId);
  const record = usePlanDriver((s) => s.drivers[key]);
  const start = usePlanDriver((s) => s.start);
  const stop = usePlanDriver((s) => s.stop);
  const view = describeDriver(record);

  useEffect(() => {
    return () => {
      usePlanDriver.getState().stop(businessId, projectId);
    };
  }, [businessId, projectId]);

  return (
    <div
      data-testid="plan-driver"
      className="flex flex-wrap items-center gap-2 rounded-2xl border border-mist-200 bg-mist-50 px-3 py-2 dark:border-white/10 dark:bg-white/5"
    >
      {view.canStart && (
        <button
          onClick={() => void start(businessId, projectId, briefId ? { briefId } : undefined)}
          className="flex h-7 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-2.5 text-[12px] font-semibold text-white hover:opacity-90"
        >
          <Play className="h-3.5 w-3.5" />
          {record && record.phase !== "idle" ? "Resume" : "Start"}
        </button>
      )}
      {view.canStop && (
        <button
          onClick={() => stop(businessId, projectId)}
          title="Stop this driver. A server run already started keeps going — this does not cancel it."
          className="flex h-7 items-center gap-1.5 rounded-lg border border-mist-200 px-2.5 text-[12px] font-medium text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:hover:bg-white/5"
        >
          <Square className="h-3.5 w-3.5" /> Stop
        </button>
      )}
      {view.running && <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-500" />}
      <div className="min-w-0">
        {view.headline && (
          <p
            className={cn(
              "text-[12px] font-semibold",
              view.tone === "ok" && "text-emerald-700 dark:text-emerald-300",
              view.tone === "bad" && "text-red-600 dark:text-red-300",
              view.tone === "warn" && "text-amber-700 dark:text-amber-300",
              view.tone === "info" && "text-ink-700 dark:text-slate-300",
            )}
          >
            {view.headline}
          </p>
        )}
        {view.detail && <p className="text-[11.5px] text-ink-500">{view.detail}</p>}
      </div>
      {record?.phase === "awaiting_finalization" && briefId && (
        <FinalizeSection businessId={businessId} briefId={briefId} />
      )}
    </div>
  );
}

type FinalizePhase = "idle" | "pending" | "done" | "error";

interface FinalizeState {
  phase: FinalizePhase;
  result: ApiFinalize | null;
  message: string | null;
}

export interface FinalizeView {
  buttonLabel: string;
  pending: boolean;
  showButton: boolean;
  showRetry: boolean;
  headline: string | null;
  tone: DriverTone;
}

/** Pure presentation mapping for finalize states (unit-tested). */
export function describeFinalize(state: FinalizeState): FinalizeView {
  switch (state.phase) {
    case "pending":
      return { buttonLabel: "Finalizing…", pending: true, showButton: true, showRetry: false, headline: null, tone: "info" };
    case "error":
      return { buttonLabel: "Retry finalization", pending: false, showButton: true, showRetry: true, headline: state.message ?? "Finalization failed.", tone: "bad" };
    case "done":
      return { buttonLabel: "", pending: false, showButton: false, showRetry: false, headline: null, tone: "ok" };
    case "idle":
    default:
      return { buttonLabel: "Finalize brief", pending: false, showButton: true, showRetry: false, headline: null, tone: "info" };
  }
}

/**
 * Share one flight across concurrent calls; the slot clears when settled so
 * later retries run again. Unit-tested; used for finalize double-clicks
 * (backend idempotency is the correctness backstop).
 */
export function shareFlight<R>(
  fn: () => Promise<R>,
  slot: { current: Promise<R> | null },
): Promise<R> {
  if (slot.current) return slot.current;
  const run = fn();
  slot.current = run;
  const clear = () => {
    if (slot.current === run) slot.current = null;
  };
  run.then(clear, clear);
  return run;
}

/**
 * LIVE brief finalization (Stage 8.5). One POST per generation (ref-guarded
 * against double clicks); the backend is idempotent, so even a raced repeat
 * returns the stored report without new LLM work. Never auto-fires on mount.
 */
export function FinalizeSection({
  businessId,
  briefId,
}: {
  businessId: string;
  briefId: string;
}) {
  const [state, setState] = useState<FinalizeState>({ phase: "idle", result: null, message: null });
  const flight = useRef<Promise<void> | null>(null);
  const view = describeFinalize(state);

  const finalize = () =>
    shareFlight(async () => {
      setState({ phase: "pending", result: null, message: null });
      try {
        const result = await apiFinalizeBrief(businessId, briefId);
        setState({ phase: "done", result, message: null });
      } catch (err) {
        setState({
          phase: "error",
          result: null,
          message: err instanceof ApiError ? err.message : "Finalization failed.",
        });
      }
    }, flight);

  if (state.phase === "done" && state.result) {
    return <FinalReportView result={state.result} />;
  }

  return (
    <div data-testid="finalize-section" className="flex w-full flex-col gap-1.5">
      {view.showRetry && (
        <p className="text-[11.5px] font-medium text-red-600 dark:text-red-300">{view.headline}</p>
      )}
      <div>
        <button
          onClick={() => void finalize()}
          disabled={view.pending}
          className="flex h-7 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-2.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          {view.pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {view.buttonLabel}
        </button>
      </div>
      <p className="text-[11px] text-ink-400">
        Writes the persisted final report once — repeats return the stored result.
      </p>
    </div>
  );
}

/** Persisted final report (verified rows only; recommendations stay separate). */
export function FinalReportView({ result }: { result: ApiFinalize }) {
  const { report, learnings, costs } = result;
  return (
    <div data-testid="final-report" className="flex w-full flex-col gap-2">
      <p className="text-[12.5px] font-bold">Final report</p>
      <p className="text-[12px] text-ink-500">{report.goal}</p>
      {report.completed_tasks.length > 0 && (
        <div>
          <p className="text-[12px] font-semibold text-emerald-700 dark:text-emerald-300">
            Completed ({report.completed_tasks.length})
          </p>
          {report.completed_tasks.map((t) => (
            <p key={t.id} className="text-[12px] text-ink-700 dark:text-slate-300">
              {t.title} — {t.summary}
            </p>
          ))}
        </div>
      )}
      {report.failed_tasks.length > 0 && (
        <div>
          <p className="text-[12px] font-semibold text-red-600 dark:text-red-300">
            Failed ({report.failed_tasks.length})
          </p>
          {report.failed_tasks.map((t) => (
            <p key={t.id} className="text-[12px] text-ink-700 dark:text-slate-300">
              {t.title} — {t.reason}
            </p>
          ))}
        </div>
      )}
      {report.limitations.length > 0 && (
        <p className="text-[11.5px] text-ink-500">Limits: {report.limitations.join("; ")}</p>
      )}
      {report.recommendations.length > 0 && (
        <p className="text-[11.5px] text-ink-500">
          Recommendations (not verified results): {report.recommendations.join("; ")}
        </p>
      )}
      {learnings.length > 0 && (
        <div>
          <p className="text-[12px] font-semibold">Learnings saved to Brain ({learnings.length})</p>
          {learnings.map((l) => (
            <p key={l.id} className="text-[12px] text-ink-700 dark:text-slate-300">
              {l.title} — {l.body}
            </p>
          ))}
        </div>
      )}
      <p className="text-[11px] text-ink-400">
        Ledger budget ${costs.budget} · AI ${costs.ai_spent} / ${costs.ai_budget} (configured
        values, not measured). {costs.note}
      </p>
    </div>
  );
}
