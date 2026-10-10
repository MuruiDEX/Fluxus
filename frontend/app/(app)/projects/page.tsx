"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { Avatar, ProgressBar } from "@/components/system/badges";
import { Card, PageHeader, PrimaryButton } from "@/components/system/ui";
import { useAgents, useProjects } from "@/lib/store/app";

export default function ProjectsPage() {
  const { projects } = useProjects();
  const agents = useAgents((s) => s.agents);

  return (
    <>
      <PageHeader
        title="Projects"
        sub="Every business initiative becomes a project with a team, plan and budget."
        actions={
          <Link href="/projects/new">
            <PrimaryButton><Plus className="h-4 w-4" /> New Project</PrimaryButton>
          </Link>
        }
      />
      <div className="grid gap-3 md:grid-cols-2">
        {projects.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`}>
            <Card className="p-4 transition-transform hover:-translate-y-0.5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-[15px] font-bold hover:text-brand-500">{p.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-[12.5px] text-ink-500">{p.goal}</p>
                </div>
                <span className="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-brand-600 dark:bg-brand-500/15 dark:text-sky-300">
                  In Progress
                </span>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <div className="flex -space-x-1.5">
                  {p.teamIds.slice(0, 5).map((id) => {
                    const a = agents.find((x) => x.id === id);
                    if (!a) return null;
                    return <Avatar key={id} name={a.name} hue={a.avatarHue} size={24} />;
                  })}
                </div>
                <span className="text-[11.5px] text-ink-400">Due {p.deadline}</span>
                <span className="ml-auto text-[12px] font-bold tabular-nums">{p.progress}%</span>
              </div>
              <ProgressBar value={p.progress} className="mt-1.5" />
              <p className="mt-2 text-[11.5px] tabular-nums text-ink-400">
                Budget ${p.budget.toLocaleString("en-US")} · AI ${p.aiSpent.toFixed(1)} / ${p.aiBudget}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </>
  );
}
