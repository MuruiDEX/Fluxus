"use client";

import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Code2,
  Coins,
  HeartHandshake,
  Layers,
  ListChecks,
  Megaphone,
  Palette,
  PlusCircle,
  Settings2,
  ShoppingBag,
  Sparkles,
  UserPlus,
  Users,
  Wallet,
  Zap,
} from "lucide-react";
import { Avatar, Badge, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card, Skeleton } from "@/components/system/ui";
import { cn } from "@/lib/cn";
import { goalHref } from "@/lib/intake-goal";
import type { ActivityEvent, Agent, Approval, Project, Task } from "@/lib/domain/types";

export type DataState = "idle" | "loading" | "ready" | "error";
export type DashboardMode = "demo" | "live";

export interface SpendSummary {
  amount: number;
  note: string;
  budget?: number;
  unknownCount?: number;
}

const money = (value: number) =>
  `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function SectionHeading({
  title,
  sub,
  action,
  live,
}: {
  title: string;
  sub?: string;
  action?: { href: string; label: string };
  live?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-heading-sm font-bold tracking-tight text-fg">
          <span
            aria-hidden="true"
            className="h-3.5 w-[2.5px] shrink-0 rounded-full bg-gradient-to-b from-brand-solid to-accent"
          />
          {title}
          {live && (
            <span className="inline-flex items-center gap-1 rounded-full border border-success-line bg-success-soft px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wide text-success-fg">
              <span aria-hidden="true" className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-current" />
              Live
            </span>
          )}
        </h2>
        {sub && <p className="mt-0.5 text-caption text-fg-muted">{sub}</p>}
      </div>
      {action && (
        <Link
          href={action.href}
          className="inline-flex items-center gap-1 text-caption font-semibold text-brand transition-colors hover:text-brand-hover"
        >
          {action.label}
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      )}
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  tone = "brand",
  children,
}: {
  icon: typeof Bot;
  label: string;
  value: string;
  hint: string;
  tone?: "brand" | "accent" | "holo" | "success";
  children?: React.ReactNode;
}) {
  const toneClass = {
    brand: "bg-brand-soft text-brand",
    accent: "bg-accent-soft text-accent",
    holo: "bg-holo-soft text-holo",
    success: "bg-success-soft text-success-fg",
  }[tone];

  return (
    <Card className="flux-stat group p-4">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -right-12 -top-14 h-32 w-32 rounded-full bg-accent/10 blur-2xl transition-opacity duration-300 group-hover:opacity-80"
      />
      <div className="relative flex items-start justify-between gap-2">
        <p className="mt-1 text-heading-lg font-bold tabular-nums tracking-tight text-fg">
          {value}
        </p>
        <span
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[0.6rem]",
            toneClass,
          )}
        >
          <Icon aria-hidden="true" className="h-4 w-4" />
        </span>
      </div>
      <p className="relative mt-1.5 text-caption font-semibold text-fg">{label}</p>
      <p className="relative mt-0.5 text-label leading-4 text-fg-subtle">{hint}</p>
      {children && <div className="relative mt-3">{children}</div>}
    </Card>
  );
}

export function SystemOverview({
  mode,
  agents,
  tasks,
  approvals,
  spend,
  agentsState = "ready",
  tasksState = "ready",
  spendState = "ready",
}: {
  mode: DashboardMode;
  agents: Agent[];
  tasks: Task[];
  approvals: Approval[];
  spend: SpendSummary | null;
  agentsState?: DataState;
  tasksState?: DataState;
  spendState?: DataState;
}) {
  const hired = agents.filter((agent) => agent.hired);
  const activeAgents = hired.filter((agent) =>
    ["working", "thinking", "reviewing"].includes(agent.status),
  );
  const openTasks = tasks.filter((task) =>
    ["working", "waiting_dependency", "waiting_assets", "waiting_approval"].includes(task.status),
  );
  const completedTasks = tasks.filter((task) => task.status === "completed");
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const failedTasks = tasks.filter((task) => task.status === "failed");
  const degraded = agentsState === "error" || tasksState === "error" || spendState === "error";

  const statusLabel = degraded
    ? "Degraded"
    : pendingApprovals.length > 0
      ? "Needs approval"
      : mode === "demo"
        ? "Demo running"
        : "Operational";
  const statusTone = degraded ? "danger" : pendingApprovals.length > 0 ? "warning" : "success";

  return (
    <section aria-labelledby="overview-title">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2
            id="overview-title"
            className="flex items-center gap-2 text-heading-sm font-bold tracking-tight text-fg"
          >
            <span
              aria-hidden="true"
              className="h-3.5 w-[2.5px] shrink-0 rounded-full bg-gradient-to-b from-brand-solid to-accent"
            />
            System overview
          </h2>
          <p className="mt-0.5 text-caption text-fg-muted">
            Real counts from the {mode === "live" ? "connected server" : "demo dataset"} — no
            estimates.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {failedTasks.length > 0 && (
            <Badge tone="danger">
              <CircleAlert aria-hidden="true" className="h-3 w-3" />
              {failedTasks.length} error{failedTasks.length === 1 ? "" : "s"}
            </Badge>
          )}
          <Badge tone={statusTone}>
            <span
              aria-hidden="true"
              className={cn("h-1.5 w-1.5 rounded-full bg-current", !degraded && "animate-pulse-dot")}
            />
            {statusLabel}
          </Badge>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile
          icon={Bot}
          label="Active agents"
          value={agentsState === "error" ? "—" : agentsState === "loading" ? "…" : String(activeAgents.length)}
          hint={
            agentsState === "error"
              ? "Team data unavailable"
              : `${hired.length} hired · ${agents.length - hired.length} available in marketplace`
          }
          tone="brand"
        >
          <div className="flex -space-x-1.5">
            {hired.slice(0, 5).map((agent) => (
              <span key={agent.id} className="ring-2 ring-surface">
                <Avatar name={agent.name} hue={agent.avatarHue} size={20} />
              </span>
            ))}
            {hired.length > 5 && (
              <span className="inline-flex h-5 items-center rounded-full bg-surface-sunken px-1.5 text-micro font-semibold text-fg-muted ring-2 ring-surface">
                +{hired.length - 5}
              </span>
            )}
          </div>
        </StatTile>

        <StatTile
          icon={ListChecks}
          label="Open tasks"
          value={tasksState === "error" ? "—" : tasksState === "loading" ? "…" : String(openTasks.length)}
          hint={
            tasksState === "error"
              ? "Task data unavailable"
              : `${completedTasks.length} completed · ${tasks.length} total`
          }
          tone="accent"
        >
          <div className="flex items-center gap-2">
            <ProgressBar
              value={tasks.length === 0 ? 0 : (completedTasks.length / tasks.length) * 100}
              className="h-1"
            />
            <span className="shrink-0 text-micro tabular-nums text-fg-subtle">
              {tasks.length === 0 ? "0%" : `${Math.round((completedTasks.length / tasks.length) * 100)}%`}
            </span>
          </div>
        </StatTile>

        <StatTile
          icon={Coins}
          label="AI spend"
          value={
            spendState === "error"
              ? "—"
              : spendState === "loading"
                ? "…"
                : spend
                  ? money(spend.amount)
                  : "—"
          }
          hint={
            spendState === "error"
              ? "Usage data unavailable"
              : spend
                ? spend.note
                : "No usage reported"
          }
          tone="holo"
        >
          {spend?.budget ? (
            <ProgressBar
              value={Math.min(100, (spend.amount / spend.budget) * 100)}
              className="h-1"
            />
          ) : (
            <p className="text-micro text-fg-subtle">
              {spend?.unknownCount ? `${spend.unknownCount} unpriced calls` : "Server-reported cost"}
            </p>
          )}
        </StatTile>

        <StatTile
          icon={CheckCircle2}
          label="Completed tasks"
          value={
            tasksState === "error" ? "—" : tasksState === "loading" ? "…" : String(completedTasks.length)
          }
          hint={
            pendingApprovals.length > 0
              ? `${pendingApprovals.length} approval${pendingApprovals.length === 1 ? "" : "s"} waiting on you`
              : "Nothing waiting on you"
          }
          tone="success"
        >
          <Link
            href="/approvals"
            className="inline-flex items-center gap-1 text-micro font-semibold text-brand hover:text-brand-hover"
          >
            Approval inbox
            <ArrowRight aria-hidden="true" className="h-3 w-3" />
          </Link>
        </StatTile>
      </div>
    </section>
  );
}

function taskRank(task: Task) {
  switch (task.status) {
    case "working":
      return 0;
    case "waiting_approval":
      return 1;
    case "waiting_dependency":
      return 2;
    case "waiting_assets":
      return 3;
    case "not_started":
      return 4;
    case "failed":
      return 5;
    default:
      return 6;
  }
}

export function OperationsPanel({
  mode,
  tasks,
  agents,
  approvals,
  projects,
  state = "ready",
  error,
}: {
  mode: DashboardMode;
  tasks: Task[];
  agents: Agent[];
  approvals: Approval[];
  projects: Project[];
  state?: DataState;
  error?: string | null;
}) {
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));
  const projectById = new Map(projects.map((project) => [project.id, project]));
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const failed = tasks.filter((task) => task.status === "failed");
  const completed = tasks.filter((task) => task.status === "completed");
  const visible = [...tasks].sort((a, b) => taskRank(a) - taskRank(b)).slice(0, 4);

  return (
    <section aria-labelledby="operations-title" className="min-w-0">
      <Card className="flux-panel relative flex h-full flex-col overflow-hidden p-4 sm:p-5">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -right-16 -top-20 h-52 w-52 rounded-full bg-accent/10 blur-3xl"
        />
        <div className="relative">
          <SectionHeading
            title="AI Operations"
            sub="Running work, owners and execution progress."
            action={{ href: "/office", label: "Open AI Office" }}
            live={mode === "live"}
          />
        </div>

        {state === "error" ? (
          <p role="alert" className="mt-4 rounded-control border border-danger-line bg-danger-soft p-3 text-caption text-danger-fg">
            {error ?? "Operations data is unavailable."}
          </p>
        ) : state === "loading" || state === "idle" ? (
          <div role="status" aria-label="Loading operations" className="mt-4 flex flex-col gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-3 rounded-control border border-line-subtle p-3">
                <Skeleton className="h-8 w-8 rounded-control" />
                <div className="flex-1">
                  <Skeleton className="h-3 w-2/3" />
                  <Skeleton className="mt-1.5 h-2.5 w-1/2" />
                </div>
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            ))}
          </div>
        ) : visible.length === 0 ? (
          <p className="mt-4 rounded-control border border-dashed border-line bg-surface-sunken/50 p-4 text-caption text-fg-muted">
            No tasks yet. Create a task to give the AI team work.
          </p>
        ) : (
          <ul className="relative mt-4 flex flex-col">
            {visible.map((task) => {
              const owner = agentById.get(task.ownerAgentId);
              const project = projectById.get(task.projectId);
              const running = task.status === "working";
              return (
                <li key={task.id}>
                  <Link
                    href={project ? `/projects/${project.id}` : "/tasks"}
                    className="group -mx-1.5 flex items-start gap-3 rounded-control border-b border-line-subtle px-1.5 py-3 transition-colors hover:bg-surface-hover/60 last:border-0"
                  >
                    <span
                      className={cn(
                        "mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control border",
                        running
                          ? "border-brand/25 bg-gradient-to-br from-brand-solid to-accent text-on-brand shadow-card"
                          : "border-line bg-surface-sunken text-fg-subtle",
                      )}
                    >
                      {task.status === "failed" ? (
                        <CircleAlert aria-hidden="true" className="h-4 w-4" />
                      ) : task.status === "completed" ? (
                        <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
                      ) : (
                        <Layers aria-hidden="true" className="h-4 w-4" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                        <p className="min-w-0 truncate text-body-sm font-semibold text-fg transition-colors group-hover:text-brand">
                          {task.title}
                        </p>
                        <TaskStatusBadge status={task.status} />
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-fg-subtle">
                        {owner ? (
                          <span className="inline-flex min-w-0 items-center gap-1.5">
                            <Avatar name={owner.name} hue={owner.avatarHue} size={16} />
                            <span className="truncate">
                              <span className="font-medium text-fg-muted">{owner.name}</span>
                              {" · "}
                              {owner.role}
                            </span>
                          </span>
                        ) : (
                          <span>Owner unavailable</span>
                        )}
                        {project && <span className="truncate">· {project.title}</span>}
                      </div>
                      {running && (
                        <div className="mt-2 flex items-center gap-2">
                          <ProgressBar value={task.progress} className="h-1" />
                          <span className="w-8 shrink-0 text-right text-micro font-semibold tabular-nums text-fg-muted">
                            {task.progress}%
                          </span>
                        </div>
                      )}
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <div className="relative mt-auto flex flex-col gap-2 pt-4">
          {pendingApprovals.length > 0 && (
            <Link
              href="/approvals"
              className="flex items-center justify-between gap-2 rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-caption font-semibold text-warning-fg transition-colors hover:border-warning/50"
            >
              <span className="flex items-center gap-2">
                <CircleAlert aria-hidden="true" className="h-3.5 w-3.5" />
                {pendingApprovals.length} approval{pendingApprovals.length === 1 ? "" : "s"} need a decision
              </span>
              <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
            </Link>
          )}
          {failed.length > 0 && (
            <Link
              href="/tasks"
              className="flex items-center justify-between gap-2 rounded-control border border-danger-line bg-danger-soft px-3 py-2 text-caption font-semibold text-danger-fg"
            >
              <span className="flex items-center gap-2">
                <CircleAlert aria-hidden="true" className="h-3.5 w-3.5" />
                {failed.length} task{failed.length === 1 ? "" : "s"} failed — retry from Tasks
              </span>
              <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
            </Link>
          )}
          <div className="flex items-center justify-between text-caption text-fg-subtle">
            <span>
              {completed.length} completed · {tasks.length - completed.length} open
            </span>
            <Link href="/tasks" className="font-semibold text-brand hover:text-brand-hover">
              All tasks
            </Link>
          </div>
        </div>
      </Card>
    </section>
  );
}

interface DepartmentConfig {
  id: string;
  name: string;
  focus: string;
  href: string;
  icon: typeof Megaphone;
  bounded?: boolean;
  planned: boolean;
  roles: RegExp;
  tone: string;
}

const DEPARTMENTS: DepartmentConfig[] = [
  {
    id: "marketing",
    name: "Marketing",
    focus: "Growth & content",
    href: "/marketing",
    icon: Megaphone,
    planned: true,
    roles: /market/i,
    tone: "bg-brand-soft text-brand",
  },
  {
    id: "sales",
    name: "Sales",
    focus: "Pipeline & revenue",
    href: "/crm",
    icon: Users,
    planned: true,
    roles: /sales|crm|revenue/i,
    tone: "bg-accent-soft text-accent",
  },
  {
    id: "finance",
    name: "Finance",
    focus: "Budget & forecast",
    href: "/finance",
    icon: Wallet,
    planned: true,
    roles: /cfo|financ/i,
    tone: "bg-holo-soft text-holo",
  },
  {
    id: "engineering",
    name: "Engineering",
    focus: "Build & deploy",
    href: "/projects",
    icon: Code2,
    planned: false,
    roles: /developer|engineer|web/i,
    tone: "bg-success-soft text-success-fg",
  },
  {
    id: "creative",
    name: "Creative",
    focus: "Design & media",
    href: "/brand",
    icon: Palette,
    planned: true,
    roles: /design|brand|creative/i,
    tone: "bg-holo-soft text-holo",
  },
  {
    id: "operations",
    name: "Operations",
    focus: "Workflow & delivery",
    href: "/office",
    icon: Settings2,
    planned: false,
    roles: /lead|operat/i,
    tone: "bg-brand-soft text-brand",
  },
  {
    id: "hr",
    name: "HR",
    focus: "Talent & agents",
    href: "/agents",
    icon: UserPlus,
    planned: false,
    roles: /\bhr\b|people|recruit/i,
    tone: "bg-warning-soft text-warning-fg",
  },
  {
    id: "customer-success",
    name: "Customer Success",
    focus: "Customers & support",
    href: "/customers",
    icon: HeartHandshake,
    planned: true,
    roles: /support|customer/i,
    tone: "bg-accent-soft text-accent",
  },
];

export function DepartmentsPanel({
  agents,
  state = "ready",
}: {
  agents: Agent[];
  state?: DataState;
}) {
  const hired = agents.filter((agent) => agent.hired);

  return (
    <section aria-labelledby="departments-title" className="min-w-0">
      <Card className="flux-panel flex h-full flex-col p-4 sm:p-5">
        <SectionHeading
          title="AI Departments"
          sub="Each card opens the closest workspace. Planned surfaces are marked."
          action={{ href: "/departments", label: "Manage" }}
        />
        <div className="mt-3 grid grid-cols-2 gap-2">
          {DEPARTMENTS.map((department) => {
            const Icon = department.icon;
            const matched = hired.filter((agent) => department.roles.test(agent.role)).length;
            return (
              <Link
                key={department.id}
                href={department.href}
                aria-label={`${department.name} — ${department.planned ? "planned" : "workspace available"}`}
                className="flux-stat group relative rounded-control p-2.5"
              >
                <div className="flex items-start justify-between gap-1">
                  <span
                    className={cn(
                      "inline-flex h-7 w-7 items-center justify-center rounded-[0.6rem]",
                      department.tone,
                    )}
                  >
                    <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                  </span>
                  <span
                    className={cn(
                      "mt-1 h-1.5 w-1.5 shrink-0 rounded-full shadow-[0_0_8px_currentColor]",
                      department.planned ? "bg-warning-fg" : "bg-success-fg",
                    )}
                    title={department.planned ? "Planned surface" : "Operational workspace"}
                  />
                </div>
                <p className="mt-2 truncate text-caption font-bold text-fg">{department.name}</p>
                <p className="truncate text-micro text-fg-subtle">{department.focus}</p>
                <p className="mt-1.5 truncate text-micro font-medium text-fg-muted">
                  {state === "loading"
                    ? "Loading agents…"
                    : state === "error"
                      ? "Team unavailable"
                      : matched > 0
                        ? `${matched} AI role${matched === 1 ? "" : "s"}`
                        : "No assigned agent"}
                </p>
                <span className="mt-1 block text-micro font-semibold text-brand opacity-0 transition-opacity group-hover:opacity-100">
                  {department.planned ? "View preview →" : "Open workspace →"}
                </span>
              </Link>
            );
          })}
        </div>
        <p className="mt-2.5 text-micro leading-4 text-fg-subtle">
          Planned cards open a preview page; they are not connected to a backend.
        </p>
      </Card>
    </section>
  );
}

const QUICK_ACTIONS = [
  { label: "Create task", href: "/projects/new", icon: PlusCircle, planned: false },
  { label: "Agents", href: "/agents", icon: Bot, planned: false },
  { label: "Plugins", href: "/marketplace", icon: ShoppingBag, planned: false },
  { label: "Analytics", href: "/analytics", icon: BarChart3, planned: false },
  { label: "Automation", href: "/automations", icon: Zap, planned: true },
  { label: "Creative Studio", href: "/brand", icon: Palette, planned: true },
];

export function QuickActions() {
  return (
    <section aria-labelledby="quick-actions-title" className="min-w-0">
      <Card className="flux-panel flex h-full flex-col p-4 sm:p-5">
        <SectionHeading title="Quick actions" sub="Direct routes into existing capabilities." />
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {QUICK_ACTIONS.map((action) => {
            const Icon = action.icon;
            return (
              <Link
                key={action.label}
                href={action.href}
                className="flux-stat group flex flex-col gap-2 rounded-control p-3"
              >
                <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[0.6rem] bg-gradient-to-br from-brand-soft to-accent-soft text-brand ring-1 ring-inset ring-brand/10 transition-all duration-200 group-hover:from-brand-solid group-hover:to-accent group-hover:text-on-brand">
                  <Icon aria-hidden="true" className="h-4 w-4" />
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-1 text-caption font-semibold text-fg">
                    <span className="truncate">{action.label}</span>
                    <ArrowUpRight
                      aria-hidden="true"
                      className="h-3 w-3 shrink-0 text-fg-subtle transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-brand"
                    />
                  </span>
                  <span className="mt-0.5 block text-micro text-fg-subtle">
                    {action.planned ? "Planned preview" : "Ready"}
                  </span>
                </span>
              </Link>
            );
          })}
        </div>
      </Card>
    </section>
  );
}

export function CommandPanel({
  mode,
  onOpen,
}: {
  mode: DashboardMode;
  onOpen: () => void;
}) {
  const suggestions = [
    { label: "Create a marketing campaign", href: goalHref("Create a marketing campaign for the business.") },
    { label: "Analyze this month's performance", href: "/analytics" },
    { label: "Open the AI Office", href: "/office" },
    { label: "Hire an AI specialist", href: "/marketplace" },
  ];

  return (
    <Card className="flux-panel relative overflow-hidden p-4 sm:p-5">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -right-12 -top-16 h-44 w-44 rounded-full bg-holo/10 blur-3xl"
      />
      <div className="relative flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-[0.65rem] bg-gradient-to-br from-brand-solid to-accent text-on-brand shadow-card ring-1 ring-inset ring-white/20">
            <Sparkles aria-hidden="true" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-body-sm font-bold text-fg">Fluxus Assistant</p>
            <p className="truncate text-micro text-fg-subtle">
              {mode === "live" ? "Command palette · live data" : "Command palette · demo data"}
            </p>
          </div>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-success-line bg-success-soft px-1.5 py-0.5 text-micro font-semibold text-success-fg">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
          Ready
        </span>
      </div>

      <p className="relative mt-4 text-heading-sm font-bold text-fg">How can I help today?</p>
      <button
        type="button"
        onClick={onOpen}
        className="relative mt-2 flex w-full items-center gap-2 rounded-control border border-line bg-surface-sunken/70 px-3 py-2.5 text-left text-caption text-fg-subtle transition-colors hover:border-brand/35 hover:text-fg"
      >
        <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-brand" />
        <span className="flex-1">Ask Fluxus anything…</span>
        <kbd className="rounded border border-line bg-surface px-1 py-0.5 text-micro font-semibold text-fg-muted">
          Ctrl K
        </kbd>
      </button>

      <ul className="relative mt-3 flex flex-col gap-1.5">
        {suggestions.map((suggestion) => (
          <li key={suggestion.label}>
            <Link
              href={suggestion.href}
              className="group flex items-center gap-2 rounded-control border border-transparent px-2.5 py-2 text-caption font-medium text-fg-muted transition-colors hover:border-line hover:bg-surface-hover hover:text-fg"
            >
              <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-fg-subtle transition-colors group-hover:text-brand" />
              <span className="min-w-0 flex-1 truncate">{suggestion.label}</span>
              <ChevronRight
                aria-hidden="true"
                className="h-3 w-3 shrink-0 text-fg-subtle opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
              />
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Module-scope row: declaring it inside the panel would remount on render. */
function HealthRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line-subtle py-1.5 last:border-0">
      <span className="text-caption text-fg-subtle">{label}</span>
      <span className="truncate text-caption font-semibold tabular-nums text-fg">{value}</span>
    </div>
  );
}

export function HealthPanel({
  mode,
  pendingApprovals,
  activeTasks,
  totalTasks,
  healthScore,
  lastActivity,
  degraded,
}: {
  mode: DashboardMode;
  pendingApprovals: number;
  activeTasks: number;
  totalTasks: number;
  healthScore?: number;
  lastActivity?: string;
  degraded?: boolean;
}) {
  const stateLabel = degraded
    ? "Attention needed"
    : pendingApprovals > 0
      ? "Approvals waiting"
      : "All systems operational";
  const stateTone = degraded ? "text-danger-fg" : pendingApprovals > 0 ? "text-warning-fg" : "text-success-fg";

  return (
    <Card className="flux-panel p-4 sm:p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-body-sm font-bold text-fg">System health</h2>
        <span className={cn("flex items-center gap-1.5 text-caption font-medium", stateTone)}>
          <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full bg-current", !degraded && "animate-pulse-dot")} />
          {stateLabel}
        </span>
      </div>
      <div className="mt-2 flex flex-col">
        <HealthRow label="Data source" value={mode === "live" ? "Server" : "Demo fixture"} />
        <HealthRow label="Pending approvals" value={String(pendingApprovals)} />
        <HealthRow label="Active work" value={`${activeTasks} of ${totalTasks} tasks`} />
        {typeof healthScore === "number" && (
          <HealthRow label="Business health" value={`${healthScore}/100`} />
        )}
        {lastActivity && <HealthRow label="Last activity" value={lastActivity} />}
      </div>
      <div className="mt-2.5 flex items-center gap-2">
        <ProgressBar value={totalTasks === 0 ? 0 : (activeTasks / totalTasks) * 100} className="h-1" />
        <span className="shrink-0 text-micro tabular-nums text-fg-subtle">
          {totalTasks === 0 ? "0%" : `${Math.round((activeTasks / totalTasks) * 100)}%`}
        </span>
      </div>
    </Card>
  );
}

function activityTone(kind: ActivityEvent["kind"]) {
  switch (kind) {
    case "approval":
      return "bg-warning-fg";
    case "result":
      return "bg-success-fg";
    case "system":
      return "bg-info";
    default:
      return "bg-brand";
  }
}

export function RecentActivity({
  events,
  state = "ready",
  error,
}: {
  events: ActivityEvent[];
  state?: DataState;
  error?: string | null;
}) {
  return (
    <section aria-labelledby="activity-title" className="min-w-0">
      <Card className="flux-panel flex h-full flex-col p-4 sm:p-5">
        <SectionHeading
          title="Recent activity"
          sub="Agent events from the active workspace."
          action={{ href: "/activity", label: "View all" }}
        />
        {state === "error" ? (
          <p role="alert" className="mt-3 rounded-control border border-danger-line bg-danger-soft p-3 text-caption text-danger-fg">
            {error ?? "Activity is unavailable."}
          </p>
        ) : state === "loading" || state === "idle" ? (
          <div role="status" aria-label="Loading activity" className="mt-3 flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-2">
                <Skeleton className="h-2 w-2 rounded-full" />
                <Skeleton className="h-3 flex-1" />
                <Skeleton className="h-3 w-12" />
              </div>
            ))}
          </div>
        ) : events.length === 0 ? (
          <p className="mt-3 rounded-control border border-dashed border-line bg-surface-sunken/50 p-3 text-caption text-fg-muted">
            No activity recorded yet.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col">
            {events.slice(0, 4).map((event) => (
              <li
                key={event.id}
                className="flex items-start gap-2 border-b border-line-subtle py-2 last:border-0"
              >
                <span
                  aria-hidden="true"
                  className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", activityTone(event.kind))}
                />
                <p className="min-w-0 flex-1 text-caption leading-5 text-fg-muted">
                  <span className="font-semibold text-fg">{event.agentName}</span> — {event.text}
                </p>
                <span className="shrink-0 text-micro text-fg-subtle">{event.at}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}
