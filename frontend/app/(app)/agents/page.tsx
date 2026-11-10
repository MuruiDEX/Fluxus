"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { AgentStatusBadge, Avatar, Badge } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, PrimaryButton, SkeletonCard } from "@/components/system/ui";
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
  return (
    <>
      <PageHeader
        title="Agents"
        sub={`${hired.length} AI employees on your team. Each has permissions, tools and a performance record.`}
      />
      <StaggerGroup className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
        {agents.map((a) => (
          <StaggerItem key={a.id} className="flex h-full flex-col gap-2">
            <Link href={`/agents/${a.id}`} className="flex-1">
              <Card variant="interactive" className="flex h-full flex-col gap-2 p-4">
                <div className="flex items-center gap-2.5">
                  <Avatar name={a.name} hue={a.avatarHue} size={38} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] font-bold">{a.role}</p>
                    <p className="truncate text-[12px] text-ink-400">{a.currentTask ?? "Standing by"}</p>
                  </div>
                {!a.hired && (
                  <Badge tone="neutral" size="xs">
                    {live ? "DISABLED" : "MARKETPLACE"}
                  </Badge>
                )}
                </div>
                <div className="flex items-center justify-between">
                  <AgentStatusBadge status={a.status} live />
                  <span className="text-[11.5px] tabular-nums text-ink-400">
                    {a.performance.completionRate}% · ★{a.performance.rating.toFixed(1)}
                  </span>
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
          className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3"
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
          <p className="text-[14px] font-semibold">Couldn&apos;t load agents</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
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
        <Card className="p-8 text-center text-[13px] text-ink-400">
          No agents in this business yet.
        </Card>
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
