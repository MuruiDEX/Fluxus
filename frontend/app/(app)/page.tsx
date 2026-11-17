"use client";

import { useEffect } from "react";
import { useUI } from "@/lib/store/app";
import {
  useActivity,
  useAgents,
  useApprovals,
  useBusiness,
  useProjects,
} from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";
import {
  useLiveActivity,
  useLiveAgentsList,
  useLiveApprovals,
  useLiveBusiness,
  useLiveProjects,
  useLiveTasks,
  useLiveUsage,
} from "@/lib/store/live";
import { AgentWorkflow } from "@/components/dashboard/AgentWorkflow";
import { DashboardHero } from "@/components/dashboard/DashboardHero";
import {
  CommandPanel,
  DepartmentsPanel,
  HealthPanel,
  OperationsPanel,
  QuickActions,
  RecentActivity,
  SystemOverview,
} from "@/components/dashboard/panels";
import { Card, LiveBadge, PageHeader, PrimaryButton, Skeleton } from "@/components/system/ui";

function DashboardSkeleton() {
  return (
    <div className="flex flex-col gap-4" role="status" aria-label="Loading dashboard">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card className="relative overflow-hidden p-6">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-6 h-12 w-2/3" />
          <Skeleton className="mt-3 h-5 w-1/2" />
          <Skeleton className="mt-3 h-3 w-3/4" />
          <div className="mt-6 flex gap-2">
            <Skeleton className="h-11 w-36 rounded-control" />
            <Skeleton className="h-11 w-32 rounded-control" />
          </div>
        </Card>
        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <Skeleton className="h-8 w-44" />
            <Skeleton className="mt-4 h-4 w-40" />
            <Skeleton className="mt-3 h-10 w-full rounded-control" />
            <Skeleton className="mt-2 h-9 w-full rounded-control" />
          </Card>
          <Card className="p-4">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="mt-3 h-3 w-full" />
            <Skeleton className="mt-2 h-3 w-full" />
          </Card>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} className="p-4">
            <Skeleton className="h-8 w-8 rounded-control" />
            <Skeleton className="mt-3 h-7 w-1/3" />
            <Skeleton className="mt-2 h-3 w-2/3" />
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <Skeleton className="h-5 w-40" />
        <div className="mt-4 flex gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-40 flex-1 rounded-card" />
          ))}
        </div>
      </Card>
    </div>
  );
}

function DemoHomePage() {
  const business = useBusiness((s) => s.business);
  const agents = useAgents((s) => s.agents);
  const projects = useProjects((s) => s.projects);
  const tasks = useProjects((s) => s.tasks);
  const activeProjectId = useProjects((s) => s.activeProjectId);
  const approvals = useApprovals((s) => s.approvals);
  const events = useActivity((s) => s.events);
  const setCommandOpen = useUI((s) => s.setCommandOpen);

  const activeProject = projects.find((project) => project.id === activeProjectId) ?? projects[0] ?? null;
  const hired = agents.filter((agent) => agent.hired);
  const activeAgents = hired.filter((agent) =>
    ["working", "thinking", "reviewing"].includes(agent.status),
  );
  const pending = approvals.filter((approval) => approval.status === "pending");
  const openTasks = tasks.filter((task) =>
    ["working", "waiting_dependency", "waiting_assets", "waiting_approval"].includes(task.status),
  );

  const spend = activeProject
    ? {
        amount: activeProject.aiSpent,
        budget: activeProject.aiBudget,
        note: `of $${activeProject.aiBudget.toFixed(2)} project budget`,
      }
    : null;

  const systemLabel =
    pending.length > 0
      ? `${pending.length} approval${pending.length === 1 ? "" : "s"} waiting`
      : "Demo · systems normal";
  const systemTone = pending.length > 0 ? "warning" : "brand";

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <DashboardHero
          mode="demo"
          greetingName={business.ownerName}
          businessName={business.name}
          systemLabel={systemLabel}
          systemTone={systemTone}
          activeAgents={activeAgents.length}
          pendingApprovals={pending.length}
          onOpenCommand={() => setCommandOpen(true)}
        />
        <div className="flex flex-col gap-3">
          <CommandPanel mode="demo" onOpen={() => setCommandOpen(true)} />
          <HealthPanel
            mode="demo"
            pendingApprovals={pending.length}
            activeTasks={openTasks.length}
            totalTasks={tasks.length}
            healthScore={business.healthScore}
            lastActivity={events[0]?.at}
          />
        </div>
      </div>

      <SystemOverview
        mode="demo"
        agents={agents}
        tasks={tasks}
        approvals={approvals}
        spend={spend}
      />

      <div id="agent-workflow" className="scroll-mt-4">
        <AgentWorkflow
          mode="demo"
          project={activeProject}
          tasks={tasks}
          agents={agents}
          approvals={approvals}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
        <OperationsPanel
          mode="demo"
          tasks={tasks}
          agents={agents}
          approvals={approvals}
          projects={projects}
        />
        <DepartmentsPanel agents={agents} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QuickActions />
        <RecentActivity events={events} />
      </div>
    </>
  );
}

function LiveDashboard({ businessId }: { businessId: string }) {
  const business = useLiveBusiness((s) => s.business);
  const businessStatus = useLiveBusiness((s) => s.status);
  const businessError = useLiveBusiness((s) => s.error);
  const projects = useLiveProjects((s) => s.projects);
  const projectsStatus = useLiveProjects((s) => s.status);
  const tasks = useLiveTasks((s) => s.tasks);
  const tasksStatus = useLiveTasks((s) => s.status);
  const tasksError = useLiveTasks((s) => s.error);
  const agents = useLiveAgentsList((s) => s.agents);
  const agentsStatus = useLiveAgentsList((s) => s.status);
  const agentsError = useLiveAgentsList((s) => s.error);
  const approvals = useLiveApprovals((s) => s.approvals);
  const approvalsStatus = useLiveApprovals((s) => s.status);
  const events = useLiveActivity((s) => s.events);
  const activityStatus = useLiveActivity((s) => s.status);
  const activityError = useLiveActivity((s) => s.error);
  const spendStatus = useLiveUsage((s) => s.status);
  const spendSummary = useLiveUsage((s) => s.summary);
  const spendError = useLiveUsage((s) => s.error);
  const setCommandOpen = useUI((s) => s.setCommandOpen);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await Promise.all([
        useLiveBusiness.getState().load(businessId),
        useLiveProjects.getState().load(businessId),
        useLiveAgentsList.getState().load(businessId),
        useLiveApprovals.getState().load(businessId),
        useLiveActivity.getState().load({ businessId }),
        useLiveUsage.getState().load({ businessId }),
      ]);
      if (cancelled) return;
      const projectIds = useLiveProjects.getState().projects.map((project) => project.id);
      if (projectIds.length > 0) {
        await useLiveTasks.getState().loadAll(businessId, projectIds);
      }
    })();
    return () => {
      cancelled = true;
      useLiveBusiness.getState().reset();
      useLiveProjects.getState().reset();
      useLiveAgentsList.getState().reset();
      useLiveApprovals.getState().reset();
      useLiveActivity.getState().reset();
      useLiveUsage.getState().reset();
      useLiveTasks.getState().reset();
    };
  }, [businessId]);

  if (businessStatus === "error") {
    return (
      <>
        <PageHeader title="Workspace" badge={<LiveBadge />} sub="Server data for this business." />
        <Card className="p-8 text-center">
          <p className="text-body font-semibold text-fg">Couldn&apos;t load this business</p>
          <p className="mx-auto mt-1 max-w-md text-body-sm text-fg-muted">{businessError}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton onClick={() => void useLiveBusiness.getState().load(businessId)}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      </>
    );
  }

  if (!business) return <DashboardSkeleton />;

  const hired = agents.filter((agent) => agent.hired);
  const activeAgents = hired.filter((agent) =>
    ["working", "thinking", "reviewing"].includes(agent.status),
  );
  const activeProject =
    projects.find((project) => project.status === "in_progress") ?? projects[0] ?? null;
  const pending = approvals.filter((approval) => approval.status === "pending");
  const openTasks = tasks.filter((task) =>
    ["working", "waiting_dependency", "waiting_assets", "waiting_approval"].includes(task.status),
  );
  const budget = projects.reduce((total, project) => total + project.aiBudget, 0);
  const spendAmount = spendSummary.confirmedCost + spendSummary.estimatedCost;
  const spend =
    spendStatus === "ready"
      ? {
          amount: spendAmount,
          note: budget > 0 ? `of $${budget.toFixed(2)} project budget` : "Server-reported usage",
          budget: budget > 0 ? budget : undefined,
          unknownCount: spendSummary.unknownCount,
        }
      : null;

  // `businessStatus === "error"` is handled by the early return above, so it
  // is not part of this union (the narrowed type made the check dead code).
  const degraded =
    projectsStatus === "error" ||
    tasksStatus === "error" ||
    agentsStatus === "error" ||
    approvalsStatus === "error" ||
    activityStatus === "error" ||
    spendStatus === "error";
  const systemLabel = degraded
    ? "Degraded · a data source failed"
    : pending.length > 0
      ? `${pending.length} approval${pending.length === 1 ? "" : "s"} waiting`
      : "Online · systems normal";
  const systemTone = degraded ? "danger" : pending.length > 0 ? "warning" : "success";

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <DashboardHero
          mode="live"
          greetingName={business.ownerName}
          businessName={business.name}
          systemLabel={systemLabel}
          systemTone={systemTone}
          activeAgents={activeAgents.length}
          pendingApprovals={pending.length}
          onOpenCommand={() => setCommandOpen(true)}
        />
        <div className="flex flex-col gap-3">
          <CommandPanel mode="live" onOpen={() => setCommandOpen(true)} />
          <HealthPanel
            mode="live"
            pendingApprovals={pending.length}
            activeTasks={openTasks.length}
            totalTasks={tasks.length}
            healthScore={business.healthScore}
            lastActivity={events[0]?.at}
            degraded={degraded}
          />
        </div>
      </div>

      <SystemOverview
        mode="live"
        agents={agents}
        tasks={tasks}
        approvals={approvals}
        spend={spend}
        agentsState={agentsStatus}
        tasksState={tasksStatus}
        spendState={spendStatus}
      />

      <div id="agent-workflow" className="scroll-mt-4">
        <AgentWorkflow
          mode="live"
          project={activeProject}
          tasks={tasks}
          agents={agents}
          approvals={approvals}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
        <OperationsPanel
          mode="live"
          tasks={tasks}
          agents={agents}
          approvals={approvals}
          projects={projects}
          state={tasksStatus === "error" || agentsStatus === "error" ? "error" : tasksStatus}
          error={tasksError ?? agentsError ?? spendError}
        />
        <DepartmentsPanel agents={agents} state={agentsStatus} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QuickActions />
        <RecentActivity events={events} state={activityStatus} error={activityError} />
      </div>
    </>
  );
}

export default function HomePage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveDashboard businessId={currentBusinessId} />;
  }
  return <DemoHomePage />;
}
