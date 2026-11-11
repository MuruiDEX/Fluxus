"use client";

import { memo, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AgentStatusBadge, Avatar, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, Skeleton } from "@/components/system/ui";
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

function OfficeAgentsSkeleton() {
  return (
    <div role="status" aria-label="Loading AI team" className="grid gap-2.5 sm:grid-cols-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-2xl border border-mist-200 bg-white/90 p-3 shadow-card backdrop-blur dark:border-white/10 dark:bg-night-800/90"
        >
          <Skeleton className="h-[38px] w-[38px] shrink-0 rounded-full" />
          <div className="min-w-0 flex-1">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="mt-1.5 h-3 w-32" />
            <Skeleton className="mt-1 h-5 w-20 rounded-full" />
          </div>
          <div className="hidden text-right sm:block">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-1 h-3 w-14" />
          </div>
        </div>
      ))}
    </div>
  );
}

function LiveAgentsSkeleton() {
  return (
    <div role="status" aria-label="Loading AI team" className="flex flex-col gap-1.5">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="flex items-center gap-2.5 rounded-xl border border-mist-200 bg-white/90 px-3 py-2 dark:border-white/10 dark:bg-night-800/90"
        >
          <Skeleton className="h-[26px] w-[26px] shrink-0 rounded-full" />
          <Skeleton className="h-3 w-32 shrink-0" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-5 w-20 shrink-0 rounded-full" />
        </div>
      ))}
    </div>
  );
}

function TimelineSkeleton() {
  return (
    <div role="status" aria-label="Loading timeline" className="flex flex-col gap-0">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-3">
          <div className="flex flex-col items-center">
            <Skeleton className="mt-1.5 h-2 w-2 shrink-0 rounded-full" />
            {i < 3 && <Skeleton className="mt-1 w-px flex-1" />}
          </div>
          <div className="min-w-0 flex-1 pb-3">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="mt-1.5 h-3 w-full max-w-[280px]" />
          </div>
        </div>
      ))}
    </div>
  );
}

function WhiteboardSkeleton() {
  return (
    <div role="status" aria-label="Loading tasks" className="grid gap-2 sm:col-span-2 sm:grid-cols-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="rounded-2xl border border-mist-200 bg-white/90 p-3 dark:border-white/10 dark:bg-night-800/90"
        >
          <div className="flex items-start justify-between gap-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-5 w-20 shrink-0 rounded-full" />
          </div>
          <Skeleton className="mt-2 h-1.5 w-full rounded-full" />
          <Skeleton className="mt-1.5 h-3 w-3/4" />
        </div>
      ))}
    </div>
  );
}

const LiveOfficeTimeline = memo(function LiveOfficeTimeline() {
  const status = useLiveActivity((s) => s.status);
  const events = useLiveActivity((s) => s.events);
  const error = useLiveActivity((s) => s.error);

  return (
    <div className="flex max-h-[420px] flex-col gap-0 overflow-y-auto">
      {(status === "idle" || status === "loading") && <TimelineSkeleton />}
      {status === "error" && (
        <p role="alert" className="text-[13px] text-red-500">{error}</p>
      )}
      {status === "ready" && events.length === 0 && (
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
  );
});

const LiveOfficeFeed = memo(function LiveOfficeFeed() {
  const status = useLiveActivity((s) => s.status);
  const events = useLiveActivity((s) => s.events);
  const error = useLiveActivity((s) => s.error);
  const pendingCount = useLiveApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );

  return (
    <div className="flex flex-col gap-3">
      <Card className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-[14px] font-bold">Activity feed</h2>
          <span className="text-[11px] text-ink-400">server events</span>
        </div>
        <div className="flex max-h-[380px] flex-col overflow-y-auto">
          {(status === "idle" || status === "loading") && (
            <p role="status" className="text-[12.5px] text-ink-400">Loading activity…</p>
          )}
          {status === "error" && (
            <p role="alert" className="text-[12.5px] text-red-500">{error}</p>
          )}
          {status === "ready" && events.length === 0 && (
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
      {pendingCount > 0 && (
        <Card className="border-orange-200 bg-orange-50 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
          <p className="text-[13px] font-bold text-orange-700 dark:text-orange-300">
            {pendingCount} approval{pendingCount === 1 ? "" : "s"} need
            {pendingCount === 1 ? "s" : ""} a human
          </p>
          <p className="text-[12.5px] text-orange-600/90 dark:text-orange-200/80">
            Nothing deploys, spends or sends without you.
          </p>
          <Link
            href="/approvals"
            className="mt-2 inline-block text-[12.5px] font-bold underline"
          >
            Open Approval Inbox →
          </Link>
        </Card>
      )}
    </div>
  );
});

const LiveOfficeWhiteboard = memo(function LiveOfficeWhiteboard() {
  const taskStatus = useLiveTasks((s) => s.status);
  const tasks = useLiveTasks((s) => s.tasks);
  const taskError = useLiveTasks((s) => s.error);
  const agents = useLiveAgentsList((s) => s.agents);
  const projectsStatus = useLiveProjects((s) => s.status);
  const projectsError = useLiveProjects((s) => s.error);

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {projectsStatus === "error" ? (
        <p role="alert" className="text-[13px] text-red-500">{projectsError}</p>
      ) : taskStatus === "idle" || taskStatus === "loading" ? (
        <WhiteboardSkeleton />
      ) : taskStatus === "error" ? (
        <p role="alert" className="text-[13px] text-red-500">{taskError}</p>
      ) : null}
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
      {taskStatus === "ready" && tasks.length === 0 && (
        <p className="text-[13px] text-ink-400">No tasks yet.</p>
      )}
    </div>
  );
});

export function LiveOffice({ businessId }: { businessId: string }) {
  const agents = useLiveAgentsList((s) => s.agents);
  const agentsStatus = useLiveAgentsList((s) => s.status);
  const agentsError = useLiveAgentsList((s) => s.error);
  const projects = useLiveProjects((s) => s.projects);
  const projectsStatus = useLiveProjects((s) => s.status);
  const [view, setView] = useState<(typeof views)[number]>("Live");

  useEffect(() => {
    void useLiveAgentsList.getState().load(businessId);
    void useLiveProjects.getState().load(businessId);
    void useLiveApprovals.getState().load(businessId);
    void useLiveActivity.getState().load({ businessId });
    return () => {
      useLiveAgentsList.getState().reset();
      useLiveProjects.getState().reset();
      useLiveTasks.getState().reset();
      useLiveApprovals.getState().reset();
      useLiveActivity.getState().reset();
    };
  }, [businessId]);

  const projectIds = useMemo(
    () => projects.map((project) => project.id).sort().join(","),
    [projects],
  );

  useEffect(() => {
    if (projectsStatus !== "ready") return;
    const ids = projectIds ? projectIds.split(",") : [];
    void useLiveTasks.getState().loadAll(businessId, ids);
  }, [businessId, projectIds, projectsStatus]);

  const hiredAgents = agents.filter((agent) => agent.hired);
  const workingNow = hiredAgents.filter(
    (agent) => agent.status === "working" || agent.status === "thinking",
  );
  const activeProject = projects.find((project) => project.status !== "completed") ?? projects[0];
  const agentsReady = agentsStatus === "ready";

  return (
    <>
      <PageHeader
        title="AI Office"
        badge={<LiveBadge />}
        sub={
          agentsReady
            ? `${hiredAgents.length} AI employee${hiredAgents.length === 1 ? "" : "s"} · ${workingNow.length} active now`
            : agentsStatus === "error"
              ? "AI team unavailable"
              : "Loading AI team…"
        }
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
            (agentsStatus === "error" ? (
              <p role="alert" className="text-[13px] text-red-500">{agentsError}</p>
            ) : !agentsReady ? (
              view === "Office" ? <OfficeAgentsSkeleton /> : <LiveAgentsSkeleton />
            ) : hiredAgents.length === 0 ? (
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

          {view === "Timeline" && <LiveOfficeTimeline />}

          {view === "Whiteboard" && <LiveOfficeWhiteboard />}
        </Card>

        <LiveOfficeFeed />
      </div>
    </>
  );
}
