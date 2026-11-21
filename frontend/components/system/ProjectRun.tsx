"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { Badge, ProgressBar } from "@/components/system/badges";
import { Button } from "@/components/system/controls";
import type { ApiProjectRun, ProjectRunAction, ProjectRunCommand, ProjectRunState } from "@/lib/api";
import type { Agent, Approval, Task } from "@/lib/domain/types";
import { retryTarget, summarizeRunTasks } from "@/lib/live/project-run";

const states: Record<ProjectRunState, {
  label: string; tone: "neutral" | "info" | "warning" | "danger" | "success";
}> = {
  idle: { label: "Idle", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  waiting_approval: { label: "Waiting for approval", tone: "warning" },
  paused: { label: "Paused", tone: "warning" },
  needs_attention: { label: "Needs attention", tone: "danger" },
  completed: { label: "Completed", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

const reasons: Record<string, string> = {
  approval_pending: "A task is waiting for a human decision.",
  approval_rejected: "A rejected approval is blocking work.",
  external_action_pending: "Waiting for the external action to settle.",
  external_action_unknown: "The external action outcome is uncertain.",
  ambiguous_execution: "The previous attempt needs reconciliation.",
  budget_exceeded: "The project or business AI budget would be exceeded.",
  budget_unpriced: "A usage price is required under the current AI budget policy.",
  access_revoked: "The run authorizer no longer has access.",
  task_failed: "A task failed. Review its result before retrying.",
  retry_required: "Review the previous attempt before authorizing a retry.",
  retry_authorized: "A retry is authorized and waiting for the worker.",
  deadline_exceeded: "The task passed its recovery deadline.",
};

export function ProjectRunStatus({ run, tasks, approvals, agents, stale = false }: {
  run: ApiProjectRun;
  tasks: readonly Task[];
  approvals: readonly Approval[];
  agents: readonly Agent[];
  stale?: boolean;
}) {
  const counts = useMemo(() => summarizeRunTasks(tasks, approvals), [tasks, approvals]);
  const active = tasks.find((task) => task.id === run.activeTaskId);
  const agent = agents.find((item) => item.id === active?.ownerAgentId);
  const state = states[run.state];
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption">
        <Badge tone={state.tone}>{state.label}</Badge>
        {stale && <span className="text-warning-fg">Showing last known status</span>}
        {run.activeTaskId && (
          <span className="min-w-0 font-medium text-fg">
            {active?.title ?? "Active task"}{agent ? ` · ${agent.name}` : ""}
          </span>
        )}
        {counts.total > 0 && (
          <span className="tabular-nums text-fg-muted">{counts.completed}/{counts.total} tasks complete</span>
        )}
      </div>
      {counts.total > 0 && (
        <ProgressBar
          value={100 * counts.completed / counts.total}
          label="Task completion"
          className="max-w-48"
        />
      )}
      <div className="flex flex-wrap gap-x-3 text-caption text-fg-muted">
        {counts.waiting > 0 && <Link href="/approvals" className="text-brand underline">
          {counts.waiting} waiting for approval
        </Link>}
        {counts.failed > 0 && <span>{counts.failed} failed</span>}
        {counts.blocked > 0 && <span>{counts.blocked} blocked</span>}
      </div>
      {run.reasonCode && (
        <p className="text-caption text-fg-muted">
          {reasons[run.reasonCode] ?? run.reasonCode.replaceAll("_", " ")}
        </p>
      )}
      {run.state === "waiting_approval" && counts.waiting === 0 && run.reasonCode !== "external_action_pending" && (
        <Link href="/approvals" className="text-caption text-brand underline">Open approvals</Link>
      )}
      {run.activeTaskId && run.recoveredTaskGeneration == null &&
        (run.state === "paused" || run.state === "cancelled") && (
        <p className="text-caption text-fg-muted">A task already in progress may still finish.</p>
      )}
    </div>
  );
}

const controls = [
  { action: "start", label: "Start" },
  { action: "pause", label: "Pause" },
  { action: "resume", label: "Resume" },
  { action: "retry", label: "Retry task" },
  { action: "cancel", label: "Cancel run" },
] as const;

export function ProjectRunControls({ run, pending, disabled, confirmation, onConfirm, onDismiss, issue }: {
  run: ApiProjectRun;
  pending: ProjectRunAction | null;
  disabled: boolean;
  confirmation: "cancel" | "assume" | null;
  onConfirm: (action: "cancel" | "assume") => void;
  onDismiss: () => void;
  issue: (command: ProjectRunCommand) => void;
}) {
  const confirmId = useId();
  const retry = retryTarget(run);
  const locked = disabled || pending !== null;
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        {controls.map(({ action, label }) => run.allowedActions.includes(action) && (
          <Button key={action} size="sm" variant={action === "start" ? "primary" : "outline"}
            disabled={locked || confirmation !== null || (action === "retry" && retry === null)}
            loading={pending === action}
            onClick={() => {
              if (action === "cancel") onConfirm("cancel");
              else if (action === "retry") {
                if (retry) issue({ action, ...retry });
              } else issue({ action });
            }}
          >{label}</Button>
        ))}
      </div>
      {confirmation && (
        <div role="group" aria-labelledby={confirmId} className="rounded-control border border-line bg-surface p-2">
          <p id={confirmId} className="mb-2 text-caption text-fg">
            {confirmation === "cancel"
              ? "Cancel this run? An active task may still finish."
              : "Assume responsibility for this run and resume execution?"}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant={confirmation === "cancel" ? "destructive" : "primary"}
              disabled={locked} onClick={() => {
                issue(confirmation === "cancel" ? { action: "cancel" }
                  : { action: "resume", assumeResponsibility: true });
                onDismiss();
              }}
            >{confirmation === "cancel" ? "Confirm cancellation" : "Assume and resume"}</Button>
            <Button size="sm" variant="ghost" onClick={onDismiss}>Go back</Button>
          </div>
        </div>
      )}
      {run.allowedActions.includes("retry") && retry === null && (
        <p className="text-caption text-fg-muted">This run does not expose a retryable task generation.</p>
      )}
    </>
  );
}

export function ProjectRunActions({
  run, pending, disabled, commandError, issue,
}: Omit<Parameters<typeof ProjectRunControls>[0], "confirmation" | "onConfirm" | "onDismiss"> & {
  commandError: { message: string; assumeResponsibility: boolean } | null;
}) {
  const [confirm, setConfirm] = useState<{ action: "cancel" | "assume"; version: number } | null>(null);
  const currentConfirmation = confirm?.version === run.version
    && run.allowedActions.includes(confirm.action === "cancel" ? "cancel" : "resume")
    ? confirm.action : null;
  return (
    <>
      <ProjectRunControls run={run} pending={pending} disabled={disabled} issue={issue}
        confirmation={currentConfirmation}
        onConfirm={(action) => setConfirm({ action, version: run.version })}
        onDismiss={() => setConfirm(null)}
      />
      {commandError && (
        <div role="alert" className="text-caption text-danger-fg">
          <p>{commandError.message}</p>
          {commandError.assumeResponsibility && run.allowedActions.includes("resume") && (
            <Button size="sm" variant="outline" disabled={disabled || pending !== null}
              onClick={() => setConfirm({ action: "assume", version: run.version })}
            >Take responsibility</Button>
          )}
        </div>
      )}
    </>
  );
}
