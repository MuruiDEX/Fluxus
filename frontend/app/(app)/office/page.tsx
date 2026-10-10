"use client";

import { useEffect, useMemo, useState } from "react";
import { AgentStatusBadge, Avatar, ProgressBar, TaskStatusBadge } from "@/components/system/badges";
import { Card, PageHeader } from "@/components/system/ui";
import { EngineControls } from "@/components/system/EngineControls";
import { startLoop } from "@/lib/sim/engine";
import { useActivity, useAgents, useApprovals, useBusiness, useProjects } from "@/lib/store/app";
import { cn } from "@/lib/cn";

const views = ["Office", "Live", "Timeline", "Whiteboard"] as const;

export default function OfficePage() {
  const agents = useAgents((s) => s.agents);
  const events = useActivity((s) => s.events);
  const allApprovals = useApprovals((s) => s.approvals);
  const pending = useMemo(() => allApprovals.filter((a) => a.status === "pending").length, [allApprovals]);
  const { currentBusinessId } = useBusiness();
  const activeProjectId = useProjects((s) => s.activeProjectId);
  const allTasks = useProjects((s) => s.tasks);
  const tasks = useMemo(() => allTasks.filter((t) => t.projectId === activeProjectId), [allTasks, activeProjectId]);
  const [view, setView] = useState<(typeof views)[number]>("Live");
  const working = agents.filter((a) => a.hired).length;
  const hiredAgents = agents.filter((a) => a.hired);

  // The office comes alive on open; the guarded engine ignores duplicate starts.
  useEffect(() => {
    startLoop({ businessId: currentBusinessId, projectId: activeProjectId });
  }, [currentBusinessId, activeProjectId]);

  return (
    <>
      <PageHeader
        title="AI Office"
        sub={`${working} AI employees working`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <EngineControls scope={{ businessId: currentBusinessId, projectId: activeProjectId }} compact />
            <div className="flex items-center gap-1 rounded-[10px] border border-mist-200 bg-white p-1 dark:border-white/10 dark:bg-night-900">
            <span className="hidden px-2 text-[12px] text-ink-400 sm:block">View:</span>
            {views.map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
                  view === v ? "bg-brand-50 text-brand-600 dark:bg-brand-500/20 dark:text-sky-300" : "text-ink-500 hover:text-ink-950 dark:text-slate-400",
                )}
              >
                {v}
              </button>
            ))}
            </div>
          </div>
        }
      />

      <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
        <Card className="bg-office-grid overflow-hidden p-4 dark:bg-night-900">
          <div className="mb-1 flex items-center justify-between">
            <p className="text-[13px] font-semibold">
              {view === "Timeline" ? "Execution timeline" : view === "Whiteboard" ? "Plan whiteboard" : "Floor — Kraków launch team"}
            </p>
            <span className="flex items-center gap-1.5 text-[12px] text-emerald-600">
              <span className="animate-pulse-dot h-2 w-2 rounded-full bg-emerald-500" /> Live
            </span>
          </div>

          {(view === "Office" || view === "Live") && (
            view === "Office" ? (
              <div className="grid gap-2.5 sm:grid-cols-2">
                {hiredAgents.map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-3 rounded-2xl border border-mist-200 bg-white/90 p-3 shadow-card backdrop-blur transition-transform hover:-translate-y-0.5 dark:border-white/10 dark:bg-night-800/90"
                  >
                    <Avatar name={a.name} hue={a.avatarHue} size={38} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-bold">{a.role}</p>
                      <p className="truncate text-[12px] text-ink-500 dark:text-slate-400">
                        {a.currentTask ?? "Standing by"}
                      </p>
                      <div className="mt-1"><AgentStatusBadge status={a.status} live /></div>
                    </div>
                    <div className="hidden text-right text-[11px] tabular-nums text-ink-400 sm:block">
                      <p>{a.performance.completionRate}% done</p>
                      <p>~{a.performance.avgMinutes}m avg</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {hiredAgents.map((a) => (
                  <div key={a.id} className="flex items-center gap-2.5 rounded-xl border border-mist-200 bg-white/90 px-3 py-2 dark:border-white/10 dark:bg-night-800/90">
                    <Avatar name={a.name} hue={a.avatarHue} size={26} />
                    <p className="w-32 shrink-0 truncate text-[12.5px] font-semibold">{a.role}</p>
                    <p className="min-w-0 flex-1 truncate text-[12px] text-ink-500">{a.currentTask ?? "Standing by"}</p>
                    <AgentStatusBadge status={a.status} live />
                  </div>
                ))}
              </div>
            )
          )}

          {view === "Timeline" && (
            <div className="flex max-h-[420px] flex-col gap-0 overflow-y-auto">
              {events.map((e, i) => (
                <div key={e.id} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", e.kind === "approval" ? "bg-orange-400" : e.kind === "result" ? "bg-emerald-400" : e.kind === "system" ? "bg-violet-400" : "bg-sky-400")} />
                    {i < events.length - 1 && <span className="w-px flex-1 bg-mist-200 dark:bg-white/10" />}
                  </div>
                  <div className="pb-3">
                    <p className="text-[12.5px]"><span className="font-semibold">{e.agentName}</span> <span className="text-ink-400">· {e.at}</span></p>
                    <p className="text-[12.5px] text-ink-700 dark:text-slate-300">{e.text}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {view === "Whiteboard" && (
            <div className="grid gap-2 sm:grid-cols-2">
              {tasks.map((t) => {
                const owner = agents.find((a) => a.id === t.ownerAgentId);
                const deps = t.dependsOn.map((d) => tasks.find((x) => x.id === d)?.title ?? d).filter(Boolean);
                return (
                  <div key={t.id} className="rounded-2xl border border-mist-200 bg-white/90 p-3 dark:border-white/10 dark:bg-night-800/90">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[13px] font-bold">{t.title}</p>
                      <TaskStatusBadge status={t.status} />
                    </div>
                    <ProgressBar value={t.progress} className="mt-2" />
                    <p className="mt-1.5 text-[11.5px] text-ink-400">
                      {owner?.role ?? "Unassigned"}
                      {deps.length > 0 ? `  ← after ${deps.join(", ")}` : "  ← no dependencies"}
                      {t.approvalGateId ? `  · gate @ ${t.gateAt ?? 100}%` : ""}
                    </p>
                  </div>
                );
              })}
              {tasks.length === 0 && <p className="text-[13px] text-ink-400">No tasks in this project yet.</p>}
            </div>
          )}
        </Card>

        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[14px] font-bold">Activity feed</h2>
              <span className="text-[11px] text-ink-400">restrained, no spam</span>
            </div>
            <div className="flex max-h-[380px] flex-col overflow-y-auto">
              {events.map((e) => (
                <div key={e.id} className="flex items-start gap-2 border-b border-mist-100 py-2 text-[12.5px] last:border-0 dark:border-white/5">
                  <span
                    className={cn(
                      "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                      e.kind === "approval" ? "bg-orange-400" : e.kind === "result" ? "bg-emerald-400" : e.kind === "system" ? "bg-violet-400" : "bg-sky-400",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="leading-5">
                      <span className="font-semibold">{e.agentName}</span>
                      <span className="text-ink-400"> · {e.at}</span>
                    </p>
                    <p className="leading-5 text-ink-700 dark:text-slate-300">{e.text}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
          {pending > 0 && (
            <Card className="border-orange-200 bg-orange-50 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
              <p className="text-[13px] font-bold text-orange-700 dark:text-orange-300">
                {pending} approval{pending === 1 ? "" : "s"} need{pending === 1 ? "s" : ""} a human
              </p>
              <p className="text-[12.5px] text-orange-600/90 dark:text-orange-200/80">
                Nothing deploys, spends or sends without you.
              </p>
              <a href="/approvals" className="mt-2 inline-block text-[12.5px] font-bold underline">
                Open Approval Inbox →
              </a>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
