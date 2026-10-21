"use client";

import { useEffect, useMemo, useState } from "react";
import { AgentStatusBadge, Avatar, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader } from "@/components/system/ui";
import { formatWhen } from "@/lib/live/datetime";
import {
  useLiveActivity,
  useLiveAgentsList,
  useLiveApprovals,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";
import { cn } from "@/lib/cn";

const views = ["Office", "Live", "Timeline", "Whiteboard"] as const;

export function LiveOffice({ businessId }: { businessId: string }) {
  const agentsSlice = useLiveAgentsList();
  const projectsSlice = useLiveProjects();
  const tasksSlice = useLiveTasks();
  const approvalsSlice = useLiveApprovals();
  const activitySlice = useLiveActivity();
  const [view, setView] = useState<(typeof views)[number]>("Live");

  useEffect(() => {
    void agentsSlice.load(businessId);
    void projectsSlice.load(businessId);
    void approvalsSlice.load(businessId);
    void activitySlice.load({ businessId });
    return () => {
      agentsSlice.reset();
      projectsSlice.reset();
      tasksSlice.reset();
      approvalsSlice.reset();
      activitySlice.reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId]);

  const projects = projectsSlice.projects;
  const projectIds = useMemo(
    () => projects.map((project) => project.id).sort().join(","),
    [projects],
  );

  useEffect(() => {
    if (projectsSlice.status !== "ready") return;
    const ids = projectIds ? projectIds.split(",") : [];
    void tasksSlice.loadAll(businessId, ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, projectIds, projectsSlice.status]);

  const agents = agentsSlice.agents;
  const tasks = tasksSlice.tasks;
  const events = activitySlice.events;
  const pending = approvalsSlice.approvals.filter((approval) => approval.status === "pending");
  const hiredAgents = agents.filter((agent) => agent.hired);
  const workingNow = hiredAgents.filter(
    (agent) => agent.status === "working" || agent.status === "thinking",
  );
  const activeProject = projects.find((project) => project.status !== "completed") ?? projects[0];

  return (
    <>
      <PageHeader
        title="AI Office"
        badge={<LiveBadge />}
        sub={`${hiredAgents.length} AI employee${hiredAgents.length === 1 ? "" : "s"} · ${workingNow.length} active now`}
        actions={
          <div className="flex items-center gap-1 rounded-[10px] border border-mist-200 bg-white p-1 dark:border-white/10 dark:bg-night-900">
            <span className="hidden px-2 text-[12px] text-ink-400 sm:block">View:</span>
            {views.map((entry) => (
              <button
                key={entry}
                onClick={() => setView(entry)}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
                  view === entry
                    ? "bg-brand-50 text-brand-600 dark:bg-brand-500/20 dark:text-sky-300"
                    : "text-ink-500 hover:text-ink-950 dark:text-slate-400",
                )}
              >
                {entry}
              </button>
            ))}
          </div>
        }
      />

      <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
        <Card className="bg-office-grid overflow-hidden p-4 dark:bg-night-900">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-[13px] font-semibold">
              {view === "Timeline"
                ? "Execution timeline"
                : view === "Whiteboard"
                  ? "Plan whiteboard"
                  : activeProject
                    ? `Floor — ${activeProject.title}`
                    : "Floor — AI Office"}
            </p>
            <span className="flex items-center gap-1.5 text-[12px] text-emerald-600">
              <span className="h-2 w-2 rounded-full bg-emerald-500" /> Server data
            </span>
          </div>

          {(view === "Office" || view === "Live") &&
            (hiredAgents.length === 0 ? (
              <p className="rounded-xl bg-mist-50 p-3 text-[13px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
                No AI employees yet. Hire a specialist from the marketplace.
              </p>
            ) : view === "Office" ? (
              <div className="grid gap-2.5 sm:grid-cols-2">
                {hiredAgents.map((agent) => (
                  <div
                    key={agent.id}
                    className="flex items-center gap-3 rounded-2xl border border-mist-200 bg-white/90 p-3 shadow-card backdrop-blur dark:border-white/10 dark:bg-night-800/90"
                  >
                    <Avatar name={agent.name} hue={agent.avatarHue} size={38} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-bold">{agent.role}</p>
                      <p className="truncate text-[12px] text-ink-500 dark:text-slate-400">
                        {agent.currentTask ?? "Standing by"}
                      </p>
                      <div className="mt-1">
                        <AgentStatusBadge status={agent.status} live />
                      </div>
                    </div>
                    <div className="hidden text-right text-[11px] tabular-nums text-ink-400 sm:block">
                      <p>{agent.performance.completionRate}% done</p>
                      <p>~{agent.performance.avgMinutes}m avg</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {hiredAgents.map((agent) => (
                  <div
                    key={agent.id}
                    className="flex items-center gap-2.5 rounded-xl border border-mist-200 bg-white/90 px-3 py-2 dark:border-white/10 dark:bg-night-800/90"
                  >
                    <Avatar name={agent.name} hue={agent.avatarHue} size={26} />
                    <p className="w-32 shrink-0 truncate text-[12.5px] font-semibold">
                      {agent.role}
                    </p>
                    <p className="min-w-0 flex-1 truncate text-[12px] text-ink-500">
                      {agent.currentTask ?? "Standing by"}
                    </p>
                    <AgentStatusBadge status={agent.status} live />
                  </div>
                ))}
              </div>
            ))}

          {view === "Timeline" && (
            <div className="flex max-h-[420px] flex-col gap-0 overflow-y-auto">
              {activitySlice.status === "loading" && (
                <p className="text-[13px] text-ink-400">Loading timeline…</p>
              )}
              {activitySlice.status === "error" && (
                <p className="text-[13px] text-red-500">{activitySlice.error}</p>
              )}
              {activitySlice.status === "ready" && events.length === 0 && (
                <p className="text-[13px] text-ink-400">No activity recorded yet.</p>
              )}
              {events.map((event, index) => (
                <div key={event.id} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span
                      className={cn(
                        "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                        event.kind === "approval"
                          ? "bg-orange-400"
                          : event.kind === "result"
                            ? "bg-emerald-400"
                            : event.kind === "system"
                              ? "bg-violet-400"
                              : "bg-sky-400",
                      )}
                    />
                    {index < events.length - 1 && (
                      <span className="w-px flex-1 bg-mist-200 dark:bg-white/10" />
                    )}
                  </div>
                  <div className="pb-3">
                    <p className="text-[12.5px]">
                      <span className="font-semibold">{event.agentName}</span>{" "}
                      <span className="text-ink-400">· {formatWhen(event.at)}</span>
                    </p>
                    <p className="text-[12.5px] text-ink-700 dark:text-slate-300">{event.text}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {view === "Whiteboard" && (
            <div className="grid gap-2 sm:grid-cols-2">
              {tasksSlice.status === "loading" && (
                <p className="text-[13px] text-ink-400">Loading tasks…</p>
              )}
              {tasksSlice.status === "error" && (
                <p className="text-[13px] text-red-500">{tasksSlice.error}</p>
              )}
              {tasks.map((task) => {
                const owner = agents.find((agent) => agent.id === task.ownerAgentId);
                const deps = task.dependsOn
                  .map((dependency) => tasks.find((item) => item.id === dependency)?.title ?? dependency)
                  .filter(Boolean);
                return (
                  <div
                    key={task.id}
                    className="rounded-2xl border border-mist-200 bg-white/90 p-3 dark:border-white/10 dark:bg-night-800/90"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[13px] font-bold">{task.title}</p>
                      <TaskStatusBadge status={task.status} />
                    </div>
                    <ProgressBar value={task.progress} className="mt-2" />
                    <p className="mt-1.5 text-[11.5px] text-ink-400">
                      {owner?.role ?? "Unassigned"}
                      {deps.length > 0
                        ? `  ← after ${deps.join(", ")}`
                        : "  ← no dependencies"}
                      {task.approvalGateId ? `  · gate @ ${task.gateAt ?? 100}%` : ""}
                    </p>
                  </div>
                );
              })}
              {tasksSlice.status === "ready" && tasks.length === 0 && (
                <p className="text-[13px] text-ink-400">No tasks yet.</p>
              )}
            </div>
          )}
        </Card>

        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[14px] font-bold">Activity feed</h2>
              <span className="text-[11px] text-ink-400">server events</span>
            </div>
            <div className="flex max-h-[380px] flex-col overflow-y-auto">
              {events.length === 0 && activitySlice.status !== "loading" && (
                <p className="text-[12.5px] text-ink-400">No activity recorded yet.</p>
              )}
              {events.map((event) => (
                <div
                  key={event.id}
                  className="flex items-start gap-2 border-b border-mist-100 py-2 text-[12.5px] last:border-0 dark:border-white/5"
                >
                  <span
                    className={cn(
                      "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                      event.kind === "approval"
                        ? "bg-orange-400"
                        : event.kind === "result"
                          ? "bg-emerald-400"
                          : event.kind === "system"
                            ? "bg-violet-400"
                            : "bg-sky-400",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="leading-5">
                      <span className="font-semibold">{event.agentName}</span>
                      <span className="text-ink-400"> · {formatWhen(event.at)}</span>
                    </p>
                    <p className="leading-5 text-ink-700 dark:text-slate-300">{event.text}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
          {pending.length > 0 && (
            <Card className="border-orange-200 bg-orange-50 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
              <p className="text-[13px] font-bold text-orange-700 dark:text-orange-300">
                {pending.length} approval{pending.length === 1 ? "" : "s"} need
                {pending.length === 1 ? "s" : ""} a human
              </p>
              <p className="text-[12.5px] text-orange-600/90 dark:text-orange-200/80">
                Nothing deploys, spends or sends without you.
              </p>
              <a
                href="/approvals"
                className="mt-2 inline-block text-[12.5px] font-bold underline"
              >
                Open Approval Inbox →
              </a>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
