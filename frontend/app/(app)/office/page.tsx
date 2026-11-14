"use client";

import { useEffect, useMemo } from "react";
import { DemoBadge, PageHeader } from "@/components/system/ui";
import { EngineControls } from "@/components/system/EngineControls";
import { WorkflowView } from "@/components/workflow/WorkflowView";
import { LiveOffice } from "@/components/live/LiveOffice";
import { startLoop } from "@/lib/sim/engine";
import { useActivity, useAgents, useApprovals, useBusiness, useProjects } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";
import { buildWorkflowModel } from "@/lib/workflow";

function DemoOfficePage() {
  const agents = useAgents((s) => s.agents);
  const events = useActivity((s) => s.events);
  const allApprovals = useApprovals((s) => s.approvals);
  const { currentBusinessId } = useBusiness();
  const activeProjectId = useProjects((s) => s.activeProjectId);
  const allTasks = useProjects((s) => s.tasks);
  const projects = useProjects((s) => s.projects);
  const activeProject = projects.find((p) => p.id === activeProjectId);
  const hiredAgents = agents.filter((a) => a.hired);
  const workingNow = hiredAgents.filter(
    (a) => a.status === "working" || a.status === "thinking",
  ).length;

  const workflow = useMemo(
    () =>
      buildWorkflowModel({
        project: activeProject ?? null,
        tasks: allTasks,
        agents,
        approvals: allApprovals,
        events,
        mode: "demo",
        activityScope: "business",
        // The local demo store is the full approval dataset: it is seeded and
        // updated in place, and DEMO has no paging or remote partial fetches.
        approvalsCompleteness: "complete",
      }),
    [activeProject, allApprovals, agents, allTasks, events],
  );

  // The office comes alive on open; the guarded engine ignores duplicate starts.
  useEffect(() => {
    startLoop({ businessId: currentBusinessId, projectId: activeProjectId });
  }, [currentBusinessId, activeProjectId]);

  return (
    <>
      <PageHeader
        title="AI Office"
        badge={<DemoBadge />}
        sub={`${workingNow} of ${hiredAgents.length} simulated AI employee${hiredAgents.length === 1 ? "" : "s"} working`}
        actions={
          <EngineControls
            scope={{ businessId: currentBusinessId, projectId: activeProjectId }}
            compact
          />
        }
      />
      <WorkflowView model={workflow} className="mt-4" />
    </>
  );
}

export default function OfficePage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveOffice businessId={currentBusinessId} />;
  }
  return <DemoOfficePage />;
}
