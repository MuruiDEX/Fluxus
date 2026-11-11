"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { Avatar, Badge, ProgressBar } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, PrimaryButton, PrimaryLink } from "@/components/system/ui";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgents, useLiveProjects } from "@/lib/store/live";
import { useAgents, useProjects } from "@/lib/store/app";
import { newProjectHref } from "@/lib/new-project";
import { PROJECT_STATUS_BADGE_TONE, projectStatusView } from "@/lib/project-status";
import type { Agent, Project } from "@/lib/domain/types";

function ProjectGrid({ projects, agents }: { projects: Project[]; agents: Agent[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {projects.map((p) => {
        const status = projectStatusView(p.status);
        return (
        <Link key={p.id} href={`/projects/${p.id}`}>
          <Card variant="interactive" className="p-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="text-[15px] font-bold hover:text-brand-500">{p.title}</p>
                <p className="mt-0.5 line-clamp-2 text-[12.5px] text-ink-500">{p.goal}</p>
              </div>
              <Badge tone={PROJECT_STATUS_BADGE_TONE[status.tone]} className="shrink-0">
                {status.label}
              </Badge>
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
        );
      })}
    </div>
  );
}

function DemoProjectsView() {
  const { projects } = useProjects();
  const agents = useAgents((s) => s.agents);

  return (
    <>
      <PageHeader
        title="Projects"
        sub="Every business initiative becomes a project with a team, plan and budget."
        actions={
          <PrimaryLink href="/projects/new">
            <Plus className="h-4 w-4" /> New Project
          </PrimaryLink>
        }
      />
      <ProjectGrid projects={projects} agents={agents} />
    </>
  );
}

function LiveProjectsView({ businessId }: { businessId: string }) {
  const { status, projects, error, load, reset } = useLiveProjects();
  const agents = useLiveAgents((s) => s.agents);
  const ensureAgents = useLiveAgents((s) => s.ensure);
  const resetAgents = useLiveAgents((s) => s.reset);

  useEffect(() => {
    void load(businessId);
    void ensureAgents(businessId);
    return () => {
      reset();
      resetAgents();
    };
  }, [businessId, load, ensureAgents, reset, resetAgents]);

  return (
    <>
      <PageHeader
        title="Projects"
        sub="Every business initiative becomes a project with a team, plan and budget."
        badge={<LiveBadge />}
        actions={
          <PrimaryLink href={newProjectHref()}>
            <Plus className="h-4 w-4" /> New Project
          </PrimaryLink>
        }
      />
      {status === "loading" || status === "idle" ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading projects…</Card>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load projects</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={() => void load(businessId)}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : projects.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">No projects in this business yet</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            Projects created on the server will appear here.
          </p>
        </Card>
      ) : (
        <ProjectGrid projects={projects} agents={agents} />
      )}
    </>
  );
}

export default function ProjectsPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveProjectsView businessId={currentBusinessId} />;
  }
  return <DemoProjectsView />;
}
