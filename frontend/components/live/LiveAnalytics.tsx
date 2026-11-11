"use client";

import { memo, useEffect } from "react";
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

function WorkforceSkeleton() {
  return (
    <div role="status" aria-label="Loading workforce" className="overflow-x-auto">
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
          {[0, 1, 2, 3].map((i) => (
            <tr key={i} className="border-t border-mist-100 dark:border-white/5">
              <td className="py-2 pr-2"><Skeleton className="h-3 w-24" /></td>
              <td className="py-2 pr-2"><Skeleton className="ml-auto h-3 w-10" /></td>
              <td className="py-2 pr-2"><Skeleton className="ml-auto h-3 w-10" /></td>
              <td className="py-2 pr-2"><Skeleton className="ml-auto h-3 w-10" /></td>
              <td className="py-2"><Skeleton className="ml-auto h-3 w-10" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProjectBudgetsSkeleton() {
  return (
    <div role="status" aria-label="Loading project budgets" className="flex flex-col gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i}>
          <div className="mb-1 flex items-center justify-between gap-2">
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-3 w-24 shrink-0" />
          </div>
          <Skeleton className="h-1.5 w-full rounded-full" />
        </div>
      ))}
    </div>
  );
}

const WorkforceSection = memo(function WorkforceSection({
  businessName,
}: {
  businessName?: string;
}) {
  const status = useLiveAgentsList((s) => s.status);
  const agents = useLiveAgentsList((s) => s.agents).filter((agent) => agent.hired);
  const error = useLiveAgentsList((s) => s.error);

  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[13px] font-bold">Workforce performance</h3>
        {businessName && (
          <span className="text-[11.5px] text-ink-400">{businessName}</span>
        )}
      </div>
      {(status === "idle" || status === "loading") && <WorkforceSkeleton />}
      {status === "error" && (
        <p role="alert" className="text-[13px] text-red-500">{error}</p>
      )}
      {status === "ready" && agents.length === 0 && (
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
  );
});

export function LiveAnalytics({ businessId }: { businessId: string }) {
  const business = useLiveBusiness((s) => s.business);
  const businessStatus = useLiveBusiness((s) => s.status);
  const businessError = useLiveBusiness((s) => s.error);
  const projects = useLiveProjects((s) => s.projects);
  const projectsStatus = useLiveProjects((s) => s.status);
  const projectsError = useLiveProjects((s) => s.error);
  const usageStatus = useLiveUsage((s) => s.status);
  const usageSummary = useLiveUsage((s) => s.summary);
  const usageError = useLiveUsage((s) => s.error);

  useEffect(() => {
    void useLiveBusiness.getState().load(businessId);
    void useLiveProjects.getState().load(businessId);
    void useLiveAgentsList.getState().load(businessId);
    void useLiveUsage.getState().load({ businessId });
    return () => {
      useLiveBusiness.getState().reset();
      useLiveProjects.getState().reset();
      useLiveAgentsList.getState().reset();
      useLiveUsage.getState().reset();
    };
  }, [businessId]);

  const summary = usageSummary;
  const byKind = Object.entries(summary.byCallKind).map(([kind, entry]) => ({
    name: CALL_KIND_LABELS[kind] ?? kind,
    confirmed: entry.confirmedCost,
    estimated: entry.estimatedCost,
  }));
  const totals = projectTotals(projects);
  const totalCommitted = summary.confirmedCost + summary.estimatedCost;
  // Pending shows a skeleton; a failed load shows "—". It must never fall
  // through to the empty summary as $0.00.
  const usageState = metricState(usageStatus);
  const projectsState = metricState(projectsStatus);
  const usageFailed = usageState === "unavailable";
  const projectsFailed = projectsState === "unavailable";

  return (
    <>
      <PageHeader
        title="Analytics"
        badge={<LiveBadge />}
        sub="Server-side cost, speed and completion data for this business."
      />

      {businessStatus === "error" ? (
        <Card className="p-8 text-center">
          <div role="alert">
            <p className="text-[14px] font-semibold">Couldn&apos;t load analytics</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{businessError}</p>
          </div>
          <div className="mt-3 flex justify-center">
            <PrimaryButton onClick={() => void useLiveBusiness.getState().load(businessId)}>Retry</PrimaryButton>
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
                  if (usageFailed) void useLiveUsage.getState().load({ businessId });
                  if (projectsFailed) void useLiveProjects.getState().load(businessId);
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
                {usageStatus === "loading" && (
                  <span className="text-[11.5px] text-ink-400">Loading…</span>
                )}
              </div>
              {usageStatus === "error" ? (
                <p role="alert" className="text-[13px] text-red-500">{usageError}</p>
              ) : usageStatus !== "ready" ? (
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
              {(projectsStatus === "idle" || projectsStatus === "loading") && (
                <ProjectBudgetsSkeleton />
              )}
              {projectsStatus === "error" && (
                <p role="alert" className="text-[13px] text-red-500">{projectsError}</p>
              )}
              {projectsStatus === "ready" && projects.length === 0 && (
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

          <WorkforceSection businessName={business?.name} />
        </>
      )}
    </>
  );
}
