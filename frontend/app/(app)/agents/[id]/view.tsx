"use client";

import { useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import { AgentStatusBadge, Avatar, ProgressBar } from "@/components/system/badges";
import { Card, GhostButton, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { AgentToggleButton } from "@/components/system/AgentToggle";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgent, useLiveProjects, useLiveTasks } from "@/lib/store/live";
import { useAgents, useProjects } from "@/lib/store/app";
import type { Agent, Task } from "@/lib/domain/types";

function AgentProfile({ agent, mine }: { agent: Agent; mine: Task[] }) {
  return (
    <>
      <p className="text-[12px] text-ink-400">Agents › {agent.role}</p>
      <PageHeader
        title={agent.role}
        sub={agent.responsibilities.join(" · ")}
        actions={<AgentStatusBadge status={agent.status} live />}
      />
      <div className="grid gap-3 xl:grid-cols-[300px_1fr]">
        <Card className="h-fit p-4">
          <div className="flex items-center gap-3">
            <Avatar name={agent.name} hue={agent.avatarHue} size={52} />
            <div>
              <p className="text-[16px] font-bold">{agent.name}</p>
              <p className="text-[12.5px] text-ink-500">${agent.costPerMonth}/mo · ★{agent.performance.rating.toFixed(1)}</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center">
            {[
              { v: `${agent.performance.completionRate}%`, l: "Completion" },
              { v: `~${agent.performance.avgMinutes}m`, l: "Avg time" },
              { v: `${agent.performance.approvalRate}%`, l: "Approval" },
            ].map((s) => (
              <div key={s.l} className="rounded-xl bg-mist-50 p-2 dark:bg-white/5">
                <p className="text-[14px] font-bold tabular-nums">{s.v}</p>
                <p className="text-[11px] text-ink-400">{s.l}</p>
              </div>
            ))}
          </div>
          <h3 className="mb-1.5 mt-4 text-[13px] font-bold">Permissions</h3>
          <div className="flex flex-col gap-1.5 text-[12px]">
            <p className="rounded-lg bg-emerald-50 p-2 dark:bg-emerald-500/10"><span className="font-bold">READ:</span> {agent.permissions.read.join(", ")}</p>
            <p className="rounded-lg bg-blue-50 p-2 dark:bg-blue-500/10"><span className="font-bold">WRITE:</span> {agent.permissions.write.join(", ") || "—"}</p>
            <p className="rounded-lg bg-red-50 p-2 dark:bg-red-500/10"><span className="font-bold">NO:</span> {agent.permissions.denied.join(", ")}</p>
          </div>
          <h3 className="mb-1.5 mt-4 text-[13px] font-bold">Tools</h3>
          <div className="flex flex-wrap gap-1.5">
            {agent.tools.map((t) => (
              <span key={t} className="rounded-full border border-mist-200 px-2 py-0.5 text-[12px] dark:border-white/10">{t}</span>
            ))}
          </div>
        </Card>
        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <h3 className="mb-2 text-[13px] font-bold">Current & completed work</h3>
            {mine.length === 0 && <p className="text-[13px] text-ink-400">No tasks assigned yet.</p>}
            {mine.map((t) => (
              <div key={t.id} className="mb-2 rounded-xl border border-mist-200 p-3 last:mb-0 dark:border-white/10">
                <div className="flex items-center justify-between text-[13px]">
                  <span className="font-semibold">{t.title}</span>
                  <span className="tabular-nums text-ink-500">{t.progress}%</span>
                </div>
                <ProgressBar value={t.progress} className="mt-1.5" />
              </div>
            ))}
          </Card>
        </div>
      </div>
    </>
  );
}

function DemoAgentDetail({ id }: { id: string }) {
  const agents = useAgents((s) => s.agents);
  const { tasks } = useProjects();
  const agent = useMemo(() => agents.find((a) => a.id === id) ?? agents[0], [agents, id]);
  const mine = useMemo(() => tasks.filter((t) => t.ownerAgentId === agent.id), [tasks, agent.id]);

  return (
    <>
      <AgentProfile agent={agent} mine={mine} />
      <Card className="mt-3 flex items-center gap-3 p-4">
        <div className="flex-1">
          <p className="text-[13px] font-bold">Operational note</p>
          <p className="text-[12.5px] text-ink-500">Used competitor pricing data and current product margins to calculate the recommendation.</p>
        </div>
        <GhostButton>Take over</GhostButton>
      </Card>
    </>
  );
}

function LiveAgentDetail({ businessId, agentId }: { businessId: string; agentId: string }) {
  const { status, agent, notFound, error, load, reset } = useLiveAgent();
  const projectsSlice = useLiveProjects();
  const tasksSlice = useLiveTasks();

  useEffect(() => {
    void load(businessId, agentId);
    void (async () => {
      await projectsSlice.load(businessId);
      const ids = useLiveProjects.getState().projects.map((p) => p.id);
      await tasksSlice.loadAll(businessId, ids);
    })();
    return () => {
      reset();
      projectsSlice.reset();
      tasksSlice.reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, agentId]);

  if (status === "loading" || status === "idle") {
    return (
      <>
        <PageHeader title="Agent" sub="Loading from server…" badge={<LiveBadge />} />
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading agent…</Card>
      </>
    );
  }

  if (status === "error" || !agent) {
    return (
      <>
        <PageHeader title="Agent" sub="Server data for your business." badge={<LiveBadge />} />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">
            {notFound ? "Agent not found" : "Couldn't load agent"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            {notFound
              ? "This agent doesn't exist in your business or isn't available to you."
              : error}
          </p>
          {!notFound && (
            <div className="mt-3 flex justify-center">
              <PrimaryButton
                className="justify-center"
                onClick={() => {
                  void load(businessId, agentId);
                }}
              >
                Retry
              </PrimaryButton>
            </div>
          )}
        </Card>
      </>
    );
  }

  const mine = tasksSlice.tasks.filter((t) => t.ownerAgentId === agent.id);
  return (
    <>
      <AgentProfile agent={agent} mine={mine} />
      <Card className="mt-3 p-4">
        <p className="text-[13px] font-bold">Team status</p>
        <p className="mt-0.5 text-[12px] text-ink-500">
          {agent.hired
            ? "Active — can take new work."
            : "Disabled — keeps history, takes no new work."}
        </p>
        <div className="mt-2">
          <AgentToggleButton businessId={businessId} agentId={agent.id} hired={agent.hired} />
        </div>
      </Card>
    </>
  );
}

export function AgentDetail() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveAgentDetail businessId={currentBusinessId} agentId={id} />;
  }
  return <DemoAgentDetail id={id} />;
}
