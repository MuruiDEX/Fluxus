"use client";

import { useEffect } from "react";
import dynamic from "next/dynamic";
import { Card, LiveBadge, PageHeader, PrimaryButton, Skeleton } from "@/components/system/ui";
import { ProgressBar } from "@/components/system/badges";
import {
  useLiveAgentsList,
  useLiveBusiness,
  useLiveProjects,
  useLiveUsage,
} from "@/lib/store/live";
import { metricState, projectTotals, type MetricState } from "@/lib/live/analytics-metrics";

const CostBarChart = dynamic(
  () => import("@/components/charts/cost-bar-chart").then((mod) => mod.CostBarChart),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> },
);

const CALL_KIND_LABELS: Record<string, string> = {
  task_execution: "Task execution",
  brief_questions: "Brief questions",
  brief_summary: "Brief summary",
  plan_generation: "Plan generation",
  brief_finalize: "Brief finalize",
  "": "Unclassified",
};

function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

/**
 * Renders a server figure, a loading skeleton, or an explicit "—". A failed
 * request is never rendered as $0.00 — that is a balance the server did not
 * report.
 */
function MoneyValue({ state, value }: { state: MetricState; value: number }) {
  if (state === "pending") return <Skeleton className="h-7 w-24" />;
  if (state === "unavailable") {
    return (
      <span
        className="text-ink-400"
        title="This request failed — the server reported no value, so none is shown."
      >
        —
      </span>
    );
  }
  return <>{money(value)}</>;
}

export function LiveAnalytics({ businessId }: { businessId: string }) {
  const businessSlice = useLiveBusiness();
  const projectsSlice = useLiveProjects();
  const agentsSlice = useLiveAgentsList();
  const usageSlice = useLiveUsage();

  useEffect(() => {
    void businessSlice.load(businessId);
    void projectsSlice.load(businessId);
    void agentsSlice.load(businessId);
    void usageSlice.load({ businessId });
    return () => {
      businessSlice.reset();
      projectsSlice.reset();
      agentsSlice.reset();
      usageSlice.reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId]);

  const business = businessSlice.business;
  const projects = projectsSlice.projects;
  const agents = agentsSlice.agents.filter((agent) => agent.hired);
  const summary = usageSlice.summary;
  const byKind = Object.entries(summary.byCallKind).map(([kind, entry]) => ({
    name: CALL_KIND_LABELS[kind] ?? kind,
    confirmed: entry.confirmedCost,
    estimated: entry.estimatedCost,
  }));
  const totals = projectTotals(projects);
  const totalCommitted = summary.confirmedCost + summary.estimatedCost;
  // Pending shows a skeleton; a failed load shows "—". It must never fall
  // through to the empty summary as $0.00.
  const usageState = metricState(usageSlice.status);
  const projectsState = metricState(projectsSlice.status);
  const usageFailed = usageState === "unavailable";
  const projectsFailed = projectsState === "unavailable";

  return (
    <>
      <PageHeader
        title="Analytics"
        badge={<LiveBadge />}
        sub="Server-side cost, speed and completion data for this business."
      />

      {businessSlice.status === "error" ? (
        <Card className="p-8 text-center">
          <div role="alert">
            <p className="text-[14px] font-semibold">Couldn&apos;t load analytics</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{businessSlice.error}</p>
          </div>
          <div className="mt-3 flex justify-center">
            <PrimaryButton onClick={() => void businessSlice.load(businessId)}>Retry</PrimaryButton>
          </div>
        </Card>
      ) : (
        <>
          {(usageFailed || projectsFailed) && (
            <Card className="flex flex-wrap items-center gap-3 border-amber-200 bg-amber-50 p-3 text-[12.5px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              <span role="alert" className="min-w-0 flex-1 leading-5">
                Some analytics could not be loaded. Dependent figures show “—” rather than a
                balance the server never reported.
              </span>
              <PrimaryButton
                onClick={() => {
                  if (usageFailed) void usageSlice.load({ businessId });
                  if (projectsFailed) void projectsSlice.load(businessId);
                }}
              >
                Retry
              </PrimaryButton>
            </Card>
          )}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Card className="p-4">
              <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">
                Confirmed AI spend
              </p>
              <p className="mt-1 text-[22px] font-bold tabular-nums">
                <MoneyValue state={usageState} value={summary.confirmedCost} />
              </p>
              <p className="mt-1 text-[11.5px] text-ink-400">Provider-reported or tariff-priced</p>
            </Card>
            <Card className="p-4">
              <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">
                Estimated + confirmed
              </p>
              <p className="mt-1 text-[22px] font-bold tabular-nums">
                <MoneyValue state={usageState} value={totalCommitted} />
              </p>
              <p className="mt-1 text-[11.5px] text-ink-400">
                {usageState === "pending"
                  ? "Loading unpriced calls…"
                  : usageState === "unavailable"
                    ? "Unpriced data unavailable"
                    : `${summary.unknownCount} unpriced calls`}
              </p>
            </Card>
            <Card className="p-4">
              <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">
                Project AI budgets
              </p>
              <p className="mt-1 text-[22px] font-bold tabular-nums">
                <MoneyValue state={projectsState} value={totals.spent} />
              </p>
              <p className="mt-1 text-[11.5px] text-ink-400">
                {projectsState === "pending"
                  ? "Loading budgets…"
                  : projectsState === "unavailable"
                    ? "Budget data unavailable"
                    : `of ${money(totals.budget)} allocated`}
              </p>
            </Card>
            <Card className="p-4">
              <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">
                Active reservations
              </p>
              <p className="mt-1 text-[22px] font-bold tabular-nums">
                <MoneyValue state={usageState} value={summary.reservations.active} />
              </p>
              <p className="mt-1 text-[11.5px] text-ink-400">
                {usageState === "pending"
                  ? "Loading reservations…"
                  : usageState === "unavailable"
                    ? "Reservation data unavailable"
                    : summary.reservations.ambiguous > 0
                      ? `${money(summary.reservations.ambiguous)} ambiguous`
                      : "No ambiguous reservations"}
              </p>
            </Card>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <Card className="p-4">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-[13px] font-bold">AI cost by call kind</h3>
                {usageSlice.status === "loading" && (
                  <span className="text-[11.5px] text-ink-400">Loading…</span>
                )}
              </div>
              {usageSlice.status === "error" ? (
                <p role="alert" className="text-[13px] text-red-500">{usageSlice.error}</p>
              ) : usageSlice.status !== "ready" ? (
                <p role="status" className="text-[13px] text-ink-400">Loading cost data…</p>
              ) : byKind.length === 0 ? (
                <p className="rounded-xl bg-mist-50 p-3 text-[12.5px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
                  No billable AI calls recorded yet.
                </p>
              ) : (
                <div className="h-56">
                  <CostBarChart
                    data={byKind}
                    angled
                    showLegend
                    formatValue={money}
                    bars={[
                      { key: "confirmed", name: "Confirmed", color: "#2563eb" },
                      { key: "estimated", name: "Estimated", color: "#93c5fd" },
                    ]}
                  />
                </div>
              )}
            </Card>

            <Card className="p-4">
              <h3 className="mb-2 text-[13px] font-bold">Project AI budgets</h3>
              {(projectsSlice.status === "idle" || projectsSlice.status === "loading") && (
                <p role="status" className="text-[13px] text-ink-400">Loading projects…</p>
              )}
              {projectsSlice.status === "error" && (
                <p role="alert" className="text-[13px] text-red-500">{projectsSlice.error}</p>
              )}
              {projectsSlice.status === "ready" && projects.length === 0 && (
                <p className="rounded-xl bg-mist-50 p-3 text-[12.5px] text-ink-500 dark:bg-white/5 dark:text-slate-300">
                  No projects yet.
                </p>
              )}
              <div className="flex max-h-56 flex-col gap-3 overflow-y-auto">
                {projects.map((project) => {
                  const used = project.aiBudget > 0 ? (project.aiSpent / project.aiBudget) * 100 : 0;
                  return (
                    <div key={project.id}>
                      <div className="mb-1 flex items-center justify-between gap-2 text-[12.5px]">
                        <span className="truncate font-medium">{project.title}</span>
                        <span className="shrink-0 tabular-nums text-ink-500">
                          {money(project.aiSpent)} / {money(project.aiBudget)}
                        </span>
                      </div>
                      <ProgressBar value={used} />
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>

          <Card className="p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-[13px] font-bold">Workforce performance</h3>
              {business && (
                <span className="text-[11.5px] text-ink-400">{business.name}</span>
              )}
            </div>
            {(agentsSlice.status === "idle" || agentsSlice.status === "loading") && (
              <p role="status" className="text-[13px] text-ink-400">Loading workforce…</p>
            )}
            {agentsSlice.status === "error" && (
              <p role="alert" className="text-[13px] text-red-500">{agentsSlice.error}</p>
            )}
            {agentsSlice.status === "ready" && agents.length === 0 && (
              <p className="text-[13px] text-ink-400">No hired AI employees yet.</p>
            )}
            {agents.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wide text-ink-400">
                      <th className="py-1.5 pr-2">Employee</th>
                      <th className="py-1.5 pr-2 text-right">Done</th>
                      <th className="py-1.5 pr-2 text-right">Avg</th>
                      <th className="py-1.5 pr-2 text-right">Approval</th>
                      <th className="py-1.5 text-right">Rating</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agents.map((agent) => (
                      <tr key={agent.id} className="border-t border-mist-100 dark:border-white/5">
                        <td className="py-2 pr-2 font-medium">{agent.role}</td>
                        <td className="py-2 pr-2 text-right tabular-nums">
                          {agent.performance.completionRate}%
                        </td>
                        <td className="py-2 pr-2 text-right tabular-nums">
                          ~{agent.performance.avgMinutes}m
                        </td>
                        <td className="py-2 pr-2 text-right tabular-nums">
                          {agent.performance.approvalRate}%
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          ★{agent.performance.rating.toFixed(1)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}
