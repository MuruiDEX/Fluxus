"use client";

import { useState } from "react";
import { Avatar, TaskStatusBadge } from "@/components/system/badges";
import { Card, PageHeader } from "@/components/system/ui";
import { failTaskInExecution, retryTaskInExecution } from "@/lib/sim/engine";
import { useAgents, useProjects } from "@/lib/store/app";
import { cn } from "@/lib/cn";
import type { TaskStatus } from "@/lib/domain/types";

const filters: ("all" | TaskStatus)[] = ["all", "working", "waiting_dependency", "waiting_assets", "not_started", "completed"];

export default function TasksPage() {
  const { tasks, projects } = useProjects();
  const agents = useAgents((s) => s.agents);
  const [filter, setFilter] = useState<(typeof filters)[number]>("all");
  const visible = tasks.filter((t) => filter === "all" || t.status === filter);

  return (
    <>
      <PageHeader title="Tasks" sub="Parallel AI work with dependencies. Independent tasks run simultaneously." />
      <p className="-mt-2 text-[12px] text-ink-400">Demo: use Fail / Retry to rehearse failure handling — the engine, not the UI, owns recovery.</p>
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
                <span>{a?.role}</span>
                <span>·</span>
                <span className="truncate">{p?.title}</span>
                <span className="ml-auto shrink-0 font-bold tabular-nums text-ink-700 dark:text-slate-200">{t.progress}%</span>
              </div>
              {(t.status === "working" || t.status === "failed") && (
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
            </Card>
          );
        })}
      </div>
    </>
  );
}
