"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { ArrowUpRight, Bot, Sparkles } from "lucide-react";
import { AgentStatusBadge, Avatar, Badge, ProgressBar } from "@/components/system/badges";
import {
  Card,
  EmptyState,
  LiveBadge,
  PageHeader,
  PrimaryButton,
  SkeletonCard,
} from "@/components/system/ui";
import { StaggerGroup, StaggerItem } from "@/components/system/motion";
import { AgentToggleButton } from "@/components/system/AgentToggle";

import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgentsList } from "@/lib/store/live";
import { useAgents } from "@/lib/store/app";
import type { Agent } from "@/lib/domain/types";

function AgentGrid({
  agents,
  renderAction,
  live,
}: {
  agents: Agent[];
  renderAction?: (agent: Agent) => ReactNode;
  /** LIVE rows use `hired` as the active flag; DEMO rows use it for suggestions. */
  live?: boolean;
}) {
  const hired = agents.filter((a) => a.hired);
  const working = hired.filter((agent) =>
    ["working", "thinking", "reviewing"].includes(agent.status),
  ).length;

  return (
    <>
      <PageHeader
        title="Agents"
        sub={`${hired.length} AI employees on your team · ${working} active. Each has permissions, tools and a performance record.`}
        actions={
          <Link
            href="/marketplace"
            className="inline-flex items-center gap-1.5 rounded-control border border-line bg-surface px-3 py-2 text-caption font-semibold text-fg transition-colors hover:border-brand/30 hover:bg-brand-soft hover:text-brand"
          >
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            Hire from marketplace
            <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
          </Link>
        }
      />
      <StaggerGroup className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {agents.map((a) => (
          <StaggerItem key={a.id} className="flex h-full flex-col gap-2">
            <Link href={`/agents/${a.id}`} className="flex-1">
              <Card variant="interactive" className="group flex h-full flex-col p-4">
                <div className="flex items-start gap-3">
                  <span className="relative">
                    <Avatar name={a.name} hue={a.avatarHue} size={40} />
                    {a.hired && (
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface bg-success-fg"
                      />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-body-sm font-bold text-fg">{a.role}</p>
                    <p className="truncate text-caption text-fg-subtle">
                      {a.currentTask ?? "Standing by"}
                    </p>
                  </div>
                  {!a.hired && (
                    <Badge tone="neutral" size="xs">
                      {live ? "DISABLED" : "MARKETPLACE"}
                    </Badge>
                  )}
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                  <AgentStatusBadge status={a.status} live />
                  <span className="inline-flex items-center gap-1 text-micro font-semibold tabular-nums text-fg-subtle">
                    <Bot aria-hidden="true" className="h-3 w-3" />
                    {a.performance.completionRate}% done · ★ {a.performance.rating.toFixed(1)}
                  </span>
                </div>

                <div className="mt-3">
                  <div className="mb-1 flex items-center justify-between text-micro text-fg-subtle">
                    <span>Completion record</span>
                    <span className="tabular-nums">{a.performance.completionRate}%</span>
                  </div>
                  <ProgressBar value={a.performance.completionRate} className="h-1" />
                </div>
              </Card>
            </Link>
            {renderAction?.(a)}
          </StaggerItem>
        ))}
      </StaggerGroup>
    </>
  );
}

function DemoAgentsView() {
  const agents = useAgents((s) => s.agents);
  return <AgentGrid agents={agents} />;
}

function LiveAgentsView({ businessId }: { businessId: string }) {
  const { status, agents, total, error, load, reset } = useLiveAgentsList();

  useEffect(() => {
    void load(businessId);
    return () => reset();
  }, [businessId, load, reset]);

  if (status === "loading" || status === "idle") {
    return (
      <>
        <PageHeader title="Agents" sub="Loading team from server…" badge={<LiveBadge />} />
        <div
          role="status"
          aria-label="Loading agents"
          className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
        >
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </>
    );
  }

  if (status === "error") {
    return (
      <>
        <PageHeader title="Agents" sub="Server team for your business." badge={<LiveBadge />} />
        <Card className="p-8 text-center">
          <p className="text-body font-semibold text-fg">Couldn&apos;t load agents</p>
          <p className="mx-auto mt-1 max-w-md text-body-sm text-fg-muted">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={() => void load(businessId)}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      </>
    );
  }

  if (agents.length === 0) {
    return (
      <>
        <PageHeader title="Agents" sub="Server team for your business." badge={<LiveBadge />} />
        <EmptyState
          title="No agents in this business yet"
          body="Hire a specialist from the marketplace to start building the team."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Agents"
        sub={`${total} AI employees on your team. Each has permissions, tools and a performance record.`}
        badge={<LiveBadge />}
      />
      <AgentGrid
        agents={agents}
        live
        renderAction={(a) => (
          <AgentToggleButton businessId={businessId} agentId={a.id} hired={a.hired} />
        )}
      />
    </>
  );
}

export default function AgentsPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveAgentsView businessId={currentBusinessId} />;
  }
  return <DemoAgentsView />;
}
