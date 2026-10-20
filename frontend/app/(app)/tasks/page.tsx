"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Avatar, TaskStatusBadge } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
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

const filters: ("all" | TaskStatus)[] = ["all", "working", "waiting_dependency", "waiting_assets", "not_started", "completed"];

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
      <div className="flex flex-wrap gap-1.5">
        {filters.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={cn(
              "rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-colors",
              filter === f ? "bg-brand-500 text-white" : "border border-mist-200 bg-white text-ink-500 hover:border-brand-200 dark:border-white/10 dark:bg-night-900",
            )}
          >
            {f === "all" ? `All (${tasks.length})` : f.replace(/_/g, " ")}
          </button>
        ))}
      </div>
      <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
        {visible.map((t) => {
          const a = agents.find((x) => x.id === t.ownerAgentId);
          const p = projects.find((x) => x.id === t.projectId);
          return (
            <Card key={t.id} className="p-3.5">
              <div className="flex items-start justify-between gap-2">
                <p className="text-[13.5px] font-semibold">{t.title}</p>
                <TaskStatusBadge status={t.status} />
              </div>
              <p className="mt-1 text-[12.5px] text-ink-500">{t.summary}</p>
              <div className="mt-2.5 flex items-center gap-1.5 text-[12px] text-ink-400">
                {a && <Avatar name={a.name} hue={a.avatarHue} size={20} />}
                <span>{a?.role ?? t.ownerAgentId}</span>
                <span>·</span>
                <span className="truncate">{p?.title ?? t.projectId}</span>
                <span className="ml-auto shrink-0 font-bold tabular-nums text-ink-700 dark:text-slate-200">{t.progress}%</span>
              </div>
              {demoActions && (t.status === "working" || t.status === "failed") && (
                <div className="mt-2 flex gap-2">
                  {t.status === "working" && (
                    <button
                      onClick={() => failTaskInExecution(t.id, "simulated tool error (demo)")}
                      className="h-7 rounded-lg border border-mist-200 px-2.5 text-[12px] font-medium text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:hover:bg-white/5"
                    >
                      Simulate failure
                    </button>
                  )}
                  {t.status === "failed" && (
                    <button
                      onClick={() => retryTaskInExecution(t.id)}
                      className="h-7 rounded-lg bg-brand-500 px-2.5 text-[12px] font-semibold text-white hover:opacity-90"
                    >
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
      <PageHeader title="Tasks" sub="Parallel AI work with dependencies. Independent tasks run simultaneously." />
      <p className="-mt-2 text-[12px] text-ink-400">Demo: use Fail / Retry to rehearse failure handling — the engine, not the UI, owns recovery.</p>
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
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading tasks…</Card>
      ) : error ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load tasks</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={retry}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : tasksSlice.tasks.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">No tasks in this business yet</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            Tasks created on the server will appear here.
          </p>
        </Card>
      ) : (
        <TaskCards
          tasks={tasksSlice.tasks}
          agents={agents}
          projects={projectsSlice.projects}
          demoActions={false}
          renderAction={(t) => (
            <div className="mt-2 flex flex-wrap items-center gap-2">
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
