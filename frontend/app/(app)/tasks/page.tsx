"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Filter, RotateCcw, TriangleAlert } from "lucide-react";
import { Avatar, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import {
  Card,
  EmptyState,
  LiveBadge,
  PageHeader,
  PrimaryButton,
  Skeleton,
} from "@/components/system/ui";
import { buttonClass } from "@/components/system/controls";
import { TaskEditButton } from "@/components/system/TaskEdit";
import { TaskRunButton } from "@/components/system/TaskRun";
import { failTaskInExecution, retryTaskInExecution } from "@/lib/sim/engine";
import { useSession } from "@/lib/store/auth";
import {
  isLiveContext,
  useLiveAgents,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";
import { useAgents, useProjects } from "@/lib/store/app";
import { cn } from "@/lib/cn";
import type { Agent, Project, Task, TaskStatus } from "@/lib/domain/types";

const filters: ("all" | TaskStatus)[] = [
  "all",
  "working",
  "waiting_dependency",
  "waiting_assets",
  "waiting_approval",
  "not_started",
  "completed",
  "failed",
];

const filterLabels: Record<"all" | TaskStatus, string> = {
  all: "All",
  working: "Working",
  waiting_dependency: "Blocked",
  waiting_assets: "Waiting for assets",
  waiting_approval: "Waiting for approval",
  not_started: "Not started",
  completed: "Completed",
  failed: "Failed",
};

function TaskCards({
  tasks,
  agents,
  projects,
  demoActions,
  renderAction,
}: {
  tasks: Task[];
  agents: Agent[];
  projects: Project[];
  demoActions: boolean;
  renderAction?: (task: Task) => ReactNode;
}) {
  const [filter, setFilter] = useState<(typeof filters)[number]>("all");
  const visible = tasks.filter((t) => filter === "all" || t.status === filter);

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Filter tasks by status">
        <span className="mr-1 inline-flex items-center gap-1 text-caption font-medium text-fg-subtle">
          <Filter aria-hidden="true" className="h-3.5 w-3.5" />
          Filter
        </span>
        {filters.map((f) => {
          const count = f === "all" ? tasks.length : tasks.filter((task) => task.status === f).length;
          return (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "rounded-full border px-3 py-1.5 text-caption font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
                filter === f
                  ? "border-brand-solid bg-brand-solid text-on-brand shadow-card"
                  : "border-line bg-surface text-fg-muted hover:border-brand/30 hover:bg-brand-soft hover:text-brand",
              )}
            >
              {filterLabels[f]}
              <span className={cn("ml-1.5 tabular-nums", filter === f ? "opacity-80" : "text-fg-subtle")}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {visible.length === 0 && (
        <Card className="p-8 text-center">
          <p className="text-body-sm text-fg-muted">
            {tasks.length === 0
              ? "No tasks yet. Plan a project to give the team work."
              : `No tasks with status “${filterLabels[filter]}”.`}
          </p>
        </Card>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {visible.map((t) => {
          const a = agents.find((x) => x.id === t.ownerAgentId);
          const p = projects.find((x) => x.id === t.projectId);
          const active = t.status === "working";
          return (
            <Card key={t.id} variant="interactive" className="flex h-full flex-col p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 text-body-sm font-semibold leading-snug text-fg">{t.title}</p>
                <TaskStatusBadge status={t.status} />
              </div>
              <p className="mt-1.5 line-clamp-2 text-caption leading-5 text-fg-muted">{t.summary}</p>

              <div className="mt-3 flex items-center gap-2">
                <ProgressBar value={t.progress} className="h-1" />
                <span className="w-9 shrink-0 text-right text-micro font-bold tabular-nums text-fg-muted">
                  {t.progress}%
                </span>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-fg-subtle">
                {a ? (
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <Avatar name={a.name} hue={a.avatarHue} size={20} />
                    <span className="truncate">{a.role}</span>
                  </span>
                ) : (
                  <span>{t.ownerAgentId}</span>
                )}
                <span aria-hidden="true">·</span>
                <span className="min-w-0 truncate">{p?.title ?? t.projectId}</span>
                {active && (
                  <span className="ml-auto inline-flex shrink-0 items-center gap-1 text-micro font-semibold text-brand">
                    <span className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-current" />
                    Running
                  </span>
                )}
              </div>

              {demoActions && (t.status === "working" || t.status === "failed") && (
                <div className="mt-3 flex flex-wrap gap-2 border-t border-line-subtle pt-3">
                  {t.status === "working" && (
                    <button
                      onClick={() => failTaskInExecution(t.id, "simulated tool error (demo)")}
                      className={buttonClass({ variant: "outline", size: "sm" })}
                    >
                      <TriangleAlert aria-hidden="true" className="h-3.5 w-3.5" />
                      Simulate failure
                    </button>
                  )}
                  {t.status === "failed" && (
                    <button
                      onClick={() => retryTaskInExecution(t.id)}
                      className={buttonClass({ variant: "primary", size: "sm" })}
                    >
                      <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
                      Retry{(t.attempts ?? 0) > 0 ? ` (attempt ${t.attempts})` : ""}
                    </button>
                  )}
                </div>
              )}
              {!demoActions && renderAction?.(t)}
            </Card>
          );
        })}
      </div>
    </>
  );
}

function DemoTasksView() {
  const { tasks, projects } = useProjects();
  const agents = useAgents((s) => s.agents);

  return (
    <>
      <PageHeader
        title="Tasks"
        sub="Parallel AI work with dependencies. Independent tasks run simultaneously."
      />
      <p className="-mt-2 text-caption text-fg-subtle">
        Demo controls rehearse failure handling — the engine, not the UI, owns recovery.
      </p>
      <TaskCards tasks={tasks} agents={agents} projects={projects} demoActions />
    </>
  );
}

function LiveTasksView({ businessId }: { businessId: string }) {
  const projectsSlice = useLiveProjects();
  const tasksSlice = useLiveTasks();
  const agents = useLiveAgents((s) => s.agents);
  const ensureAgents = useLiveAgents((s) => s.ensure);
  const resetAgents = useLiveAgents((s) => s.reset);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await projectsSlice.load(businessId);
      if (cancelled) return;
      const ids = useLiveProjects.getState().projects.map((p) => p.id);
      await tasksSlice.loadAll(businessId, ids);
    })();
    void ensureAgents(businessId);
    return () => {
      cancelled = true;
      projectsSlice.reset();
      tasksSlice.reset();
      resetAgents();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId]);

  const loading =
    projectsSlice.status === "loading" ||
    projectsSlice.status === "idle" ||
    tasksSlice.status === "loading" ||
    tasksSlice.status === "idle";
  const error = projectsSlice.status === "error" ? projectsSlice.error : tasksSlice.error;

  const retry = () => {
    void (async () => {
      await projectsSlice.load(businessId);
      const ids = useLiveProjects.getState().projects.map((p) => p.id);
      await tasksSlice.loadAll(businessId, ids);
    })();
  };

  return (
    <>
      <PageHeader
        title="Tasks"
        sub="Server tasks across your projects in this business."
        badge={<LiveBadge />}
      />
      {loading ? (
        <div className="flex flex-col gap-3" role="status" aria-label="Loading tasks">
          <div className="flex flex-wrap gap-1.5">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-8 w-24 rounded-full" />
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <Card key={i} className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-5 w-20 shrink-0 rounded-full" />
                </div>
                <Skeleton className="mt-2 h-3 w-full" />
                <Skeleton className="mt-1.5 h-3 w-4/5" />
                <Skeleton className="mt-3 h-1 w-full rounded-full" />
                <div className="mt-3 flex items-center gap-1.5">
                  <Skeleton className="h-5 w-5 shrink-0 rounded-full" />
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="ml-auto h-3 w-10 shrink-0" />
                </div>
              </Card>
            ))}
          </div>
        </div>
      ) : error ? (
        <Card className="p-8 text-center">
          <div role="alert">
            <p className="text-body font-semibold text-fg">Couldn&apos;t load tasks</p>
            <p className="mx-auto mt-1 max-w-md text-body-sm text-fg-muted">{error}</p>
          </div>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={retry}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : tasksSlice.tasks.length === 0 ? (
        <EmptyState
          title="No tasks in this business yet"
          body="Tasks created on the server will appear here."
        />
      ) : (
        <TaskCards
          tasks={tasksSlice.tasks}
          agents={agents}
          projects={projectsSlice.projects}
          demoActions={false}
          renderAction={(t) => (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line-subtle pt-3">
              <TaskEditButton businessId={businessId} task={t} />
              <TaskRunButton businessId={businessId} projectId={t.projectId} taskId={t.id} />
            </div>
          )}
        />
      )}
    </>
  );
}

export default function TasksPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveTasksView businessId={currentBusinessId} />;
  }
  return <DemoTasksView />;
}
