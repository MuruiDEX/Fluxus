"use client";

import dynamic from "next/dynamic";
import { Card, DemoBadge, PageHeader, Skeleton } from "@/components/system/ui";
import { LiveAnalytics } from "@/components/live/LiveAnalytics";
import { useAgents, useProjects } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";

/** Recharts is ~380 KB of the client bundle — load it only when a chart mounts. */
const CostBarChart = dynamic(
  () => import("@/components/charts/cost-bar-chart").then((mod) => mod.CostBarChart),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> },
);

function DemoAnalyticsPage() {
  const agents = useAgents((s) => s.agents);
  const { tasks, projects } = useProjects();
  const project = projects[0];
  // Chart this project's tasks only: mixing every project's cost under one
  // project's budget header claimed a total that did not belong to it.
  const projectTasks = project ? tasks.filter((t) => t.projectId === project.id) : [];
  const costByTask = projectTasks.map((t) => ({ name: t.title.split(" ")[0], cost: t.costAccrued }));

  return (
    <>
      <PageHeader
        title="Analytics"
        sub="Which AI employees actually provide value — cost, speed and completion."
        badge={<DemoBadge />}
      />
      <p className="-mt-2 text-[12px] text-ink-400">Simulated execution data from the Northstar Fitness demo — not real spend.</p>
      <div className="grid gap-3 xl:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 text-[13px] font-bold">AI cost ledger — ${project.aiSpent.toFixed(2)} of ${project.aiBudget} budget</h3>
          <div className="h-56">
            <CostBarChart
              data={costByTask}
              bars={[{ key: "cost", name: "Cost", color: "#2563eb" }]}
            />
          </div>
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 text-[13px] font-bold">Workforce performance</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-ink-400">
                  <th className="py-1.5 pr-2">Employee</th>
                  <th className="py-1.5 pr-2 text-right">Done</th>
                  <th className="py-1.5 pr-2 text-right">Avg</th>
                  <th className="py-1.5 text-right">Rating</th>
                </tr>
              </thead>
              <tbody>
                {agents.filter((a) => a.hired).map((a) => (
                  <tr key={a.id} className="border-t border-mist-100 dark:border-white/5">
                    <td className="py-2 pr-2 font-medium">{a.role}</td>
                    <td className="py-2 pr-2 text-right tabular-nums">{a.performance.completionRate}%</td>
                    <td className="py-2 pr-2 text-right tabular-nums">~{a.performance.avgMinutes}m</td>
                    <td className="py-2 text-right tabular-nums">★{a.performance.rating.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}

export default function AnalyticsPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveAnalytics businessId={currentBusinessId} />;
  }
  return <DemoAnalyticsPage />;
}
