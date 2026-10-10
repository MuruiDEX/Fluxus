"use client";

import Link from "next/link";
import { AgentStatusBadge, Avatar } from "@/components/system/badges";
import { Card, PageHeader } from "@/components/system/ui";

import { useAgents } from "@/lib/store/app";

export default function AgentsPage() {
  const agents = useAgents((s) => s.agents);
  const hired = agents.filter((a) => a.hired);

  return (
    <>
      <PageHeader title="Agents" sub={`${hired.length} AI employees on your team. Each has permissions, tools and a performance record.`} />
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
