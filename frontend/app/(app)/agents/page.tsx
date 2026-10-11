"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AgentStatusBadge, Avatar } from "@/components/system/badges";
import { Card, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";

import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgentsList } from "@/lib/store/live";
import { useAgents } from "@/lib/store/app";
import type { Agent } from "@/lib/domain/types";

function AgentGrid({ agents }: { agents: Agent[] }) {
  const hired = agents.filter((a) => a.hired);
  return (
    <>
      <PageHeader
        title="Agents"
        sub={`${hired.length} AI employees on your team. Each has permissions, tools and a performance record.`}
      />
      <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
        {agents.map((a) => (
          <Link key={a.id} href={`/agents/${a.id}`}>
            <Card className="flex h-full flex-col gap-2 p-4 transition-transform hover:-translate-y-0.5">
              <div className="flex items-center gap-2.5">
                <Avatar name={a.name} hue={a.avatarHue} size={38} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-bold">{a.role}</p>
                  <p className="truncate text-[12px] text-ink-400">{a.currentTask ?? "Standing by"}</p>
                </div>
                {!a.hired && (
                  <span className="rounded-full bg-mist-100 px-2 py-0.5 text-[10.5px] font-bold text-ink-500 dark:bg-white/10">MARKETPLACE</span>
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
        ))}
      </div>
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
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading agents…</Card>
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
      <AgentGrid agents={agents} />
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
