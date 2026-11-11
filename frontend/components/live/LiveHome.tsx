"use client";

import { memo, useEffect } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { AgentStatusBadge, Avatar, ProgressBar } from "@/components/system/badges";
import {
  Card,
  LiveBadge,
  MetricCard,
  PageHeader,
  PrimaryButton,
  PrimaryLink,
  Skeleton,
  SkeletonCard,
} from "@/components/system/ui";
import { formatWhen } from "@/lib/live/datetime";
import { StaggerGroup, StaggerItem } from "@/components/system/motion";
import {
  useLiveActivity,
  useLiveAgentsList,
  useLiveApprovals,
  useLiveBusiness,
  useLiveProjects,
} from "@/lib/store/live";

const LiveHomeActivityCard = memo(function LiveHomeActivityCard() {
  const status = useLiveActivity((s) => s.status);
  const events = useLiveActivity((s) => s.events);
  const error = useLiveActivity((s) => s.error);
  const pendingCount = useLiveApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );

  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[15px] font-bold">AI Activity</h2>
        <Link
          href="/activity"
          className="flex items-center gap-1 text-[12px] font-semibold text-brand-500 hover:underline"
        >
          View all <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      {(status === "idle" || status === "loading") && (
        <p role="status" className="text-[13px] text-ink-400">Loading activity…</p>
      )}
      {status === "error" && (
        <p role="alert" className="text-[13px] text-red-500">{error}</p>
      )}
      {status === "ready" && events.length === 0 && (
        <p className="rounded-xl bg-mist-50 p-3 text-[12.5px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
          No activity recorded yet.
        </p>
      )}
      <div className="flex flex-col">
        {events.slice(0, 5).map((event) => (
          <div
            key={event.id}
            className="flex items-start gap-2 border-b border-mist-100 py-2 text-[12.5px] last:border-0 dark:border-white/5"
          >
            <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-400" />
            <p className="flex-1 leading-5">
              <span className="font-semibold">{event.agentName}</span> — {event.text}
            </p>
            <span className="shrink-0 text-[11px] text-ink-400">{formatWhen(event.at)}</span>
          </div>
        ))}
      </div>
      {pendingCount > 0 && (
        <Link
          href="/approvals"
          className="mt-3 flex items-center justify-between rounded-xl bg-orange-50 px-3 py-2.5 text-[13px] font-medium text-orange-700 dark:bg-orange-500/10 dark:text-orange-300"
        >
          {pendingCount} approval{pendingCount === 1 ? "" : "s"} waiting for you
          <ArrowRight className="h-4 w-4" />
        </Link>
      )}
    </Card>
  );
});

export function LiveHome({ businessId }: { businessId: string }) {
  const business = useLiveBusiness((s) => s.business);
  const businessStatus = useLiveBusiness((s) => s.status);
  const businessError = useLiveBusiness((s) => s.error);
  const projects = useLiveProjects((s) => s.projects);
  const projectsStatus = useLiveProjects((s) => s.status);
  const projectsError = useLiveProjects((s) => s.error);
  const agents = useLiveAgentsList((s) => s.agents);
  const agentsStatus = useLiveAgentsList((s) => s.status);
  const agentsError = useLiveAgentsList((s) => s.error);

  useEffect(() => {
    void useLiveBusiness.getState().load(businessId);
    void useLiveProjects.getState().load(businessId);
    void useLiveAgentsList.getState().load(businessId);
    void useLiveApprovals.getState().load(businessId);
    void useLiveActivity.getState().load({ businessId });
    return () => {
      useLiveBusiness.getState().reset();
      useLiveProjects.getState().reset();
      useLiveAgentsList.getState().reset();
      useLiveApprovals.getState().reset();
      useLiveActivity.getState().reset();
    };
  }, [businessId]);

  const activeProjects = projects.filter((project) => project.status !== "completed");
  const workingAgents = agents.filter(
    (agent) => agent.hired && (agent.status === "working" || agent.status === "thinking"),
  );
  const projectsReady = projectsStatus === "ready";
  const agentsReady = agentsStatus === "ready";
  const projectsSummary = projectsReady
    ? `${activeProjects.length} active project${activeProjects.length === 1 ? "" : "s"}`
    : projectsStatus === "error"
      ? "Projects unavailable"
      : "Loading projects…";
  const teamSummary = agentsReady
    ? `${agents.length} AI employee${agents.length === 1 ? "" : "s"} on the team.`
    : agentsStatus === "error"
      ? "AI team unavailable."
      : "Loading AI team…";

  if (businessStatus === "error") {
    return (
      <>
        <PageHeader title="Workspace" badge={<LiveBadge />} sub="Server data for this business." />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load this business</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{businessError}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton onClick={() => void useLiveBusiness.getState().load(businessId)}>Retry</PrimaryButton>
          </div>
        </Card>
      </>
    );
  }

  if (!business) {
    return (
      <>
        <PageHeader title="Workspace" badge={<LiveBadge />} sub="Loading server data…" />
        {/* Shape-matched placeholders: the page does not jump when data lands. */}
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4" role="status" aria-label="Loading workspace">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i} className="flex flex-col gap-2 p-4">
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-3 w-2/3" />
            </Card>
          ))}
        </div>
        <div className="grid gap-3 xl:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </>
    );
  }

  const metrics = business.metrics;
  const revenueSpark =
    business.revenueSeries.length > 1 ? business.revenueSeries.map((point) => point.value) : undefined;

  return (
    <>
      <PageHeader
        title={business.name}
        badge={<LiveBadge />}
        sub={`${projectsSummary} · ${teamSummary}`}
        actions={
          <PrimaryLink href="/projects/new">+ New Project</PrimaryLink>
        }
      />

      <StaggerGroup className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StaggerItem>
        <MetricCard
          label="Revenue"
          value={`$${metrics.revenue.toLocaleString("en-US")}`}
          delta={metrics.revenueDelta}
          spark={revenueSpark}
        />
        </StaggerItem>
        <StaggerItem>
        <MetricCard
          label="Customers"
          value={metrics.customers.toLocaleString("en-US")}
          delta={metrics.customersDelta}
        />
        </StaggerItem>
        <StaggerItem>
        <MetricCard
          label="Conversion"
          value={`${metrics.conversion}%`}
          delta={metrics.conversionDelta}
          invert
        />
        </StaggerItem>
        <StaggerItem>
        <Card className="flex h-full items-center gap-3 p-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-4 border-brand-100 text-[14px] font-bold tabular-nums dark:border-brand-500/20">
            {business.healthScore}
          </div>
          <div>
            <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">
              Business Health
            </p>
            <p className="text-[15px] font-bold">{business.healthScore}/100</p>
            <p className="text-[11px] text-ink-400">
              {business.industry || "No industry set"}
              {business.location ? ` · ${business.location}` : ""}
            </p>
          </div>
        </Card>
        </StaggerItem>
      </StaggerGroup>

      <div className="grid gap-3 xl:grid-cols-3">
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-bold">Active Projects</h2>
            <Link
              href="/projects"
              className="flex items-center gap-1 text-[12px] font-semibold text-brand-500 hover:underline"
            >
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="flex flex-col gap-3">
            {(projectsStatus === "idle" || projectsStatus === "loading") && (
              <p role="status" className="text-[13px] text-ink-400">Loading projects…</p>
            )}
            {projectsStatus === "error" && (
              <p role="alert" className="text-[13px] text-red-500">{projectsError}</p>
            )}
            {projectsStatus === "ready" && activeProjects.length === 0 && (
              <p className="rounded-xl bg-mist-50 p-3 text-[12.5px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
                No active projects. Start one to give the team work.
              </p>
            )}
            {activeProjects.slice(0, 5).map((project) => (
              <Link key={project.id} href={`/projects/${project.id}`} className="group">
                <div className="mb-1 flex items-center justify-between text-[13px]">
                  <span className="font-medium group-hover:text-brand-500">{project.title}</span>
                  <span className="tabular-nums text-ink-500">{project.progress}%</span>
                </div>
                <ProgressBar value={project.progress} />
              </Link>
            ))}
          </div>
        </Card>

        <LiveHomeActivityCard />

        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-bold">AI Team</h2>
            <Link
              href="/office"
              className="flex items-center gap-1 text-[12px] font-semibold text-brand-500 hover:underline"
            >
              Open office <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          {(agentsStatus === "idle" || agentsStatus === "loading") && (
            <p role="status" className="text-[13px] text-ink-400">Loading team…</p>
          )}
          {agentsStatus === "error" && (
            <p role="alert" className="text-[13px] text-red-500">{agentsError}</p>
          )}
          {agentsStatus === "ready" && agents.length === 0 && (
            <p className="rounded-xl bg-mist-50 p-3 text-[12.5px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
              No AI employees yet. Hire your first specialist.
            </p>
          )}
          <div className="flex flex-col gap-2">
            {agents.slice(0, 6).map((agent) => (
              <div
                key={agent.id}
                className="flex items-center gap-2.5 rounded-xl border border-mist-200 p-2.5 dark:border-white/10"
              >
                <Avatar name={agent.name} hue={agent.avatarHue} size={30} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[12.5px] font-semibold">{agent.role}</p>
                  <p className="truncate text-[11.5px] text-ink-500">
                    {agent.currentTask ?? "Standing by"}
                  </p>
                </div>
                <AgentStatusBadge status={agent.status} live />
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-[15px] font-bold">Working now</h2>
          <span className="text-[11.5px] text-ink-400">
            {agentsReady
              ? `${workingAgents.length} active`
              : agentsStatus === "error"
                ? "Unavailable"
                : "Loading…"}
          </span>
        </div>
        {agentsStatus === "error" ? (
          <p role="alert" className="text-[13px] text-red-500">{agentsError}</p>
        ) : !agentsReady ? (
          <p role="status" className="text-[13px] text-ink-400">Loading team…</p>
        ) : workingAgents.length === 0 ? (
          <p className="text-[13px] text-ink-400">No AI employee is working right now.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {workingAgents.slice(0, 4).map((agent) => (
              <div
                key={agent.id}
                className="flex items-center gap-2.5 rounded-xl border border-mist-200 p-2.5 dark:border-white/10"
              >
                <Avatar name={agent.name} hue={agent.avatarHue} size={30} />
                <div className="min-w-0">
                  <p className="truncate text-[12.5px] font-semibold">{agent.role}</p>
                  <p className="truncate text-[11.5px] text-ink-500">{agent.currentTask}</p>
                </div>
                <div className="ml-auto">
                  <AgentStatusBadge status={agent.status} live />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
