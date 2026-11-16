"use client";

import { useEffect } from "react";
import {
  Card,
  LiveBadge,
  PageHeader,
  PrimaryButton,
  PrimaryLink,
  Skeleton,
} from "@/components/system/ui";
import { WorkflowView } from "@/components/workflow/WorkflowView";
import {
  loadLiveOfficeTasksForProject,
  retryLiveOfficeApprovals,
  retryLiveOfficeProjects,
  retryLiveOfficeTasks,
} from "@/lib/live/live-office-actions";
import {
  buildLiveOfficeWorkflowModel,
  selectLiveOfficeProjectForBusiness,
} from "@/lib/live/office-workflow";
import {
  useLiveActivity,
  useLiveAgentsList,
  useLiveApprovals,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";

function WorkflowLoading({ reasons }: { reasons: readonly string[] }) {
  return (
    <Card className="mt-4 p-5">
      <div role="status" aria-label="Loading AI office">
        <p className="text-[14px] font-semibold">Loading AI office…</p>
        <p className="mt-1 text-[12.5px] text-ink-500">
          {reasons.join(" ") || "Waiting for server data."}
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map((entry) => (
            <Skeleton key={entry} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      </div>
    </Card>
  );
}

function WorkflowUnavailable({
  reasons,
  onRetry,
  retryLabel,
}: {
  reasons: readonly string[];
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <Card className="mt-4 p-6">
      <div role="alert">
        <p className="text-[14px] font-semibold">AI office unavailable</p>
        <p className="mt-1 max-w-xl text-[12.5px] text-ink-500">
          {reasons.join(" ") || "The server could not supply the workflow."}
        </p>
        {onRetry && retryLabel && (
          <div className="mt-3">
            <PrimaryButton onClick={onRetry}>{retryLabel}</PrimaryButton>
          </div>
        )}
      </div>
    </Card>
  );
}

function NoProject() {
  return (
    <Card className="mt-4 p-6">
      <p className="text-[14px] font-semibold">No project available</p>
      <p className="mt-1 max-w-xl text-[12.5px] text-ink-500">
        Create a project to see the goal, team lead, task lanes, approvals and
        review readiness.
      </p>
      <div className="mt-3">
        <PrimaryLink href="/projects">Open projects</PrimaryLink>
      </div>
    </Card>
  );
}

export function LiveOffice({ businessId }: { businessId: string }) {
  const agents = useLiveAgentsList((s) => s.agents);
  const agentsStatus = useLiveAgentsList((s) => s.status);
  const agentsBusinessId = useLiveAgentsList((s) => s.businessId);
  const agentsError = useLiveAgentsList((s) => s.error);

  const projects = useLiveProjects((s) => s.projects);
  const projectsStatus = useLiveProjects((s) => s.status);
  const projectsBusinessId = useLiveProjects((s) => s.businessId);
  const projectsError = useLiveProjects((s) => s.error);

  const approvals = useLiveApprovals((s) => s.approvals);
  const approvalsStatus = useLiveApprovals((s) => s.status);
  const approvalsBusinessId = useLiveApprovals((s) => s.businessId);
  const approvalsError = useLiveApprovals((s) => s.error);

  const activityEvents = useLiveActivity((s) => s.events);
  const activityStatus = useLiveActivity((s) => s.status);
  const activityBusinessId = useLiveActivity((s) => s.businessId);
  const activityProjectId = useLiveActivity((s) => s.projectId);
  const activityError = useLiveActivity((s) => s.error);

  const tasks = useLiveTasks((s) => s.tasks);
  const tasksStatus = useLiveTasks((s) => s.status);
  const tasksKey = useLiveTasks((s) => s.key);
  const tasksError = useLiveTasks((s) => s.error);

  useEffect(() => {
    void useLiveAgentsList.getState().load(businessId);
    void useLiveProjects.getState().load(businessId);
    void useLiveApprovals.getState().load(businessId);
    void useLiveActivity.getState().load({ businessId });
    return () => {
      useLiveAgentsList.getState().reset();
      useLiveProjects.getState().reset();
      useLiveTasks.getState().reset();
      useLiveApprovals.getState().reset();
      useLiveActivity.getState().reset();
    };
  }, [businessId]);

  const selectedProject = selectLiveOfficeProjectForBusiness(
    {
      status: projectsStatus,
      businessId: projectsBusinessId,
      projects,
    },
    businessId,
  );
  const selectedProjectId = selectedProject?.id ?? null;
  const expectedTaskKey =
    selectedProjectId === null ? null : `${businessId}/${selectedProjectId}`;
  const tasksKeyMatches =
    expectedTaskKey !== null && tasksKey === expectedTaskKey;

  useEffect(() => {
    void loadLiveOfficeTasksForProject(
      businessId,
      selectedProjectId,
      projectsBusinessId,
    );
  }, [businessId, projectsBusinessId, selectedProjectId]);

  const taskSourceStatus = tasksKeyMatches ? tasksStatus : "loading";
  const taskSourceError = tasksKeyMatches ? tasksError : null;

  const result = buildLiveOfficeWorkflowModel({
    businessId,
    projects: {
      status: projectsStatus,
      businessId: projectsBusinessId,
      projects,
      error: projectsError,
    },
    tasks: {
      status: taskSourceStatus,
      businessId,
      scope:
        selectedProjectId === null
          ? null
          : { kind: "project", projectId: selectedProjectId },
      tasks: tasksKeyMatches ? tasks : [],
      error: taskSourceError,
    },
    agents: {
      status: agentsStatus,
      businessId: agentsBusinessId,
      agents,
      error: agentsError,
    },
    approvals: {
      status: approvalsStatus,
      businessId: approvalsBusinessId,
      approvals,
      error: approvalsError,
    },
    activity: {
      status: activityStatus,
      businessId: activityBusinessId,
      projectId: activityProjectId,
      events: activityEvents,
      error: activityError,
    },
  });

  const hiredAgents = agents.filter((agent) => agent.hired);
  const workingNow = hiredAgents.filter(
    (agent) => agent.status === "working" || agent.status === "thinking",
  );
  const agentsBelongToBusiness = agentsBusinessId === businessId;
  const agentsReady = agentsBelongToBusiness && agentsStatus === "ready";
  const agentsFailed = agentsBelongToBusiness && agentsStatus === "error";
  const projectsFailed = projectsStatus === "error";
  const tasksFailed = tasksKeyMatches && tasksStatus === "error";

  return (
    <>
      <PageHeader
        title="AI Office"
        badge={<LiveBadge />}
        sub={
          agentsReady
            ? `${hiredAgents.length} AI employee${hiredAgents.length === 1 ? "" : "s"} · ${workingNow.length} active now`
            : agentsFailed
              ? "AI team unavailable"
              : "Loading AI team…"
        }
      />

      {approvalsStatus === "error" && (
        <Card className="mt-4 border-amber-200 bg-amber-50 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div role="alert" className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-bold text-amber-800 dark:text-amber-200">
                Approvals unavailable
              </p>
              <p className="mt-0.5 text-[12.5px] text-amber-700 dark:text-amber-200/80">
                {approvalsError ??
                  "Approval data could not be loaded; gates cannot be confirmed."}{" "}
                Previously loaded approval records are not used and all gates
                remain unverified.
              </p>
            </div>
            <PrimaryButton
              onClick={() => void retryLiveOfficeApprovals(businessId)}
            >
              Retry approvals
            </PrimaryButton>
          </div>
        </Card>
      )}

      {result.status === "loading" && (
        <WorkflowLoading reasons={result.reasons} />
      )}

      {result.status === "unavailable" && (
        <WorkflowUnavailable
          reasons={result.reasons}
          onRetry={
            projectsFailed
              ? () => void retryLiveOfficeProjects(businessId)
              : tasksFailed && selectedProjectId !== null
                ? () =>
                    void retryLiveOfficeTasks(businessId, selectedProjectId)
                : undefined
          }
          retryLabel={
            projectsFailed
              ? "Retry projects"
              : tasksFailed
                ? "Retry tasks"
                : undefined
          }
        />
      )}

      {result.status === "no_project" && <NoProject />}

      {result.status === "ready" && (
        <WorkflowView model={result.model} className="mt-4" />
      )}
    </>
  );
}
