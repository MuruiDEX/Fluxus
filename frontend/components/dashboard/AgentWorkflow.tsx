"use client";

import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleAlert,
  Clock,
  Play,
  Target,
  Users,
} from "lucide-react";
import { AgentStatusBadge, Avatar, Badge, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card } from "@/components/system/ui";
import { cn } from "@/lib/cn";
import type { Agent, Approval, Project, Task } from "@/lib/domain/types";

type NodeState = "active" | "waiting" | "complete" | "idle";

const STATE_META: Record<NodeState, { label: string; chip: string; icon: string }> = {
  active: {
    label: "Active",
    chip: "border-brand/25 bg-brand-soft text-brand",
    icon: "bg-gradient-to-br from-brand-solid to-accent text-on-brand shadow-card",
  },
  waiting: {
    label: "Waiting",
    chip: "border-warning-line bg-warning-soft text-warning-fg",
    icon: "bg-warning-soft text-warning-fg",
  },
  complete: {
    label: "Complete",
    chip: "border-success-line bg-success-soft text-success-fg",
    icon: "bg-success-soft text-success-fg",
  },
  idle: {
    label: "Idle",
    chip: "border-line bg-surface-sunken text-fg-subtle",
    icon: "bg-surface-sunken text-fg-subtle",
  },
};

function stateShell(state: NodeState) {
  switch (state) {
    case "active":
      return "border-brand/30 bg-brand-soft/25";
    case "waiting":
      return "border-warning-line/80 bg-warning-soft/20";
    case "complete":
      return "border-success-line/70 bg-success-soft/20";
    default:
      return "border-line bg-surface/60";
  }
}

function Connector({ active }: { active: boolean }) {
  return (
    <div aria-hidden="true" className="hidden min-w-5 max-w-14 flex-1 items-center gap-1 lg:flex">
      <span className="flux-connector flex-1" data-active={active ? "true" : "false"} />
      <ChevronRight
        className={cn(
          "h-3.5 w-3.5 shrink-0 transition-colors",
          active ? "text-accent" : "text-fg-subtle",
        )}
      />
    </div>
  );
}

function NodeShell({
  index,
  title,
  icon: Icon,
  state,
  children,
  className,
  dataStage,
}: {
  index: number;
  title: string;
  icon: typeof Target;
  state: NodeState;
  children: React.ReactNode;
  className?: string;
  dataStage: string;
}) {
  const meta = STATE_META[state];
  return (
    <li
      data-workflow-stage={dataStage}
      className={cn(
        "flux-node relative flex min-w-0 flex-col rounded-card border p-3",
        stateShell(state),
        className,
      )}
      data-state={state}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-surface/80 px-1 text-micro font-bold tabular-nums text-fg-muted ring-1 ring-inset ring-line">
          {String(index).padStart(2, "0")}
        </span>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wide",
            meta.chip,
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-1 w-1 rounded-full bg-current",
              state === "active" && "animate-pulse-dot",
            )}
          />
          {meta.label}
        </span>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <span
          className={cn(
            "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-[0.5rem]",
            meta.icon,
          )}
        >
          <Icon aria-hidden="true" className="h-3.5 w-3.5" />
        </span>
        <p className="min-w-0 truncate text-micro font-bold uppercase tracking-[0.12em] text-fg-subtle">
          {title}
        </p>
      </div>

      <div className="mt-2 flex flex-1 flex-col text-caption text-fg">{children}</div>
    </li>
  );
}

function agentTaskState(status: Task["status"] | undefined): NodeState {
  if (!status) return "idle";
  if (status === "working" || status === "waiting_assets") return "active";
  if (status === "waiting_approval" || status === "waiting_dependency" || status === "not_started") {
    return "waiting";
  }
  if (status === "completed") return "complete";
  return "idle";
}

/**
 * Horizontal execution map: User goal → Team lead → parallel agents →
 * approval → final result.
 *
 * Every state is derived from the supplied project, tasks, agents and
 * approvals. Nothing is invented and the component never starts work. Agent
 * rows are expandable `<details>` elements so the visualization stays calm
 * while still exposing task details.
 */
export function AgentWorkflow({
  project,
  tasks,
  agents,
  approvals,
  mode,
}: {
  project: Project | null;
  tasks: Task[];
  agents: Agent[];
  approvals: Approval[];
  mode: "demo" | "live";
}) {
  const hired = agents.filter((agent) => agent.hired);
  const lead =
    hired.find((agent) => /team lead|lead/i.test(agent.role)) ??
    (project ? hired.find((agent) => project.teamIds.includes(agent.id)) : undefined) ??
    hired[0];

  const activeTasks = tasks.filter((task) =>
    ["working", "waiting_dependency", "waiting_assets", "waiting_approval", "not_started"].includes(
      task.status,
    ),
  );
  const completed = tasks.filter((task) => task.status === "completed");
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const failed = tasks.filter((task) => task.status === "failed");

  const ownerIds = Array.from(new Set(activeTasks.map((task) => task.ownerAgentId)));
  const parallelAgents = ownerIds
    .map((id) => hired.find((agent) => agent.id === id))
    .filter((agent): agent is Agent => Boolean(agent))
    .slice(0, 4);

  const goalState: NodeState = !project
    ? "idle"
    : project.status === "completed"
      ? "complete"
      : project.status === "in_progress" || project.status === "review"
        ? "active"
        : "waiting";
  const leadState: NodeState = lead
    ? lead.status === "working" || lead.status === "thinking" || lead.status === "reviewing"
      ? "active"
      : "waiting"
    : "idle";
  const agentsState: NodeState =
    parallelAgents.length === 0
      ? "idle"
      : parallelAgents.some((a) => a.status === "working")
        ? "active"
        : "waiting";
  const approvalState: NodeState =
    pendingApprovals.length > 0 ? "waiting" : completed.length > 0 ? "complete" : "idle";
  const resultState: NodeState =
    !project || tasks.length === 0
      ? "idle"
      : failed.length > 0
        ? "waiting"
        : tasks.every((task) => task.status === "completed")
          ? "complete"
          : "active";
  const resultPercent =
    project?.progress ??
    (tasks.length === 0 ? 0 : Math.round((completed.length / tasks.length) * 100));

  return (
    <section aria-labelledby="agent-workflow-title">
      <Card className="flux-panel relative overflow-hidden p-4 sm:p-5">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-accent/10 blur-3xl"
        />
        <span
          aria-hidden="true"
          className="flux-grid-overlay pointer-events-none absolute inset-0 opacity-25"
        />
        <div className="relative flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2
              id="agent-workflow-title"
              className="flex items-center gap-2 text-heading-sm font-bold tracking-tight text-fg"
            >
              <span
                aria-hidden="true"
                className="h-3.5 w-[2.5px] shrink-0 rounded-full bg-gradient-to-b from-brand-solid to-accent"
              />
              Agent workflow
            </h2>
            <p className="mt-0.5 text-caption text-fg-muted">
              User goal → Team lead → Parallel agents → Approval → Final result
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={mode === "live" ? "success" : "brand"}>
              <span
                aria-hidden="true"
                className={cn(
                  "h-1.5 w-1.5 rounded-full bg-current",
                  mode === "live" && "animate-pulse-dot",
                )}
              />
              {mode === "live" ? "Live execution state" : "Demo execution state"}
            </Badge>
            <Badge tone="neutral">Read-only</Badge>
          </div>
        </div>

        <p className="relative mt-1 text-micro text-fg-subtle xl:hidden">
          Scroll horizontally to follow the execution path.
        </p>

        <div className="relative -mx-1 mt-3 overflow-x-auto px-1 pb-1">
          <ol
            aria-label="Agent execution workflow from user goal to final result"
            className="flex min-w-[900px] items-stretch gap-1 xl:min-w-0"
          >
            <NodeShell
              index={1}
              dataStage="goal"
              title="User goal"
              icon={Target}
              state={goalState}
              className="w-[146px] shrink-0"
            >
              <p className="line-clamp-2 font-semibold leading-snug">
                {project?.title ?? "No active project"}
              </p>
              <p className="mt-1 line-clamp-2 text-micro leading-4 text-fg-muted">
                {project?.goal ?? "Create a project to define the goal."}
              </p>
              <div className="mt-auto flex flex-wrap gap-1 pt-2">
                {project ? (
                  <Badge tone="info">{project.status.replace(/_/g, " ")}</Badge>
                ) : (
                  <Badge tone="neutral">Idle</Badge>
                )}
                {project && (
                  <Badge tone="neutral">
                    <Clock aria-hidden="true" className="h-3 w-3" />
                    {project.deadline}
                  </Badge>
                )}
              </div>
            </NodeShell>

            <Connector active={Boolean(project)} />

            <NodeShell
              index={2}
              dataStage="team-lead"
              title="Team lead"
              icon={Users}
              state={leadState}
              className="w-[156px] shrink-0"
            >
              {lead ? (
                <>
                  <div className="flex items-center gap-2">
                    <Avatar name={lead.name} hue={lead.avatarHue} size={30} />
                    <div className="min-w-0">
                      <p className="truncate font-semibold">{lead.name}</p>
                      <p className="truncate text-micro text-fg-muted">{lead.role}</p>
                    </div>
                  </div>
                  <p className="mt-2 line-clamp-2 text-micro leading-4 text-fg-muted">
                    {lead.currentTask ?? "Standing by"}
                  </p>
                  <div className="mt-auto pt-2">
                    <AgentStatusBadge status={lead.status} live={mode === "live"} />
                  </div>
                </>
              ) : (
                <>
                  <p className="font-semibold text-fg-muted">No team lead</p>
                  <p className="mt-1 text-micro leading-4 text-fg-muted">
                    Assign a Team Lead agent to coordinate execution.
                  </p>
                  <Link
                    href="/agents"
                    className="mt-auto inline-flex items-center gap-1 pt-2 text-micro font-semibold text-brand"
                  >
                    Open agents
                    <ArrowRight aria-hidden="true" className="h-3 w-3" />
                  </Link>
                </>
              )}
            </NodeShell>

            <Connector active={Boolean(lead)} />

            <NodeShell
              index={3}
              dataStage="parallel-agents"
              title="Parallel AI agents"
              icon={Play}
              state={agentsState}
              className="w-[252px] shrink-0"
            >
              {parallelAgents.length === 0 ? (
                <div className="flex flex-1 flex-col">
                  <p className="font-semibold text-fg-muted">No agents executing</p>
                  <p className="mt-1 text-micro leading-4 text-fg-muted">
                    Owners appear here when tasks start running.
                  </p>
                  <Link
                    href="/tasks"
                    className="mt-auto inline-flex items-center gap-1 pt-2 text-micro font-semibold text-brand"
                  >
                    Open tasks
                    <ArrowRight aria-hidden="true" className="h-3 w-3" />
                  </Link>
                </div>
              ) : (
                <div className="flex flex-1 flex-col gap-1.5">
                  {parallelAgents.map((agent) => {
                    const task = activeTasks.find((candidate) => candidate.ownerAgentId === agent.id);
                    const nodeState = agentTaskState(task?.status);
                    return (
                      <details
                        key={agent.id}
                        className="group rounded-control border border-line bg-surface/80 p-2 transition-colors open:border-brand/25 open:bg-surface"
                      >
                        <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
                          <Avatar name={agent.name} hue={agent.avatarHue} size={22} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-caption font-semibold text-fg">
                              {agent.name}
                            </span>
                            <span className="block truncate text-micro text-fg-subtle">
                              {agent.role}
                            </span>
                          </span>
                          <span
                            aria-hidden="true"
                            className={cn(
                              "h-1.5 w-1.5 shrink-0 rounded-full",
                              nodeState === "active"
                                ? "animate-pulse-dot bg-brand"
                                : nodeState === "waiting"
                                  ? "bg-warning-fg"
                                  : nodeState === "complete"
                                    ? "bg-success-fg"
                                    : "bg-fg-subtle",
                            )}
                          />
                          <ChevronRight className="h-3 w-3 shrink-0 text-fg-subtle transition-transform group-open:rotate-90" />
                        </summary>
                        <div className="mt-2 border-t border-line-subtle pt-2">
                          <p className="line-clamp-2 text-micro leading-4 text-fg">
                            {task?.title ?? agent.currentTask ?? "Standing by"}
                          </p>
                          {task ? (
                            <>
                              <div className="mt-1.5 flex items-center gap-2">
                                <ProgressBar value={task.progress} className="h-1" />
                                <span className="w-7 shrink-0 text-right text-micro font-semibold tabular-nums text-fg-muted">
                                  {task.progress}%
                                </span>
                              </div>
                              <div className="mt-1.5 flex items-center justify-between gap-1">
                                <TaskStatusBadge status={task.status} />
                                {task.etaMinutes > 0 && (
                                  <span className="text-micro text-fg-subtle">~{task.etaMinutes}m</span>
                                )}
                              </div>
                            </>
                          ) : (
                            <p className="mt-1 text-micro text-fg-subtle">No task assigned.</p>
                          )}
                        </div>
                      </details>
                    );
                  })}
                </div>
              )}
            </NodeShell>

            <Connector active={parallelAgents.length > 0} />

            <NodeShell
              index={4}
              dataStage="approval"
              title="Approval"
              icon={pendingApprovals.length > 0 ? CircleAlert : CheckCircle2}
              state={approvalState}
              className="w-[148px] shrink-0"
            >
              <p className="font-semibold tabular-nums">{pendingApprovals.length} pending</p>
              <p className="mt-1 text-micro leading-4 text-fg-muted">
                {pendingApprovals.length > 0
                  ? "A human decision is required before work can continue."
                  : "No approval is currently blocking execution."}
              </p>
              {pendingApprovals[0] && (
                <p className="mt-1 line-clamp-2 text-micro font-medium text-warning-fg">
                  {pendingApprovals[0].title}
                </p>
              )}
              <Link
                href="/approvals"
                className="mt-auto inline-flex items-center gap-1 pt-2 text-micro font-semibold text-brand"
              >
                Approval inbox
                <ArrowRight aria-hidden="true" className="h-3 w-3" />
              </Link>
            </NodeShell>

            <Connector active={pendingApprovals.length > 0 || completed.length > 0} />

            <NodeShell
              index={5}
              dataStage="result"
              title="Final result"
              icon={CheckCircle2}
              state={resultState}
              className="w-[152px] shrink-0"
            >
              <p className="font-semibold">
                {resultState === "complete"
                  ? "Delivered"
                  : resultState === "waiting"
                    ? "Needs attention"
                    : project
                      ? "In progress"
                      : "Waiting for goal"}
              </p>
              <p className="mt-1 text-micro leading-4 text-fg-muted">
                {completed.length} of {tasks.length} tasks complete
                {failed.length > 0 ? ` · ${failed.length} failed` : ""}
              </p>
              <div className="mt-2 flex items-center gap-2">
                <ProgressBar value={resultPercent} className="h-1" />
                <span className="w-8 shrink-0 text-right text-micro font-semibold tabular-nums text-fg-muted">
                  {resultPercent}%
                </span>
              </div>
              {project && (
                <Link
                  href={`/projects/${project.id}`}
                  className="mt-auto inline-flex items-center gap-1 pt-2 text-micro font-semibold text-brand"
                >
                  Open result
                  <ArrowRight aria-hidden="true" className="h-3 w-3" />
                </Link>
              )}
            </NodeShell>
          </ol>
        </div>

        <div className="relative mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-fg-subtle">
          <span className="inline-flex items-center gap-1">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-brand" /> Active
          </span>
          <span className="inline-flex items-center gap-1">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warning-fg" /> Waiting
          </span>
          <span className="inline-flex items-center gap-1">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-success-fg" /> Complete
          </span>
          <span className="inline-flex items-center gap-1">
            <Circle aria-hidden="true" className="h-2 w-2 text-fg-subtle" /> Idle
          </span>
          <span className="ml-auto">Derived from task, agent and approval records.</span>
        </div>
      </Card>
    </section>
  );
}
