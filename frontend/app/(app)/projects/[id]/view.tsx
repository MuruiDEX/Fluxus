"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Circle, Loader2, Lock } from "lucide-react";
import { Avatar, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { EngineControls } from "@/components/system/EngineControls";
import { TaskRunButton } from "@/components/system/TaskRun";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgents, useLiveProject, useLiveTasks } from "@/lib/store/live";
import { useAgents, useApprovals, useProjects } from "@/lib/store/app";
import { cn } from "@/lib/cn";
import type { Agent, Approval, Project, Task } from "@/lib/domain/types";

const tabs = ["Overview", "Tasks", "Team", "Research", "Finance", "Brand", "Website", "Marketing", "CRM", "Docs"] as const;

function ProjectLayout({
  project,
  projectTasks,
  agents,
  approvals,
  badge,
  engineSlot,
  renderTaskAction,
}: {
  project: Project;
  projectTasks: Task[];
  agents: Agent[];
  approvals: Approval[];
  badge?: ReactNode;
  engineSlot: ReactNode;
  renderTaskAction?: (task: Task) => ReactNode;
}) {
  const [tab, setTab] = useState<(typeof tabs)[number]>("Overview");
  const agentOf = (agentId: string) => agents.find((a) => a.id === agentId);
  const taskTitle = (taskId: string) => projectTasks.find((t) => t.id === taskId)?.title ?? taskId;
  const statusPill =
    project.status === "completed"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
      : "bg-blue-50 text-brand-600 dark:bg-brand-500/15 dark:text-sky-300";
  const statusLabel = project.status === "completed" ? "Completed" : project.status === "planning" ? "Planning" : project.status === "review" ? "In Review" : "In Progress";

  return (
    <>
      <p className="text-[12px] text-ink-400">
        <span className="hover:underline">Projects</span> › {project.title}
      </p>
      <PageHeader
        title={project.title}
        sub={`Due ${project.deadline} · AI $${project.aiSpent.toFixed(1)} / $${project.aiBudget}`}
        badge={badge}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {engineSlot}
            <span data-testid="project-status" className={cn("rounded-full px-2.5 py-1 text-[12px] font-semibold", statusPill)}>
              {statusLabel}
            </span>
          </div>
        }
      />

      <div className="flex gap-1 overflow-x-auto border-b border-mist-200 dark:border-white/10">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
              tab === t
                ? "border-brand-500 text-brand-600 dark:text-sky-300"
                : "border-transparent text-ink-500 hover:text-ink-950 dark:text-slate-400",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {(tab === "Overview" || tab === "Tasks") && (
        <div className="grid gap-3 xl:grid-cols-[280px_1fr_260px]">
          <Card className="h-fit p-4">
            <h3 className="text-[13px] font-bold">Project goal</h3>
            <p className="mt-1 text-[12.5px] leading-5 text-ink-700 dark:text-slate-300">{project.goal}</p>
            <div className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
              <div className="rounded-xl bg-mist-50 p-2.5 dark:bg-white/5">
                <p className="text-ink-400">Budget</p>
                <p className="font-bold tabular-nums">${project.budget.toLocaleString("en-US")}</p>
              </div>
              <div className="rounded-xl bg-mist-50 p-2.5 dark:bg-white/5">
                <p className="text-ink-400">Timeline</p>
                <p className="font-bold">3 months</p>
              </div>
            </div>
            <h3 className="mb-1.5 mt-4 text-[13px] font-bold">Key milestones</h3>
            <div className="flex flex-col gap-1.5">
              {project.milestones.map((m) => (
                <p key={m.label} className="flex items-center gap-2 text-[12.5px]">
                  {m.done ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                  ) : (
                    <Circle className="h-4 w-4 shrink-0 text-ink-400" />
                  )}
                  <span className={m.done ? "" : "text-ink-500"}>{m.label}</span>
                </p>
              ))}
            </div>
          </Card>

          <Card className="p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-[13px] font-bold">Task graph</h3>
              <span className="text-[12px] tabular-nums text-ink-400">{project.progress}%</span>
            </div>
            <ProgressBar value={project.progress} className="mb-3" />
            <div className="grid gap-2 sm:grid-cols-2">
              {projectTasks.map((t) => {
                const a = agentOf(t.ownerAgentId);
                const blocked = t.dependsOn.length > 0 && t.status !== "completed";
                return (
                  <div key={t.id} className="rounded-2xl border border-mist-200 p-3 dark:border-white/10">
                    <div className="flex items-start justify-between gap-2">
                      <p className="flex items-center gap-1.5 text-[13px] font-semibold">
                        {t.status === "working" && <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-500" />}
                        {blocked && t.status !== "working" && <Lock className="h-3.5 w-3.5 text-amber-500" />}
                        {t.title}
                      </p>
                      <TaskStatusBadge status={t.status} />
                    </div>
                    {t.status === "working" && <ProgressBar value={t.progress} className="mt-2" />}
                    <p className="mt-1.5 line-clamp-2 text-[12px] text-ink-500">{t.summary}</p>
                    {t.dependsOn.length > 0 && (
                      <p className="mt-1 text-[11.5px] text-ink-400">
                        After: {t.dependsOn.map(taskTitle).join(", ")}
                      </p>
                    )}
                    {t.approvalGateId && (
                      <p className="mt-0.5 text-[11.5px] font-medium text-orange-600 dark:text-orange-300">
                        Gate: {approvals.find((a) => a.id === t.approvalGateId)?.title ?? t.approvalGateId} @ {t.gateAt ?? 100}%
                      </p>
                    )}
                    <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-ink-400">
                      {a && <Avatar name={a.name} hue={a.avatarHue} size={18} />}
                      <span>{a?.role ?? t.ownerAgentId}</span>
                      <span className="ml-auto tabular-nums">${t.costAccrued.toFixed(2)}</span>
                    </div>
                    {renderTaskAction?.(t)}
                  </div>
                );
              })}
            </div>
          </Card>

          <Card className="h-fit p-4">
            <h3 className="mb-2 text-[13px] font-bold">Project team</h3>
            <div className="flex flex-col gap-2">
              {project.teamIds.map((aid) => {
                const a = agentOf(aid);
                if (!a) return null;
                return (
                  <div key={aid} className="flex items-center gap-2">
                    <Avatar name={a.name} hue={a.avatarHue} size={26} />
                    <div className="min-w-0">
                      <p className="truncate text-[12.5px] font-semibold">{a.role}</p>
                      <p className="truncate text-[11px] text-ink-400">{a.currentTask}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      )}

      {tab !== "Overview" && tab !== "Tasks" && (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">{tab} — connected to this project</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            Deep {tab.toLowerCase()} tooling is deferred. In this build the project graph, team and
            approvals carry the end-to-end demo; full {tab.toLowerCase()} modules land after the core loop is stable.
          </p>
        </Card>
      )}
    </>
  );
}

function DemoProjectDetail({ id }: { id: string }) {
  const { projects, tasks } = useProjects();
  const agents = useAgents((s) => s.agents);
  const project = useMemo(() => projects.find((p) => p.id === id) ?? projects[0], [projects, id]);
  const projectTasks = useMemo(() => tasks.filter((t) => t.projectId === project.id), [tasks, project.id]);
  const approvals = useApprovals((s) => s.approvals);

  return (
    <ProjectLayout
      project={project}
      projectTasks={projectTasks}
      agents={agents}
      approvals={approvals}
      engineSlot={<EngineControls scope={{ businessId: project.businessId, projectId: project.id }} compact />}
    />
  );
}

function LiveProjectDetail({ businessId, projectId }: { businessId: string; projectId: string }) {
  const { status, project, notFound, error, load, reset } = useLiveProject();
  const tasksSlice = useLiveTasks();
  const agents = useLiveAgents((s) => s.agents);
  const ensureAgents = useLiveAgents((s) => s.ensure);
  const resetAgents = useLiveAgents((s) => s.reset);

  useEffect(() => {
    void load(businessId, projectId);
    void tasksSlice.load(businessId, projectId);
    void ensureAgents(businessId);
    return () => {
      reset();
      tasksSlice.reset();
      resetAgents();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, projectId]);

  if (status === "loading" || status === "idle") {
    return (
      <>
        <PageHeader title="Project" sub="Loading from server…" badge={<LiveBadge />} />
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading project…</Card>
      </>
    );
  }

  if (status === "error" || !project) {
    return (
      <>
        <PageHeader title="Project" sub="Live server data" badge={<LiveBadge />} />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">
            {notFound ? "Project not found" : "Couldn't load project"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            {notFound
              ? "This project doesn't exist in your business or isn't available to you."
              : error}
          </p>
          {!notFound && (
            <div className="mt-3 flex justify-center">
              <PrimaryButton
                className="justify-center"
                onClick={() => {
                  void load(businessId, projectId);
                  void tasksSlice.load(businessId, projectId);
                }}
              >
                Retry
              </PrimaryButton>
            </div>
          )}
        </Card>
      </>
    );
  }

  return (
    <ProjectLayout
      project={project}
      projectTasks={tasksSlice.tasks}
      agents={agents}
      approvals={[]}
      badge={<LiveBadge />}
      engineSlot={
        <span
          title="Each task runs on the server when you press its Run button."
          className="rounded-full bg-mist-100 px-2.5 py-1 text-[11.5px] font-medium text-ink-500 dark:bg-white/5 dark:text-slate-400"
        >
          Server-side runs
        </span>
      }
      renderTaskAction={(t) => (
        <TaskRunButton businessId={businessId} projectId={project.id} taskId={t.id} />
      )}
    />
  );
}

export function ProjectDetail() {
  const params = useParams<{ id: string }>();
  const rawId = Array.isArray(params.id) ? params.id[0] : params.id;
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveProjectDetail businessId={currentBusinessId} projectId={rawId} />;
  }
  return <DemoProjectDetail id={rawId} />;
}
