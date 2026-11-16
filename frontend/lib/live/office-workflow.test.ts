import { describe, expect, it } from "vitest";
import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
  Task,
} from "@/lib/domain/types";
import {
  buildLiveOfficeWorkflowModel,
  LIVE_WORKFLOW_NO_SNAPSHOT_LIMITATION,
  selectLiveOfficeProject,
  selectLiveOfficeProjectForBusiness,
  type LiveWorkflowInput,
} from "@/lib/live/office-workflow";

const BUSINESS_ID = "biz_1";
const PROJECT_ID = "proj_1";

const PROJECT: Project = {
  id: PROJECT_ID,
  businessId: BUSINESS_ID,
  title: "Northstar Launch",
  goal: "Launch the Northstar product",
  status: "in_progress",
  progress: 0,
  budget: 10_000,
  aiBudget: 1_000,
  aiSpent: 100,
  deadline: "2026-12-31",
  teamIds: ["ag_lead", "ag_worker"],
  milestones: [],
};

function makeProject(
  overrides: Partial<Project> & Pick<Project, "id">,
): Project {
  return { ...PROJECT, ...overrides };
}

function makeAgent(
  overrides: Partial<Agent> & Pick<Agent, "id" | "name" | "role">,
): Agent {
  return {
    businessId: BUSINESS_ID,
    responsibilities: [],
    tools: [],
    permissions: { read: [], write: [], actions: [], denied: [] },
    status: "available",
    costPerMonth: 100,
    performance: {
      completionRate: 0,
      avgMinutes: 0,
      approvalRate: 0,
      rating: 0,
    },
    hired: true,
    avatarHue: 200,
    ...overrides,
  };
}

function makeTask(
  overrides: Partial<Task> & Pick<Task, "id" | "title">,
): Task {
  return {
    businessId: BUSINESS_ID,
    projectId: PROJECT_ID,
    ownerAgentId: "ag_worker",
    status: "not_started",
    progress: 0,
    dependsOn: [],
    costAccrued: 0,
    etaMinutes: 0,
    summary: "",
    ...overrides,
  };
}

function makeApproval(
  overrides: Partial<Approval> & Pick<Approval, "id">,
): Approval {
  return {
    businessId: BUSINESS_ID,
    projectId: PROJECT_ID,
    title: "Approval",
    requestedBy: "Owner",
    requestedByAgentId: "ag_lead",
    ageLabel: "1d",
    status: "pending",
    what: "",
    expectedResult: "",
    cost: "$0",
    affects: "",
    rationale: "",
    ...overrides,
  };
}

function makeEvent(id: string, at = "2026-01-01T00:00:00Z"): ActivityEvent {
  return { id, at, agentName: "Atlas", text: `Event ${id}`, kind: "work" };
}

function makeInput(overrides: Partial<LiveWorkflowInput> = {}): LiveWorkflowInput {
  return {
    businessId: BUSINESS_ID,
    projects: {
      status: "ready",
      businessId: BUSINESS_ID,
      projects: [PROJECT],
      error: null,
    },
    tasks: {
      status: "ready",
      businessId: BUSINESS_ID,
      scope: { kind: "project", projectId: PROJECT_ID },
      tasks: [],
      error: null,
    },
    agents: {
      status: "ready",
      businessId: BUSINESS_ID,
      agents: [],
      error: null,
    },
    approvals: {
      status: "ready",
      businessId: BUSINESS_ID,
      approvals: [],
      error: null,
    },
    activity: {
      status: "ready",
      businessId: BUSINESS_ID,
      projectId: null,
      events: [],
      error: null,
    },
    ...overrides,
  };
}

describe("live office project selection", () => {
  it("selects the first non-completed project and falls back to the first completed", () => {
    const completed = makeProject({ id: "proj_done", status: "completed" });
    const planning = makeProject({ id: "proj_plan", status: "planning" });
    const inProgress = makeProject({ id: "proj_live", status: "in_progress" });

    expect(selectLiveOfficeProject([completed, planning, inProgress])?.id).toBe(
      "proj_plan",
    );
    expect(selectLiveOfficeProject([inProgress, planning])?.id).toBe("proj_live");
    expect(selectLiveOfficeProject([completed])?.id).toBe("proj_done");
    expect(selectLiveOfficeProject([])).toBeNull();
  });

  it("selects only ready projects proven to belong to the active business", () => {
    const completed = makeProject({ id: "proj_done", status: "completed" });
    const planning = makeProject({ id: "proj_plan", status: "planning" });
    const source = {
      status: "ready" as const,
      businessId: BUSINESS_ID,
      projects: [completed, planning],
    };

    expect(
      selectLiveOfficeProjectForBusiness(source, BUSINESS_ID)?.id,
    ).toBe("proj_plan");
    expect(
      selectLiveOfficeProjectForBusiness(source, "biz_other"),
    ).toBeNull();
    expect(
      selectLiveOfficeProjectForBusiness(
        { ...source, status: "loading" },
        BUSINESS_ID,
      ),
    ).toBeNull();
    expect(
      selectLiveOfficeProjectForBusiness(
        { ...source, status: "error" },
        BUSINESS_ID,
      ),
    ).toBeNull();
  });
});

describe("critical source readiness", () => {
  it("returns no_project with no model when the ready project list is empty", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        projects: {
          status: "ready",
          businessId: BUSINESS_ID,
          projects: [],
          error: null,
        },
        agents: {
          status: "error",
          businessId: BUSINESS_ID,
          agents: [],
          error: "Agents failed.",
        },
      }),
    );

    expect(result.status).toBe("no_project");
    expect(result.model).toBeNull();
  });

  it("does not turn project loading or failure into an empty project", () => {
    const loading = buildLiveOfficeWorkflowModel(
      makeInput({
        projects: {
          status: "loading",
          businessId: BUSINESS_ID,
          projects: [],
          error: null,
        },
      }),
    );
    expect(loading.status).toBe("loading");
    expect(loading.model).toBeNull();

    const failed = buildLiveOfficeWorkflowModel(
      makeInput({
        projects: {
          status: "error",
          businessId: BUSINESS_ID,
          projects: [],
          error: "Projects failed.",
        },
      }),
    );
    expect(failed.status).toBe("unavailable");
    expect(failed.model).toBeNull();
    if (failed.status !== "unavailable") throw new Error("expected unavailable");
    expect(failed.reasons.join(" ")).toContain("Projects failed.");
  });

  it("uses only selected-project tasks and preserves dependencies", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({ id: "t_2", title: "Prerequisite", status: "completed" }),
            makeTask({ id: "t_1", title: "Dependent", dependsOn: ["t_2"] }),
            makeTask({
              id: "t_other",
              title: "Other project",
              projectId: "proj_other",
            }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.tasks.map((view) => view.task.id)).toEqual([
      "t_1",
      "t_2",
    ]);
    expect(result.model.tasks[0].waitingOn).toEqual([
      {
        id: "t_2",
        title: "Prerequisite",
        loaded: true,
        completed: true,
      },
    ]);
  });

  it("requires business-scope task proof to include the selected project", () => {
    const missing = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "business", projectIds: ["proj_other"] },
          tasks: [],
          error: null,
        },
      }),
    );
    expect(missing.status).toBe("unavailable");
    expect(missing.model).toBeNull();

    const included = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: {
            kind: "business",
            projectIds: [PROJECT_ID, "proj_other"],
          },
          tasks: [],
          error: null,
        },
      }),
    );
    expect(included.status).toBe("ready");
  });

  it("blocks task loading and failure instead of inventing Review", () => {
    const loading = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "loading",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [],
          error: null,
        },
      }),
    );
    expect(loading.status).toBe("loading");
    expect(loading.model).toBeNull();

    const failed = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "error",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [],
          error: "Task page two failed.",
        },
      }),
    );
    expect(failed.status).toBe("unavailable");
    expect(failed.model).toBeNull();
    if (failed.status !== "unavailable") throw new Error("expected unavailable");
    expect(failed.reasons.join(" ")).toContain("Task page two failed.");
  });

  it("distinguishes complete empty tasks from incomplete task data", () => {
    const empty = buildLiveOfficeWorkflowModel(makeInput());
    expect(empty.status).toBe("ready");
    if (!empty.model) throw new Error("expected model");
    expect(empty.model.review.state).toBe("empty");

    const incomplete = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: null,
          tasks: [],
          error: null,
        },
      }),
    );
    expect(incomplete.status).toBe("unavailable");
    expect(incomplete.model).toBeNull();
  });

  it("derives Review readiness only from a complete task set", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({ id: "t_done", title: "Done", status: "completed" }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.review.state).toBe("ready");
    expect(result.model.review.note).toContain("every task is completed");
  });
});

describe("auxiliary source readiness", () => {
  it("returns a ready core while all auxiliary sources are idle", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [makeTask({ id: "t_1", title: "Core task" })],
          error: null,
        },
        agents: {
          status: "idle",
          businessId: null,
          agents: [],
          error: null,
        },
        approvals: {
          status: "idle",
          businessId: null,
          approvals: [],
          error: null,
        },
        activity: {
          status: "idle",
          businessId: null,
          projectId: null,
          events: [],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.tasks.map((view) => view.task.id)).toEqual(["t_1"]);
    expect(result.model.agentsAvailability).toBe("loading");
    expect(result.model.teamLead.state).toBe("loading");
    expect(result.model.teamLead.agent).toBeNull();
    expect(result.model.tasks[0].owner).toBeNull();
    expect(result.model.approvals.state).toBe("loading");
    expect(result.model.approvals.completeness).toBe("loading");
    expect(result.model.approvals.pendingCount).toBe(0);
    expect(result.model.activity.state).toBe("loading");
    expect(result.model.activity.preview).toEqual([]);
  });

  it("does not block on unavailable agents and marks Team Lead source_unavailable", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [makeTask({ id: "t_1", title: "Task" })],
          error: null,
        },
        agents: {
          status: "error",
          businessId: BUSINESS_ID,
          agents: [],
          error: "Agents failed.",
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.agentsAvailability).toBe("unavailable");
    expect(result.model.teamLead.state).toBe("source_unavailable");
    expect(result.model.teamLead.note).toContain("Agent data is unavailable");
    expect(result.model.tasks[0].owner).toBeNull();
    expect(result.model.limitations.join(" ")).toContain(
      "Team Lead and task owners cannot be confirmed",
    );
  });

  it("preserves a known roster with no Team Lead", () => {
    const worker = makeAgent({
      id: "ag_worker",
      name: "Worker",
      role: "Engineer",
    });
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        agents: {
          status: "ready",
          businessId: BUSINESS_ID,
          agents: [worker],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.agentsAvailability).toBe("available");
    expect(result.model.teamLead.state).toBe("not_found");
    expect(result.model.teamLead.note).toContain("No Team Lead role");
  });

  it("does not block on loading approvals and marks gated tasks loading", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_gated",
              title: "Gated task",
              status: "waiting_approval",
              approvalGateId: "ap_1",
            }),
            makeTask({
              id: "t_side",
              title: "Side-linked task",
              status: "not_started",
            }),
            makeTask({
              id: "t_done",
              title: "Done dependency",
              status: "completed",
            }),
            makeTask({
              id: "t_dep_side",
              title: "Dependency-complete side link",
              status: "waiting_dependency",
              dependsOn: ["t_done"],
            }),
          ],
          error: null,
        },
        approvals: {
          status: "loading",
          businessId: BUSINESS_ID,
          approvals: [],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.state).toBe("loading");
    expect(result.model.approvals.completeness).toBe("loading");
    expect(result.model.approvals.pendingCount).toBe(0);
    for (const view of result.model.tasks) {
      expect(view.gateState).toBe("loading");
      expect(view.lane).not.toBe("ready");
    }
    expect(result.model.tasks.find((view) => view.task.id === "t_gated")?.lane).toBe(
      "waiting_user",
    );
    expect(result.model.tasks.find((view) => view.task.id === "t_side")?.lane).toBe(
      "waiting_user",
    );
    expect(result.model.tasks.find((view) => view.task.id === "t_dep_side")?.lane).toBe(
      "waiting_user",
    );
  });

  it("resolves a side-linked gate after approvals finish loading", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({ id: "t_side", title: "Side-linked", status: "not_started" }),
          ],
          error: null,
        },
        approvals: {
          status: "ready",
          businessId: BUSINESS_ID,
          approvals: [
            makeApproval({
              id: "ap_side",
              status: "pending",
              gatesTaskId: "t_side",
            }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.tasks[0].gateState).toBe("pending");
    expect(result.model.tasks[0].gate?.id).toBe("ap_side");
  });

  it("does not block on loading activity or present an empty feed", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        activity: {
          status: "loading",
          businessId: BUSINESS_ID,
          projectId: null,
          events: [makeEvent("evt_stale")],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.activity.state).toBe("loading");
    expect(result.model.activity.preview).toEqual([]);
    expect(result.model.activity.providedCount).toBe(0);
    expect(result.model.activity.scope).toBe("unknown");
    expect(result.model.activity.scopedToProject).toBe(false);
  });

  it("uses a Team Lead only from project membership", () => {
    const lead = makeAgent({
      id: "ag_lead",
      name: "Atlas",
      role: "Team Lead",
    });
    const outsider = makeAgent({
      id: "ag_outsider",
      name: "Outsider",
      role: "Team Lead",
    });
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        projects: {
          status: "ready",
          businessId: BUSINESS_ID,
          projects: [makeProject({ id: PROJECT_ID, teamIds: ["ag_lead"] })],
          error: null,
        },
        agents: {
          status: "ready",
          businessId: BUSINESS_ID,
          agents: [outsider, lead],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.teamLead.agent?.id).toBe("ag_lead");
  });

  it("uses partial approvals for a successful first page and preserves project filtering", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_gated",
              title: "Gated",
              status: "waiting_approval",
              approvalGateId: "ap_selected",
            }),
          ],
          error: null,
        },
        approvals: {
          status: "ready",
          businessId: BUSINESS_ID,
          approvals: [
            makeApproval({ id: "ap_selected", status: "pending" }),
            makeApproval({
              id: "ap_other",
              projectId: "proj_other",
              status: "pending",
            }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.completeness).toBe("partial");
    expect(result.model.approvals.pending.map((a) => a.id)).toEqual([
      "ap_selected",
    ]);
    expect(result.model.tasks[0].gateState).toBe("pending");
    expect(result.model.tasks[0].gate?.id).toBe("ap_selected");
  });

  it("does not let ready approvals from another business become authoritative", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_direct",
              title: "Direct gate",
              approvalGateId: "ap_other",
            }),
          ],
          error: null,
        },
        approvals: {
          status: "ready",
          businessId: "biz_other",
          approvals: [
            makeApproval({ id: "ap_other", status: "approved" }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.completeness).toBe("unavailable");
    expect(result.model.approvals.state).toBe("unavailable");
    expect(result.model.approvals.pendingCount).toBe(0);
    expect(result.model.approvals.decidedCount).toBe(0);
    expect(result.model.tasks[0].gateState).toBe("unverified");
    expect(result.model.tasks[0].gate).toBeNull();
    expect(result.model.tasks[0].lane).toBe("waiting_user");
  });

  it("labels an unavailable approvals source unavailable without blocking", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        approvals: {
          status: "error",
          businessId: BUSINESS_ID,
          approvals: [],
          error: "Approvals failed.",
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.completeness).toBe("unavailable");
    expect(result.model.approvals.state).toBe("unavailable");
  });

  it("ignores retained approval records when the approvals source is unavailable", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_stale",
              title: "Stale approved gate",
              approvalGateId: "ap_stale",
            }),
          ],
          error: null,
        },
        approvals: {
          status: "error",
          businessId: BUSINESS_ID,
          approvals: [
            makeApproval({ id: "ap_stale", status: "approved" }),
          ],
          error: "Approvals failed.",
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.completeness).toBe("unavailable");
    expect(result.model.tasks[0].gateState).toBe("unverified");
    expect(result.model.tasks[0].gate).toBeNull();
    expect(result.model.tasks[0].lane).not.toBe("ready");
  });

  it("treats a direct gate ID absent from a partial page as unverified", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_direct",
              title: "Direct gate",
              approvalGateId: "ap_absent",
            }),
          ],
          error: null,
        },
        approvals: {
          status: "ready",
          businessId: BUSINESS_ID,
          approvals: [],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.tasks[0].gateState).toBe("unverified");
    expect(result.model.tasks[0].lane).toBe("waiting_user");
  });

  it("uses a newly loaded approved gate after a successful approvals retry", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks: {
          status: "ready",
          businessId: BUSINESS_ID,
          scope: { kind: "project", projectId: PROJECT_ID },
          tasks: [
            makeTask({
              id: "t_new",
              title: "New gate",
              approvalGateId: "ap_new",
            }),
          ],
          error: null,
        },
        approvals: {
          status: "ready",
          businessId: BUSINESS_ID,
          approvals: [
            makeApproval({ id: "ap_new", status: "approved" }),
          ],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.tasks[0].gateState).toBe("approved");
    expect(result.model.tasks[0].lane).toBe("ready");
  });

  it("keeps a successful empty approvals page partial", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        approvals: {
          status: "ready",
          businessId: BUSINESS_ID,
          approvals: [],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.approvals.state).toBe("empty");
    expect(result.model.approvals.completeness).toBe("partial");
  });

  it("does not present unavailable activity as a successful empty feed", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        activity: {
          status: "error",
          businessId: BUSINESS_ID,
          projectId: null,
          events: [],
          error: "Activity failed.",
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.activity.state).toBe("unavailable");
    expect(result.model.activity.preview).toEqual([]);
    expect(result.model.activity.scope).toBe("unknown");
    expect(result.model.activity.scopedToProject).toBe(false);
    expect(result.model.limitations).toContain("Activity data is unavailable.");
  });

  it("refuses to relabel project-scoped activity as business activity", () => {
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        activity: {
          status: "ready",
          businessId: BUSINESS_ID,
          projectId: PROJECT_ID,
          events: [makeEvent("evt_project")],
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.activity.state).toBe("unavailable");
    expect(result.model.activity.preview).toEqual([]);
  });

  it("preserves business activity order and preview behavior", () => {
    const events = Array.from({ length: 10 }, (_, index) =>
      makeEvent(`evt_${index}`),
    );
    const result = buildLiveOfficeWorkflowModel(
      makeInput({
        activity: {
          status: "ready",
          businessId: BUSINESS_ID,
          projectId: null,
          events,
          error: null,
        },
      }),
    );

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.activity.state).toBe("available");
    expect(result.model.activity.scope).toBe("business");
    expect(result.model.activity.providedCount).toBe(10);
    expect(result.model.activity.preview.map((event) => event.id)).toEqual([
      "evt_0",
      "evt_1",
      "evt_2",
      "evt_3",
      "evt_4",
      "evt_5",
      "evt_6",
      "evt_7",
    ]);
  });

  it("distinguishes an available empty activity feed", () => {
    const result = buildLiveOfficeWorkflowModel(makeInput());

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.activity.state).toBe("available");
    expect(result.model.activity.providedCount).toBe(0);
  });

  it("transitions auxiliary loading to ready or error without changing the core", () => {
    const tasks = {
      status: "ready" as const,
      businessId: BUSINESS_ID,
      scope: { kind: "project" as const, projectId: PROJECT_ID },
      tasks: [makeTask({ id: "t_core", title: "Core task" })],
      error: null,
    };
    const lead = makeAgent({ id: "ag_lead", name: "Atlas", role: "Team Lead" });

    const loading = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks,
        agents: {
          status: "loading",
          businessId: BUSINESS_ID,
          agents: [],
          error: null,
        },
      }),
    );
    const ready = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks,
        agents: {
          status: "ready",
          businessId: BUSINESS_ID,
          agents: [lead],
          error: null,
        },
      }),
    );
    const failed = buildLiveOfficeWorkflowModel(
      makeInput({
        tasks,
        agents: {
          status: "error",
          businessId: BUSINESS_ID,
          agents: [],
          error: "Agents failed.",
        },
      }),
    );

    expect(loading.status).toBe("ready");
    expect(ready.status).toBe("ready");
    expect(failed.status).toBe("ready");
    if (!loading.model || !ready.model || !failed.model) {
      throw new Error("expected models");
    }
    expect(loading.model.teamLead.state).toBe("loading");
    expect(ready.model.teamLead.state).toBe("available");
    expect(ready.model.teamLead.agent?.id).toBe("ag_lead");
    expect(failed.model.teamLead.state).toBe("source_unavailable");
    expect(loading.model.tasks.map((view) => view.task.id)).toEqual(["t_core"]);
    expect(ready.model.tasks.map((view) => view.task.id)).toEqual(["t_core"]);
    expect(failed.model.tasks.map((view) => view.task.id)).toEqual(["t_core"]);
  });
});

describe("determinism and limitations", () => {
  it("is deterministic and does not mutate inputs", () => {
    const input = makeInput({
      tasks: {
        status: "ready",
        businessId: BUSINESS_ID,
        scope: { kind: "project", projectId: PROJECT_ID },
        tasks: [makeTask({ id: "t_1", title: "Task" })],
        error: null,
      },
    });
    const before = JSON.stringify(input);

    const first = buildLiveOfficeWorkflowModel(input);
    const second = buildLiveOfficeWorkflowModel(input);

    expect(first).toEqual(second);
    expect(JSON.stringify(input)).toBe(before);
  });

  it("adds the no-snapshot limitation to a ready model", () => {
    const result = buildLiveOfficeWorkflowModel(makeInput());

    expect(result.status).toBe("ready");
    if (!result.model) throw new Error("expected model");
    expect(result.model.limitations).toContain(
      LIVE_WORKFLOW_NO_SNAPSHOT_LIMITATION,
    );
  });
});
