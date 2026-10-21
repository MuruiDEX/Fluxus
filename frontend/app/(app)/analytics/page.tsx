"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, PageHeader } from "@/components/system/ui";
import { LiveAnalytics } from "@/components/live/LiveAnalytics";
import { useAgents, useProjects } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";

function DemoAnalyticsPage() {
  const agents = useAgents((s) => s.agents);
  const { tasks, projects } = useProjects();
  const project = projects[0];
  const costByTask = tasks.map((t) => ({ name: t.title.split(" ")[0], cost: t.costAccrued }));

  return (
    <>
      <PageHeader title="Analytics" sub="Which AI employees actually provide value — cost, speed and completion." />
      <p className="-mt-2 text-[12px] text-ink-400">Simulated execution data from the Northstar Fitness demo — not real spend.</p>
      <div className="grid gap-3 xl:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 text-[13px] font-bold">AI cost ledger — ${project.aiSpent.toFixed(2)} of ${project.aiBudget} budget</h3>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={costByTask}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="name" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip />
                <Bar dataKey="cost" fill="#2563eb" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
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
