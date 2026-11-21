"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Play, Square } from "lucide-react";
import {
  ApiError,
  apiFinalizeBrief,
  apiGetProjectWorkflow,
  type ApiFinalize,
  type ApiProjectWorkflow,
} from "@/lib/api";
import { driverKeyOf, usePlanDriver, type DriverPhase, type DriverRecord } from "@/lib/store/plan-driver";
import { cn } from "@/lib/cn";
import { useLiveRefresh } from "@/lib/live/use-live-refresh";
import { useProjectRun } from "@/lib/live/use-project-run";
import { combineRefreshReports } from "@/lib/live/live-refresh";
import { scopedRunValue } from "@/lib/live/project-run";
import { useLiveApprovals, useLiveTasks } from "@/lib/store/live";
import { ProjectRunActions, ProjectRunStatus } from "@/components/system/ProjectRun";
import type { Agent, Approval, Task } from "@/lib/domain/types";
import type { LiveRefreshReport } from "@/lib/store/live";

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

/** The project page owns one visibility-aware poll for workflow, run and task facts. */
export function useProjectWorkflow(businessId: string, projectId: string, enabled: boolean) {
  const run = useProjectRun(businessId, enabled ? projectId : null);
  const key = JSON.stringify([businessId, projectId]);
  const [snapshot, setWorkflow] = useState<{ key: string; value: ApiProjectWorkflow } | null>(null);
  const workflow = scopedRunValue(snapshot, key);
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const readWorkflow = useCallback(async (): Promise<LiveRefreshReport> => {
    if (!mounted.current || request.current) return { outcome: "skipped" };
    const controller = new AbortController();
    request.current = controller;
    try {
      const next = await apiGetProjectWorkflow(businessId, projectId, { signal: controller.signal });
      if (controller.signal.aborted) return { outcome: "skipped" };
      setWorkflow((current) => scopedRunValue(current, key)?.finalization
        ? current : { key, value: next });
      return { outcome: "ok" };
    } catch {
      if (controller.signal.aborted) return { outcome: "skipped" };
      return { outcome: "error", error: "Workflow data is unavailable." };
    } finally {
      if (request.current === controller) request.current = null;
    }
  }, [businessId, projectId, key, setWorkflow]);
  const refreshRun = run.refresh;
  const refresh = useCallback(() => Promise.all([
    refreshRun(),
    readWorkflow(),
    ...(run.run?.schedulingMode === "managed" ? [
      useLiveTasks.getState().refresh(businessId, projectId),
      useLiveApprovals.getState().refresh(businessId),
    ] : []),
  ]).then(combineRefreshReports), [
    businessId, projectId, readWorkflow, refreshRun, run.run?.schedulingMode,
  ]);
  const [initial, setInitialError] = useState<{ key: string; value: string | null } | null>(null);
  const initialError = scopedRunValue(initial, key);

  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    let cancelled = false;
    const initial = setTimeout(() => {
      if (document.hidden) return;
      void Promise.all([refreshRun(), readWorkflow()]).then((reports) => {
        if (!cancelled) setInitialError({ key, value: combineRefreshReports(reports).error ?? null });
      });
    }, 0);
    return () => {
      cancelled = true;
      mounted.current = false;
      clearTimeout(initial);
      request.current?.abort();
      request.current = null;
    };
  }, [enabled, readWorkflow, refreshRun, key]);

  const { state, activeTaskId } = run.run ?? {};
  const active = state === "running" || state === "waiting_approval"
    || Boolean(activeTaskId && state !== "needs_attention" && !run.run?.reasonCode);
  const sync = useLiveRefresh(enabled && !workflow?.finalization, active, { fast: refresh });
  const saveReport = (result: ApiFinalize) => setWorkflow({
    key,
    value: {
      briefId: result.briefId,
      phase: "finalized",
      message: null,
      finalization: result,
      contract: result.contract,
    },
  });

  return { run, workflow, error: run.error ?? sync.error ?? (workflow ? null : initialError), refresh, saveReport };
}

export function ProjectWorkflowPanel({
  businessId, projectId, execution, tasks, approvals, agents,
}: {
  businessId: string;
  projectId: string;
  execution: ReturnType<typeof useProjectWorkflow>;
  tasks: readonly Task[];
  approvals: readonly Approval[];
  agents: readonly Agent[];
}) {
  const { run, workflow, error, refresh, saveReport } = execution;
  if (workflow?.finalization) return <FinalReportView result={workflow.finalization} />;
  if (run.mode === "unknown" || (run.mode === "manual" && (!workflow || run.error))) {
    return error ? (
      <div className="text-caption text-fg-muted">
        <p role="alert">{error}</p>
        <button className="font-semibold underline" onClick={() => void refresh()}>Retry refresh</button>
      </div>
    ) : <p role="status" className="text-caption text-fg-muted">Loading workflow…</p>;
  }
  if (run.mode === "manual") {
    return (
      <div className="flex flex-col gap-1">
        <PlanDriverPanel businessId={businessId} projectId={projectId}
          workflow={workflow ?? undefined} onSettled={refresh} onFinalized={saveReport} />
        {error && <p role="status" className="text-caption text-warning-fg">{error} Showing last known state.</p>}
      </div>
    );
  }
  if (!run.run) return null;
  const finalizableBrief = workflow?.phase === "awaiting_finalization"
    && workflow.briefId
    && !run.run.activeTaskId && !["running", "waiting_approval"].includes(run.run.state);
  return (
    <section aria-label="Project execution" className="space-y-2 rounded-card border border-line bg-surface-sunken px-3 py-2.5">
      <ProjectRunStatus run={run.run} tasks={tasks} approvals={approvals} agents={agents} stale={!!error} />
      <ProjectRunActions run={run.run} pending={run.pending} disabled={!!run.error}
        issue={(command) => void run.issue(command)} commandError={run.commandError} />
      {error && (
        <p role="status" className="text-caption text-warning-fg">
          {error} <button className="underline" onClick={() => void refresh()}>Refresh</button>
        </p>
      )}
      {finalizableBrief && workflow.briefId && (
        <FinalizeSection businessId={businessId} briefId={workflow.briefId} onFinalized={saveReport} />
      )}
    </section>
  );
}

export function PlanDriverPanel({
  businessId,
  projectId,
  workflow,
  onSettled,
  onFinalized,
}: {
  businessId: string;
  projectId: string;
  workflow?: ApiProjectWorkflow;
  onSettled?: () => Promise<unknown>;
  onFinalized?: (result: ApiFinalize) => void;
}) {
  const key = driverKeyOf(businessId, projectId);
  const record = usePlanDriver((s) => s.drivers[key]);
  const start = usePlanDriver((s) => s.start);
  const stop = usePlanDriver((s) => s.stop);
  const view = describeDriver(record);
  const briefId = workflow?.briefId;
  const canFinalize = workflow?.phase === "awaiting_finalization";
  const passiveMessage = !view.running && workflow?.message;
  const canStart = view.canStart && !canFinalize;

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
      {canStart && (
        <button
          onClick={() => void start(businessId, projectId, briefId ? { briefId } : undefined).then(
            () => onSettled?.(),
          )}
          className="flex h-7 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-2.5 text-[12px] font-semibold text-white hover:opacity-90"
        >
          <Play className="h-3.5 w-3.5" />
          {(record && record.phase !== "idle") || workflow?.phase === "running" ? "Resume" : "Start"}
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
        {(passiveMessage || view.headline) && (
          <p
            className={cn(
              "text-[12px] font-semibold",
              view.tone === "ok" && "text-emerald-700 dark:text-emerald-300",
              view.tone === "bad" && "text-red-600 dark:text-red-300",
              view.tone === "warn" && "text-amber-700 dark:text-amber-300",
              view.tone === "info" && "text-ink-700 dark:text-slate-300",
            )}
          >
            {passiveMessage || view.headline}
          </p>
        )}
        {view.detail && <p className="text-[11.5px] text-ink-500">{view.detail}</p>}
      </div>
      {canFinalize && !view.running && briefId && (
        <FinalizeSection businessId={businessId} briefId={briefId} onFinalized={onFinalized} />
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

const finalizationFlights = new Map<string, { current: Promise<ApiFinalize> | null }>();

export function finalizeProjectBrief(businessId: string, briefId: string): Promise<ApiFinalize> {
  const key = driverKeyOf(businessId, briefId);
  const existing = finalizationFlights.get(key);
  if (existing?.current) return existing.current;
  const slot = { current: null };
  finalizationFlights.set(key, slot);
  const promise = shareFlight(() => apiFinalizeBrief(businessId, briefId), slot);
  const clear = () => {
    if (finalizationFlights.get(key) === slot) finalizationFlights.delete(key);
  };
  void promise.then(clear, clear);
  return promise;
}

/**
 * LIVE brief finalization (Stage 8.5). One POST per generation (ref-guarded
 * against double clicks); the backend is idempotent, so even a raced repeat
 * returns the stored report without new LLM work. Never auto-fires on mount.
 */
export function FinalizeSection({
  businessId,
  briefId,
  onFinalized,
}: {
  businessId: string;
  briefId: string;
  onFinalized?: (result: ApiFinalize) => void;
}) {
  const [state, setState] = useState<FinalizeState>({ phase: "idle", result: null, message: null });
  const flight = useRef<Promise<void> | null>(null);
  const mounted = useRef(false);
  const view = describeFinalize(state);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const finalize = () =>
    shareFlight(async () => {
      setState({ phase: "pending", result: null, message: null });
      try {
        const result = await finalizeProjectBrief(businessId, briefId);
        if (mounted.current) {
          setState({ phase: "done", result, message: null });
          onFinalized?.(result);
        }
      } catch (err) {
        if (!mounted.current) return;
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
        Ledger budget ${costs.budget} · AI spend ${costs.ai_spent} / ${costs.ai_budget}
        {costs.ai_estimated && costs.ai_estimated !== "0.00" && costs.ai_estimated !== "0"
          ? ` · estimated $${costs.ai_estimated} (not confirmed)`
          : ""}
        . {costs.note}
      </p>
    </div>
  );
}
