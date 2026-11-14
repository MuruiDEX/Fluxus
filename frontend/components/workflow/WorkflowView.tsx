import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Brain,
  CheckCircle2,
  Clock,
  Eye,
  Layers,
  Target,
  Users,
} from "lucide-react";
import {
  Avatar,
  Badge,
  ProgressBar,
  TaskStatusBadge,
  type BadgeTone,
} from "@/components/system/badges";
import { Card } from "@/components/system/ui";
import { cn } from "@/lib/cn";
import type {
  WorkflowApprovals,
  WorkflowBrain,
  WorkflowGoal,
  WorkflowLane,
  WorkflowLaneKey,
  WorkflowModel,
  WorkflowParallelWork,
  WorkflowReview,
  WorkflowStage,
  WorkflowTaskView,
  WorkflowTeamLead,
} from "@/lib/workflow";

/**
 * Shared AI Office workflow view (Slice 5B-2).
 *
 * Presentational only: the caller assembles the model and owns data loading.
 * DEMO and LIVE can pass the same derived `WorkflowModel`; this component never
 * reads stores, calls APIs, classifies tasks, or mutates project state.
 */
export interface WorkflowViewProps {
  readonly model: WorkflowModel;
  readonly className?: string;
}

const STAGE_ACCENTS: Record<WorkflowStage["key"], string> = {
  goal: "border-t-brand",
  team_lead: "border-t-info",
  parallel_work: "border-t-brand",
  approvals: "border-t-warning",
  review: "border-t-success",
  brain: "border-t-info",
};

const LANE_TONES: Record<WorkflowLaneKey, BadgeTone> = {
  running: "info",
  waiting_dependency: "warning",
  waiting_user: "warning",
  completed: "success",
  ready: "brand",
  attention: "danger",
};

function StageLink({
  href,
  children,
}: {
  href: string | null;
  children: ReactNode;
}) {
  if (!href) return null;
  return (
    <Link
      href={href}
      className="mt-auto inline-flex items-center gap-1 pt-2 text-caption font-semibold text-brand transition-colors hover:text-brand-hover"
    >
      {children}
      <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
    </Link>
  );
}

function StageCard({
  index,
  title,
  icon: Icon,
  accent,
  children,
}: {
  index: number;
  title: string;
  icon: typeof Target;
  accent: string;
  children: ReactNode;
}) {
  return (
    <Card className={cn("flex min-w-0 flex-1 flex-col border-t-2 p-3.5", accent)}>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-surface-sunken text-micro font-bold tabular-nums text-fg-muted">
          {String(index + 1).padStart(2, "0")}
        </span>
        <Icon aria-hidden="true" className="h-4 w-4 text-fg-subtle" />
      </div>
      <h3 className="mt-2 text-label font-bold uppercase tracking-wide text-fg-muted">
        {title}
      </h3>
      <div className="mt-1.5 flex flex-1 flex-col text-body-sm text-fg">
        {children}
      </div>
    </Card>
  );
}

function GoalStage({ goal }: { goal: WorkflowGoal }) {
  if (goal.state === "unavailable") {
    return (
      <>
        <p className="font-semibold text-fg-muted">No active project</p>
        <p className="mt-1 text-caption text-fg-muted">{goal.summary}</p>
      </>
    );
  }

  return (
    <>
      <p className="font-semibold leading-snug">{goal.title}</p>
      <p className="mt-1 text-caption text-fg-muted">{goal.summary}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {goal.status && (
          <Badge tone="info">{goal.status.replace(/_/g, " ")}</Badge>
        )}
        {goal.deadline && (
          <Badge tone="neutral">
            <Clock aria-hidden="true" className="h-3 w-3" />
            {goal.deadline}
          </Badge>
        )}
      </div>
      <StageLink href={goal.href}>Open project</StageLink>
    </>
  );
}

function TeamLeadStage({ teamLead }: { teamLead: WorkflowTeamLead }) {
  if (teamLead.state === "unavailable" || !teamLead.agent) {
    return (
      <>
        <div className="rounded-control border border-dashed border-line bg-surface-sunken p-2.5">
          <p className="font-semibold text-fg-muted">Not assigned</p>
          <p className="mt-0.5 text-caption text-fg-muted">{teamLead.note}</p>
        </div>
        <StageLink href={teamLead.href}>Open agents</StageLink>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <Avatar
          name={teamLead.agent.name}
          hue={teamLead.agent.avatarHue}
          size={32}
        />
        <div className="min-w-0">
          <p className="truncate font-semibold">{teamLead.agent.name}</p>
          <p className="truncate text-caption text-fg-muted">
            {teamLead.agent.role}
          </p>
        </div>
      </div>
      <p className="mt-2 text-caption text-fg-muted">{teamLead.note}</p>
      <StageLink href={teamLead.href}>Open agents</StageLink>
    </>
  );
}

function ParallelWorkStage({
  parallelWork,
  model,
}: {
  parallelWork: WorkflowParallelWork;
  model: WorkflowModel;
}) {
  if (parallelWork.state === "unavailable") {
    return <p className="text-caption text-fg-muted">{parallelWork.note}</p>;
  }

  return (
    <>
      <p className="font-semibold tabular-nums">
        {model.counts.total} task{model.counts.total === 1 ? "" : "s"}
      </p>
      <ProgressBar value={model.progress.percent} className="mt-2" />
      <p className="mt-1 text-caption text-fg-muted tabular-nums">
        {model.progress.completed} of {model.progress.total} completed
      </p>
      <div className="mt-2 flex flex-wrap gap-1">
        <Badge tone="info">{model.counts.running} running</Badge>
        <Badge tone={model.counts.waitingDependency > 0 ? "warning" : "neutral"}>
          {model.counts.waitingDependency} blocked
        </Badge>
        <Badge tone={model.counts.waitingUser > 0 ? "warning" : "neutral"}>
          {model.counts.waitingUser} waiting on you
        </Badge>
      </div>
      {parallelWork.note && (
        <p className="mt-2 text-caption text-fg-muted">{parallelWork.note}</p>
      )}
      <StageLink href={parallelWork.href}>Open project</StageLink>
    </>
  );
}

function ApprovalsStage({ approvals }: { approvals: WorkflowApprovals }) {
  if (approvals.state === "unavailable") {
    return <p className="text-caption text-fg-muted">{approvals.note}</p>;
  }

  return (
    <>
      <p className="font-semibold tabular-nums">
        {approvals.completeness === "partial" ? "Loaded page: " : ""}
        {approvals.pendingCount} pending
      </p>
      <p className="text-caption text-fg-muted tabular-nums">
        {approvals.decidedCount} decided
      </p>
      <p className="mt-2 text-caption text-fg-muted">
        {approvals.completenessNote}
      </p>
      <StageLink href={approvals.href}>Open approvals</StageLink>
    </>
  );
}

function reviewProvenance(review: WorkflowReview): {
  label: string;
  tone: BadgeTone;
} {
  if (review.derived) return { label: "Derived readiness", tone: "warning" };
  if (review.state === "empty") return { label: "Not available", tone: "neutral" };
  if (review.state === "completed") return { label: "Server-reported", tone: "success" };
  return { label: "Server-reported", tone: "info" };
}

function ReviewStage({ review }: { review: WorkflowReview }) {
  const provenance = reviewProvenance(review);
  return (
    <>
      <Badge tone={provenance.tone}>{provenance.label}</Badge>
      <p className="mt-2 font-semibold">{review.label}</p>
      <p className="mt-1 text-caption text-fg-muted">{review.note}</p>
      <StageLink href={review.href}>Open project</StageLink>
    </>
  );
}

function BrainStage({ brain }: { brain: WorkflowBrain }) {
  return (
    <>
      <Badge tone="info">Destination</Badge>
      <p className="mt-2 text-caption text-fg-muted">{brain.note}</p>
      <p className="mt-1 text-caption text-fg-muted">
        No report or Brain handoff is recorded by this view.
      </p>
      <StageLink href={brain.href}>{brain.label}</StageLink>
    </>
  );
}

function renderStage(stage: WorkflowStage, model: WorkflowModel, index: number) {
  switch (stage.key) {
    case "goal":
      return (
        <StageCard
          index={index}
          title="Goal"
          icon={Target}
          accent={STAGE_ACCENTS.goal}
        >
          <GoalStage goal={stage} />
        </StageCard>
      );
    case "team_lead":
      return (
        <StageCard
          index={index}
          title="Team Lead"
          icon={Users}
          accent={STAGE_ACCENTS.team_lead}
        >
          <TeamLeadStage teamLead={stage} />
        </StageCard>
      );
    case "parallel_work":
      return (
        <StageCard
          index={index}
          title="Parallel work"
          icon={Layers}
          accent={STAGE_ACCENTS.parallel_work}
        >
          <ParallelWorkStage parallelWork={stage} model={model} />
        </StageCard>
      );
    case "approvals":
      return (
        <StageCard
          index={index}
          title="Approvals"
          icon={CheckCircle2}
          accent={STAGE_ACCENTS.approvals}
        >
          <ApprovalsStage approvals={stage} />
        </StageCard>
      );
    case "review":
      return (
        <StageCard
          index={index}
          title="Review"
          icon={Eye}
          accent={STAGE_ACCENTS.review}
        >
          <ReviewStage review={stage} />
        </StageCard>
      );
    case "brain":
      return (
        <StageCard
          index={index}
          title="Brain"
          icon={Brain}
          accent={STAGE_ACCENTS.brain}
        >
          <BrainStage brain={stage} />
        </StageCard>
      );
  }
}

function WorkflowSpine({ model }: { model: WorkflowModel }) {
  return (
    <section aria-labelledby="workflow-spine-title">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2
            id="workflow-spine-title"
            className="text-heading-sm font-bold text-fg"
          >
            Workflow
          </h2>
          <p className="mt-0.5 text-caption text-fg-muted">
            Goal → Team Lead → Parallel work → Approvals → Review → Brain
          </p>
        </div>
        <Badge tone="neutral">Read-only office view</Badge>
      </div>
      <ol
        aria-label="Workflow stages from Goal to Brain"
        className="mt-3 flex flex-col gap-1.5 lg:flex-row lg:items-stretch lg:overflow-x-auto lg:pb-1"
      >
        {model.stages.map((stage, index) => (
          <li
            key={stage.key}
            data-stage={stage.key}
            className="flex min-w-0 flex-col lg:min-w-[190px] lg:flex-1 lg:flex-row lg:items-stretch"
          >
            {renderStage(stage, model, index)}
            {index < model.stages.length - 1 && (
              <>
                <span
                  aria-hidden="true"
                  className="flex items-center justify-center py-1 text-fg-subtle lg:hidden"
                >
                  <ArrowRight className="h-4 w-4 rotate-90" />
                </span>
                <span
                  aria-hidden="true"
                  className="hidden shrink-0 items-center px-1.5 text-fg-subtle lg:flex"
                >
                  <ArrowRight className="h-4 w-4" />
                </span>
              </>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function gateBadge(view: WorkflowTaskView): {
  label: string;
  tone: BadgeTone;
} | null {
  switch (view.gateState) {
    case "none":
      return null;
    case "pending":
      return { label: "Approval pending", tone: "warning" };
    case "approved":
      return { label: "Approval approved", tone: "success" };
    case "rejected":
      return { label: "Approval rejected", tone: "danger" };
    case "missing":
      return { label: "Gate not loaded", tone: "danger" };
    case "unknown":
      return { label: `Gate: ${view.gate?.status ?? "unknown"}`, tone: "danger" };
  }
}

function formatDependency(view: WorkflowTaskView) {
  if (view.waitingOn.length === 0) return "No dependencies.";
  return view.waitingOn
    .map((ref) => {
      const label = ref.title ?? ref.id;
      if (!ref.loaded) return `${label} (not loaded)`;
      return `${label} (${ref.completed ? "complete" : "open"})`;
    })
    .join(" · ");
}

function formatGate(view: WorkflowTaskView) {
  if (view.gateState === "none") return "No approval gate linked.";
  if (view.gate) {
    return `${view.gate.title} · ${view.gate.status}`;
  }
  if (view.gateState === "missing") return "Linked approval gate is not loaded.";
  return `Approval gate state: ${view.gateState}.`;
}

function TaskCard({ view }: { view: WorkflowTaskView }) {
  const gate = gateBadge(view);
  const showNote =
    Boolean(view.note) &&
    (view.lane === "attention" ||
      view.lane === "waiting_dependency" ||
      view.lane === "waiting_user");

  return (
    <li data-task-id={view.task.id} data-task-view-key={view.viewKey}>
      <article className="rounded-control border border-line bg-surface p-3 shadow-card">
        <div className="flex items-start justify-between gap-2">
          <h4 className="min-w-0 text-body-sm font-semibold leading-snug text-fg">
            {view.task.title}
          </h4>
          {view.knownStatus ? (
            <TaskStatusBadge status={view.knownStatus} />
          ) : (
            <Badge tone="danger">Unknown status</Badge>
          )}
        </div>

        <div className="mt-1.5 flex items-center gap-1.5 text-caption text-fg-muted">
          {view.owner ? (
            <>
              <Avatar
                name={view.owner.name}
                hue={view.owner.avatarHue}
                size={20}
              />
              <span className="min-w-0 truncate">
                <span className="font-medium text-fg">{view.owner.name}</span>
                {" · "}
                {view.owner.role}
              </span>
            </>
          ) : (
            <span>Unassigned</span>
          )}
        </div>

        <div className="mt-2 flex flex-wrap gap-1">
          {view.waitingOn.length > 0 && (
            <Badge tone="warning">
              {view.waitingOn.length} dependenc
              {view.waitingOn.length === 1 ? "y" : "ies"}
            </Badge>
          )}
          {gate && <Badge tone={gate.tone}>{gate.label}</Badge>}
        </div>

        {showNote && (
          <p className="mt-2 flex items-start gap-1.5 text-caption text-fg-muted">
            <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{view.note}</span>
          </p>
        )}

        <details className="mt-2 border-t border-line-subtle pt-2">
          <summary className="cursor-pointer text-caption font-semibold text-brand">
            Task details
          </summary>
          <dl className="mt-2 space-y-1.5 text-caption">
            <div className="grid grid-cols-[86px_1fr] gap-2">
              <dt className="text-fg-subtle">Status</dt>
              <dd className="text-fg-muted">
                {view.knownStatus ?? "Unknown"}
                {view.rawStatus !== view.knownStatus
                  ? ` · server value: ${view.rawStatus}`
                  : ""}
              </dd>
            </div>
            <div className="grid grid-cols-[86px_1fr] gap-2">
              <dt className="text-fg-subtle">Owner</dt>
              <dd className="text-fg-muted">
                {view.owner
                  ? `${view.owner.name} · ${view.owner.role}`
                  : "Unassigned"}
              </dd>
            </div>
            <div className="grid grid-cols-[86px_1fr] gap-2">
              <dt className="text-fg-subtle">Dependencies</dt>
              <dd className="text-fg-muted">{formatDependency(view)}</dd>
            </div>
            <div className="grid grid-cols-[86px_1fr] gap-2">
              <dt className="text-fg-subtle">Approval</dt>
              <dd className="text-fg-muted">{formatGate(view)}</dd>
            </div>
            {view.note && (
              <div className="grid grid-cols-[86px_1fr] gap-2">
                <dt className="text-fg-subtle">Why</dt>
                <dd className="text-fg-muted">{view.note}</dd>
              </div>
            )}
          </dl>
        </details>
      </article>
    </li>
  );
}

function LaneBlock({ lane }: { lane: WorkflowLane }) {
  const secondary = lane.kind === "secondary";
  return (
    <section data-lane={lane.key} aria-labelledby={`workflow-lane-${lane.key}`}>
      <Card
        className={cn(
          "flex h-full flex-col p-3",
          secondary && "border-warning-line bg-warning-soft/40",
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <h3
            id={`workflow-lane-${lane.key}`}
            className="text-label font-bold uppercase tracking-wide text-fg-muted"
          >
            {lane.label}
          </h3>
          <Badge tone={LANE_TONES[lane.key]}>{lane.tasks.length}</Badge>
        </div>
        <p className="mt-1 text-caption text-fg-subtle">{lane.description}</p>
        {lane.tasks.length === 0 ? (
          <p className="mt-2.5 rounded-control border border-dashed border-line p-3 text-caption text-fg-subtle">
            No tasks in this lane.
          </p>
        ) : (
          <ul className="mt-2.5 flex flex-1 flex-col gap-2">
            {lane.tasks.map((view) => (
              <TaskCard key={view.viewKey} view={view} />
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

function LaneBoard({ model }: { model: WorkflowModel }) {
  const primary = model.lanes.filter((lane) => lane.kind === "primary");
  const secondary = model.lanes.filter(
    (lane) => lane.kind === "secondary" && lane.tasks.length > 0,
  );

  return (
    <section aria-labelledby="workflow-parallel-title">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2
            id="workflow-parallel-title"
            className="text-heading-sm font-bold text-fg"
          >
            Parallel work
          </h2>
          <p className="mt-0.5 text-caption text-fg-muted">
            Four primary lanes, plus Ready and Needs attention only when they
            contain work. Every task appears exactly once.
          </p>
        </div>
        <Badge tone="neutral">
          {model.counts.total} task{model.counts.total === 1 ? "" : "s"}
        </Badge>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {primary.map((lane) => (
          <LaneBlock key={lane.key} lane={lane} />
        ))}
      </div>
      {secondary.length > 0 && (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {secondary.map((lane) => (
            <LaneBlock key={lane.key} lane={lane} />
          ))}
        </div>
      )}
    </section>
  );
}

function ProjectUnavailable({ model }: { model: WorkflowModel }) {
  return (
    <section aria-labelledby="workflow-parallel-title">
      <h2
        id="workflow-parallel-title"
        className="text-heading-sm font-bold text-fg"
      >
        Parallel work
      </h2>
      <Card className="mt-2 p-5">
        <p className="font-semibold text-fg">No active project</p>
        <p className="mt-1 max-w-xl text-body-sm text-fg-muted">
          Select a project to see the goal, team lead, task lanes, approvals and
          review readiness. Business activity remains business-wide.
        </p>
        <p className="mt-2 text-caption text-fg-subtle">{model.parallelWork.note}</p>
      </Card>
    </section>
  );
}

function ApprovalQueue({ approvals }: { approvals: WorkflowApprovals }) {
  const visible = approvals.pending.slice(0, 4);
  return (
    <section
      aria-labelledby="workflow-approval-queue"
      data-approval-completeness={approvals.completeness}
    >
      <Card className="flex h-full flex-col p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2
            id="workflow-approval-queue"
            className="text-body font-bold text-fg"
          >
            Approval queue
          </h2>
          <Badge tone={approvals.pendingCount > 0 ? "warning" : "neutral"}>
            {approvals.completeness === "partial" ? "Loaded: " : ""}
            {approvals.pendingCount} pending
          </Badge>
        </div>
        <p className="mt-1 text-caption text-fg-muted">
          {approvals.completenessNote}
        </p>
        <p className="mt-1 text-caption text-fg-subtle">{approvals.note}</p>

        {visible.length === 0 ? (
          <p className="mt-3 rounded-control border border-dashed border-line p-3 text-caption text-fg-subtle">
            No pending approvals in the provided list.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {visible.map((approval) => (
              <li
                key={approval.id}
                className="rounded-control border border-line bg-surface-sunken p-2.5"
              >
                <p className="text-body-sm font-semibold text-fg">
                  {approval.title}
                </p>
                <p className="mt-0.5 text-caption text-fg-muted">
                  {approval.requestedBy} · {approval.ageLabel}
                </p>
              </li>
            ))}
          </ul>
        )}
        {approvals.pendingCount > visible.length && (
          <p className="mt-2 text-caption text-fg-subtle">
            Showing {visible.length} of {approvals.pendingCount} pending approvals
            in the provided list.
          </p>
        )}
        <Link
          href={approvals.href}
          className="mt-auto inline-flex items-center gap-1 pt-3 text-caption font-semibold text-brand transition-colors hover:text-brand-hover"
        >
          Open approval inbox
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </Card>
    </section>
  );
}

function activityDotClass(kind: string): string {
  switch (kind) {
    case "approval":
      return "bg-warning";
    case "result":
      return "bg-success";
    case "system":
      return "bg-info";
    default:
      return "bg-brand";
  }
}

function ActivityPanel({ model }: { model: WorkflowModel }) {
  const { activity } = model;
  // A compact provenance summary, not the old full Timeline feed.
  const recent = activity.preview.slice(0, 4);
  return (
    <section
      aria-labelledby="workflow-activity-title"
      data-activity-scope={activity.scope}
    >
      <Card className="flex h-full flex-col p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2
            id="workflow-activity-title"
            className="text-body font-bold text-fg"
          >
            Recent activity
          </h2>
          <Badge tone="brand">{activity.label}</Badge>
        </div>
        <p className="mt-1 text-caption text-fg-muted">{activity.note}</p>
        <p className="mt-1 text-caption text-fg-subtle">
          Showing {recent.length} of {activity.providedCount} supplied
          event{activity.providedCount === 1 ? "" : "s"}.
        </p>

        {recent.length === 0 ? (
          <p className="mt-3 rounded-control border border-dashed border-line p-3 text-caption text-fg-subtle">
            No activity supplied.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col">
            {recent.map((event) => (
              <li
                key={event.id}
                className="flex items-start gap-2 border-b border-line-subtle py-2 text-caption last:border-0"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                    activityDotClass(event.kind),
                  )}
                />
                <div className="min-w-0">
                  <p className="leading-5">
                    <span className="font-semibold">{event.agentName}</span>
                    <span className="text-fg-subtle"> · {event.at}</span>
                  </p>
                  <p className="leading-5 text-fg-muted">{event.text}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
        <Link
          href="/activity"
          className="mt-auto inline-flex items-center gap-1 pt-3 text-caption font-semibold text-brand transition-colors hover:text-brand-hover"
        >
          Open activity
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </Card>
    </section>
  );
}

function Limitations({ model }: { model: WorkflowModel }) {
  if (model.limitations.length === 0) return null;
  return (
    <section aria-labelledby="workflow-limits-title">
      <div className="rounded-card border border-line bg-surface-sunken p-3.5">
        <h2
          id="workflow-limits-title"
          className="text-label font-bold uppercase tracking-wide text-fg-muted"
        >
          Known limits
        </h2>
        <ul className="mt-1.5 list-disc space-y-1 pl-4 text-caption text-fg-muted">
          {model.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export function WorkflowView({ model, className }: WorkflowViewProps) {
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <WorkflowSpine model={model} />
      {model.parallelWork.state === "unavailable" ? (
        <ProjectUnavailable model={model} />
      ) : (
        <LaneBoard model={model} />
      )}
      <div className={cn("grid gap-3", model.approvals.state !== "unavailable" && "lg:grid-cols-2")}>
        {model.approvals.state !== "unavailable" && (
          <ApprovalQueue approvals={model.approvals} />
        )}
        <ActivityPanel model={model} />
      </div>
      <Limitations model={model} />
    </div>
  );
}