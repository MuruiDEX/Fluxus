import { describe, expect, it } from "vitest";
import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
} from "@/lib/domain/types";
import {
  WORKFLOW_LANE_ORDER,
  buildWorkflowModel,
} from "@/lib/workflow";
import type {
  WorkflowInput,
  WorkflowLaneKey,
  WorkflowModel,
  WorkflowTaskInput,
  WorkflowTaskView,
} from "@/lib/workflow";

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
  overrides: Partial<WorkflowTaskInput> & Pick<WorkflowTaskInput, "id" | "title">,
): WorkflowTaskInput {
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

function build(overrides: Partial<WorkflowInput> = {}): WorkflowModel {
  return buildWorkflowModel({
    project: PROJECT,
    tasks: [],
    agents: [],
    approvals: [],
    events: [],
    mode: "live",
    activityScope: "project",
    ...overrides,
  });
}

function taskView(model: WorkflowModel, id: string): WorkflowTaskView {
  const view = model.tasks.find((entry) => entry.task.id === id);
  if (!view) throw new Error(`Missing task view: ${id}`);
  return view;
}

function laneKeys(model: WorkflowModel): WorkflowLaneKey[] {
  return model.lanes.map((lane) => lane.key);
}

describe("task classification precedence", () => {
  it("keeps a completed task in Completed even when its gate is rejected", () => {
    const task = makeTask({
      id: "t_done",
      title: "Completed task",
      status: "completed",
      approvalGateId: "ap_rejected",
    });
    const model = build({
      tasks: [task],
      approvals: [
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    expect(taskView(model, "t_done").lane).toBe("completed");
    expect(taskView(model, "t_done").gateState).toBe("rejected");
    expect(model.counts.completed).toBe(1);
  });

  it("places a failed task in Needs attention", () => {
    const model = build({
      tasks: [makeTask({ id: "t_failed", title: "Failed", status: "failed" })],
    });

    const view = taskView(model, "t_failed");
    expect(view.lane).toBe("attention");
    expect(view.knownStatus).toBe("failed");
  });

  it("places a working task in Running", () => {
    const model = build({
      tasks: [makeTask({ id: "t_working", title: "Working", status: "working" })],
    });

    expect(taskView(model, "t_working").lane).toBe("running");
  });

  it("places a working task with a rejected gate in Needs attention", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_working_rejected",
          title: "Working rejected",
          status: "working",
          approvalGateId: "ap_rejected",
        }),
      ],
      approvals: [
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    const view = taskView(model, "t_working_rejected");
    expect(view.lane).toBe("attention");
    expect(view.note ?? "").toContain("rejected");
  });

  it("classifies a gated-free not_started task as Ready to run", () => {
    const model = build({
      tasks: [makeTask({ id: "t_ready", title: "Ready" })],
    });

    expect(taskView(model, "t_ready").lane).toBe("ready");
    expect(taskView(model, "t_ready").gateState).toBe("none");
  });

  it("waits when a not_started task has an unmet dependency", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dep", title: "Dependency" }),
        makeTask({
          id: "t_blocked",
          title: "Blocked",
          dependsOn: ["t_dep"],
        }),
      ],
    });

    expect(taskView(model, "t_blocked").lane).toBe("waiting_dependency");
  });

  it("treats an unknown dependency id as unmet and explains it", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_missing_dep",
          title: "Missing dependency",
          dependsOn: ["t_ghost"],
        }),
      ],
    });

    const view = taskView(model, "t_missing_dep");
    expect(view.lane).toBe("waiting_dependency");
    expect(view.waitingOn).toEqual([
      { id: "t_ghost", title: null, loaded: false, completed: false },
    ]);
    expect(view.note ?? "").toContain("Dependency not loaded");
    expect(model.limitations.join(" ")).toContain("dependencies");
  });

  it("waits for a pending gate after dependencies are complete", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dep", title: "Dependency", status: "completed" }),
        makeTask({
          id: "t_gated",
          title: "Gated",
          dependsOn: ["t_dep"],
          approvalGateId: "ap_pending",
        }),
      ],
      approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
    });

    expect(taskView(model, "t_gated").lane).toBe("waiting_user");
  });

  it("becomes ready with an approved gate after dependencies are complete", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dep", title: "Dependency", status: "completed" }),
        makeTask({
          id: "t_gated",
          title: "Gated",
          dependsOn: ["t_dep"],
          approvalGateId: "ap_approved",
        }),
      ],
      approvals: [makeApproval({ id: "ap_approved", status: "approved" })],
    });

    expect(taskView(model, "t_gated").lane).toBe("ready");
  });

  it("places a not_started task with a rejected gate in Needs attention", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_rejected",
          title: "Rejected",
          approvalGateId: "ap_rejected",
        }),
      ],
      approvals: [
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    expect(taskView(model, "t_rejected").lane).toBe("attention");
  });

  it("gives an unmet dependency precedence over a pending gate", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dep", title: "Dependency" }),
        makeTask({
          id: "t_both",
          title: "Both blockers",
          dependsOn: ["t_dep"],
          approvalGateId: "ap_pending",
        }),
      ],
      approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
    });

    const view = taskView(model, "t_both");
    expect(view.lane).toBe("waiting_dependency");
    expect(view.gateState).toBe("pending");
    expect(view.gate?.id).toBe("ap_pending");
    expect(view.note ?? "").toContain("Approval gate is pending");
  });
});
describe("waiting_approval", () => {
  it("waits for a pending gate", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_waiting_gate",
          title: "Waiting gate",
          status: "waiting_approval",
          approvalGateId: "ap_pending",
        }),
      ],
      approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
    });

    expect(taskView(model, "t_waiting_gate").lane).toBe("waiting_user");
  });

  it("is ready to run when the gate is approved, with a stale-status note", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_stale_gate",
          title: "Stale gate",
          status: "waiting_approval",
          approvalGateId: "ap_approved",
        }),
      ],
      approvals: [makeApproval({ id: "ap_approved", status: "approved" })],
    });

    const view = taskView(model, "t_stale_gate");
    expect(view.lane).toBe("ready");
    expect(view.note ?? "").toContain("waiting_approval");
    expect(view.note ?? "").toContain("approved");
  });

  it("needs attention when the gate is not loaded", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_missing_gate",
          title: "Missing gate",
          status: "waiting_approval",
          approvalGateId: "ap_missing",
        }),
      ],
    });

    const view = taskView(model, "t_missing_gate");
    expect(view.lane).toBe("attention");
    expect(view.gateState).toBe("missing");
    expect(view.note ?? "").toContain("not loaded");
    expect(model.limitations.join(" ")).toContain("approval gates");
  });

  it("needs attention when waiting_approval links no gate at all", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_no_gate",
          title: "No gate",
          status: "waiting_approval",
        }),
      ],
    });

    const view = taskView(model, "t_no_gate");
    expect(view.lane).toBe("attention");
    expect(view.gateState).toBe("none");
    expect(view.note ?? "").toContain("no approval gate");
  });

  it("does not let a rejected gate hide behind unchanged waiting status", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_rejected_waiting",
          title: "Rejected waiting",
          status: "waiting_approval",
          dependsOn: ["t_ghost"],
          approvalGateId: "ap_rejected",
        }),
      ],
      approvals: [
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    expect(taskView(model, "t_rejected_waiting").lane).toBe("attention");
  });
});

describe("waiting_assets", () => {
  it("uses a neutral classification because human-input semantics are unconfirmed", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_assets",
          title: "Assets",
          status: "waiting_assets",
        }),
      ],
    });

    const view = taskView(model, "t_assets");
    expect(view.lane).toBe("ready");
    expect(view.lane).not.toBe("waiting_user");
    expect(view.note ?? "").toContain("waiting_assets");
    expect(view.note ?? "").toContain("no producer");
  });

  it("waits on unmet dependencies before considering assets", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_asset_dep", title: "Asset dependency" }),
        makeTask({
          id: "t_assets_blocked",
          title: "Assets blocked",
          status: "waiting_assets",
          dependsOn: ["t_asset_dep"],
        }),
      ],
    });

    const view = taskView(model, "t_assets_blocked");
    expect(view.lane).toBe("waiting_dependency");
    expect(view.note ?? "").toContain("waiting_assets");
  });

  it("waits for a genuinely pending approval gate", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_assets_gated",
          title: "Assets gated",
          status: "waiting_assets",
          approvalGateId: "ap_pending",
        }),
      ],
      approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
    });

    const view = taskView(model, "t_assets_gated");
    expect(view.lane).toBe("waiting_user");
    expect(view.note ?? "").toContain("pending");
  });
});

describe("waiting_dependency", () => {
  it("stays in Waiting on dependencies while a dependency is unfinished", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dep_open", title: "Open dependency" }),
        makeTask({
          id: "t_waiting_dep",
          title: "Waiting dependency",
          status: "waiting_dependency",
          dependsOn: ["t_dep_open"],
        }),
      ],
    });

    expect(taskView(model, "t_waiting_dep").lane).toBe(
      "waiting_dependency",
    );
  });

  it("becomes ready when every dependency is complete, with a stale note", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_dep_done",
          title: "Done dependency",
          status: "completed",
        }),
        makeTask({
          id: "t_stale_dep",
          title: "Stale dependency status",
          status: "waiting_dependency",
          dependsOn: ["t_dep_done"],
        }),
      ],
    });

    const view = taskView(model, "t_stale_dep");
    expect(view.lane).toBe("ready");
    expect(view.note ?? "").toContain("waiting_dependency");
    expect(view.note ?? "").toContain("complete");
  });

  it("treats an empty dependency list as complete and notes the stale status", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_no_deps",
          title: "No dependencies",
          status: "waiting_dependency",
        }),
      ],
    });

    const view = taskView(model, "t_no_deps");
    expect(view.lane).toBe("ready");
    expect(view.note ?? "").toContain("waiting_dependency");
  });
});

describe("unknown and foreign task states", () => {
  it("keeps an unknown task status visible in Needs attention", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_foreign",
          title: "Foreign status",
          status: "paused_by_operator",
        }),
      ],
    });

    const view = taskView(model, "t_foreign");
    expect(view.lane).toBe("attention");
    expect(view.rawStatus).toBe("paused_by_operator");
    expect(view.knownStatus).toBeNull();
    expect(view.note ?? "").toContain("paused_by_operator");
    expect(model.counts.attention).toBe(1);
  });

  it("keeps an unknown status even when the task also has a rejected gate", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_foreign_rejected",
          title: "Foreign rejected",
          status: "paused",
          approvalGateId: "ap_rejected",
        }),
      ],
      approvals: [
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    const view = taskView(model, "t_foreign_rejected");
    expect(view.lane).toBe("attention");
    expect(view.rawStatus).toBe("paused");
  });
});

describe("lane invariants", () => {
  it("places every task exactly once across the six categories", () => {
    const tasks: WorkflowTaskInput[] = [
      makeTask({ id: "t_completed", title: "A completed", status: "completed" }),
      makeTask({ id: "t_failed", title: "B failed", status: "failed" }),
      makeTask({ id: "t_working", title: "C working", status: "working" }),
      makeTask({
        id: "t_waiting_user",
        title: "D waiting user",
        approvalGateId: "ap_pending",
      }),
      makeTask({ id: "t_ready", title: "E ready" }),
      makeTask({
        id: "t_waiting_dep",
        title: "F waiting dependency",
        dependsOn: ["t_open_dep"],
      }),
      makeTask({ id: "t_open_dep", title: "G open dependency" }),
      makeTask({
        id: "t_rejected",
        title: "H rejected",
        approvalGateId: "ap_rejected",
      }),
      makeTask({ id: "t_unknown", title: "I unknown", status: "paused" }),
    ];

    const model = build({
      tasks,
      approvals: [
        makeApproval({ id: "ap_pending", status: "pending" }),
        makeApproval({ id: "ap_rejected", status: "rejected" }),
      ],
    });

    const flattened = model.lanes.flatMap((lane) => lane.tasks);
    expect(flattened).toHaveLength(tasks.length);
    expect(new Set(flattened.map((view) => view.task.id)).size).toBe(
      tasks.length,
    );
    expect(flattened.map((view) => view.task.id).sort()).toEqual(
      tasks.map((task) => task.id).sort(),
    );

    for (const lane of model.lanes) {
      for (const view of lane.tasks) {
        expect(view.lane).toBe(lane.key);
      }
    }

    const counted =
      model.counts.running +
      model.counts.waitingDependency +
      model.counts.waitingUser +
      model.counts.completed +
      model.counts.ready +
      model.counts.attention;
    expect(counted).toBe(model.counts.total);
  });

  it("ignores tasks belonging to another project", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_mine", title: "Mine" }),
        makeTask({
          id: "t_other",
          title: "Other",
          projectId: "proj_other",
        }),
      ],
    });

    expect(model.tasks.map((view) => view.task.id)).toEqual(["t_mine"]);
    expect(model.counts.total).toBe(1);
  });

  it("keeps duplicate task ids visible instead of silently dropping one", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_dup", title: "First", status: "working" }),
        makeTask({ id: "t_dup", title: "Second", status: "failed" }),
      ],
    });

    expect(model.tasks).toHaveLength(2);
    expect(model.tasks.map((view) => view.task.title)).toEqual([
      "First",
      "Second",
    ]);
    expect(new Set(model.tasks.map((view) => view.viewKey)).size).toBe(2);
  });

  it("keeps a task view key stable when its lane changes", () => {
    const base = makeTask({ id: "t_move", title: "Move" });
    const running = build({ tasks: [{ ...base, status: "working" }] });
    const completed = build({ tasks: [{ ...base, status: "completed" }] });

    expect(taskView(running, "t_move").lane).toBe("running");
    expect(taskView(completed, "t_move").lane).toBe("completed");
    expect(taskView(running, "t_move").viewKey).toBe(
      taskView(completed, "t_move").viewKey,
    );
  });

  it("keeps view keys independent of lane-local positions", () => {
    const tasks = [
      makeTask({ id: "t_a", title: "A", status: "working" }),
      makeTask({ id: "t_b", title: "B", status: "working" }),
    ];
    const before = build({ tasks });
    const after = build({
      tasks: [{ ...tasks[0], status: "completed" }, tasks[1]],
    });

    expect(taskView(before, "t_a").viewKey).toBe(taskView(after, "t_a").viewKey);
    expect(taskView(before, "t_b").viewKey).toBe(taskView(after, "t_b").viewKey);
    expect(taskView(before, "t_b").lane).toBe("running");
    expect(taskView(after, "t_b").lane).toBe("running");
  });

  it("resolves task owners without inventing agents", () => {
    const worker = makeAgent({
      id: "ag_worker",
      name: "Worker",
      role: "Engineer",
    });
    const model = build({
      tasks: [
        makeTask({ id: "t_owned", title: "Owned" }),
        makeTask({
          id: "t_unowned",
          title: "Unowned",
          ownerAgentId: "ag_missing",
        }),
      ],
      agents: [worker],
    });

    expect(taskView(model, "t_owned").owner?.id).toBe("ag_worker");
    expect(taskView(model, "t_unowned").owner).toBeNull();
  });
});
describe("team lead", () => {
  it.each(["Team Lead", "Lead", "Coordinator"])(
    "matches the anchored role %s inside the project roster",
    (role) => {
      const lead = makeAgent({ id: "ag_lead", name: "Atlas", role });
      const model = build({
        project: makeProject({ id: PROJECT_ID, teamIds: [lead.id] }),
        agents: [lead],
      });

      expect(model.teamLead.state).toBe("available");
      expect(model.teamLead.agent?.id).toBe("ag_lead");
    },
  );

  it("matches case-insensitively and tolerates surrounding whitespace", () => {
    const lead = makeAgent({
      id: "ag_lead",
      name: "Atlas",
      role: "  TEAM   LEAD  ",
    });
    const model = build({
      project: makeProject({ id: PROJECT_ID, teamIds: [lead.id] }),
      agents: [lead],
    });

    expect(model.teamLead.agent?.id).toBe("ag_lead");
  });

  it("does not match an unrelated role such as Lead Generation Specialist", () => {
    const specialist = makeAgent({
      id: "ag_lead_gen",
      name: "Specialist",
      role: "Lead Generation Specialist",
    });
    const model = build({
      project: makeProject({ id: PROJECT_ID, teamIds: [specialist.id] }),
      agents: [specialist],
    });

    expect(model.teamLead.state).toBe("unavailable");
    expect(model.teamLead.agent).toBeNull();
    expect(model.teamLead.note).toContain("No Team Lead");
  });

  it("never promotes an agent outside project.teamIds", () => {
    const outsider = makeAgent({
      id: "ag_outsider",
      name: "Outsider",
      role: "Team Lead",
    });
    const member = makeAgent({
      id: "ag_worker",
      name: "Worker",
      role: "Engineer",
    });
    const model = build({
      project: makeProject({
        id: PROJECT_ID,
        teamIds: ["ag_worker"],
      }),
      agents: [outsider, member],
    });

    expect(model.teamLead.state).toBe("unavailable");
    expect(model.teamLead.agent).toBeNull();
    expect(model.teamLead.note).toContain("project's roster");
  });

  it("prefers Team Lead over Lead over Coordinator deterministically", () => {
    const coordinator = makeAgent({
      id: "ag_coord",
      name: "Coordinator",
      role: "Coordinator",
    });
    const lead = makeAgent({ id: "ag_lead", name: "Lead", role: "Lead" });
    const teamLead = makeAgent({
      id: "ag_team_lead",
      name: "Team Lead",
      role: "Team Lead",
    });

    const model = build({
      project: makeProject({
        id: PROJECT_ID,
        teamIds: [coordinator.id, lead.id, teamLead.id],
      }),
      agents: [coordinator, teamLead, lead],
    });

    expect(model.teamLead.agent?.id).toBe("ag_team_lead");
  });

  it("falls back to Lead when no Team Lead is in the roster", () => {
    const coordinator = makeAgent({
      id: "ag_coord",
      name: "Coordinator",
      role: "Coordinator",
    });
    const lead = makeAgent({ id: "ag_lead", name: "Lead", role: "Lead" });

    const model = build({
      project: makeProject({
        id: PROJECT_ID,
        teamIds: [coordinator.id, lead.id],
      }),
      agents: [coordinator, lead],
    });

    expect(model.teamLead.agent?.id).toBe("ag_lead");
  });
});

describe("goal and empty project handling", () => {
  it("returns a valid empty model when the project is missing", () => {
    const model = build({
      project: null,
      tasks: [makeTask({ id: "t_ignored", title: "Ignored" })],
      agents: [
        makeAgent({ id: "ag_lead", name: "Atlas", role: "Team Lead" }),
      ],
      approvals: [makeApproval({ id: "ap_1" })],
      events: [makeEvent("evt_1")],
    });

    expect(model.project).toBeNull();
    expect(model.goal.state).toBe("unavailable");
    expect(model.goal.projectId).toBeNull();
    expect(model.goal.href).toBeNull();
    expect(model.teamLead.state).toBe("unavailable");
    expect(model.teamLead.agent).toBeNull();
    expect(model.parallelWork.state).toBe("unavailable");
    expect(model.approvals.state).toBe("unavailable");
    expect(model.tasks).toEqual([]);
    expect(model.counts.total).toBe(0);
    expect(model.progress).toEqual({
      completed: 0,
      total: 0,
      ratio: 0,
      percent: 0,
    });
    expect(model.review.state).toBe("empty");
    expect(model.review.label).toBe("No tasks yet");
    expect(model.review.derived).toBe(false);
  });

  it("treats an undefined project like a missing project", () => {
    const model = build({ project: undefined });

    expect(model.project).toBeNull();
    expect(model.goal.state).toBe("unavailable");
    expect(model.review.state).toBe("empty");
  });

  it("projects the goal fields without changing them", () => {
    const project = makeProject({
      id: PROJECT_ID,
      title: "Refresh brand",
      goal: "Ship the refreshed brand",
      deadline: "2027-01-15",
      status: "planning",
    });
    const model = build({ project });

    expect(model.goal).toMatchObject({
      key: "goal",
      state: "available",
      projectId: PROJECT_ID,
      title: "Refresh brand",
      goal: "Ship the refreshed brand",
      deadline: "2027-01-15",
      status: "planning",
      href: `/projects/${PROJECT_ID}`,
      summary: "Ship the refreshed brand",
    });
  });

  it("exposes the stage spine in the approved order", () => {
    const model = build();

    expect(model.stages.map((stage) => stage.key)).toEqual([
      "goal",
      "team_lead",
      "parallel_work",
      "approvals",
      "review",
      "brain",
    ]);
    expect(model.brain.state).toBe("destination");
    expect(model.brain.href).toBe("/brain");
  });

  it("never reports an empty project as ready for review", () => {
    const model = build({
      project: makeProject({ id: PROJECT_ID, status: "in_progress" }),
      tasks: [],
    });

    expect(model.review.state).toBe("empty");
    expect(model.review.label).toBe("No tasks yet");
    expect(model.review.derived).toBe(false);
    expect(model.parallelWork.state).toBe("empty");
    expect(model.progress.ratio).toBe(0);
  });
});

describe("review derivation", () => {
  it("derives Ready for review when every task is complete", () => {
    const model = build({
      project: makeProject({ id: PROJECT_ID, status: "in_progress" }),
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "completed" }),
      ],
    });

    expect(model.review.state).toBe("ready");
    expect(model.review.label).toBe("Ready for review (derived)");
    expect(model.review.derived).toBe(true);
    expect(model.review.openTaskCount).toBe(0);
    expect(model.project?.status).toBe("in_progress");
  });

  it("derives Ready for review from planning when every task is complete", () => {
    const model = build({
      project: makeProject({ id: PROJECT_ID, status: "planning" }),
      tasks: [makeTask({ id: "t_1", title: "One", status: "completed" })],
    });

    expect(model.review.state).toBe("ready");
    expect(model.review.derived).toBe(true);
  });

  it("reports the server review status as In review (server)", () => {
    const model = build({
      project: makeProject({ id: PROJECT_ID, status: "review" }),
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "working" }),
      ],
    });

    expect(model.review.state).toBe("in_review");
    expect(model.review.label).toBe("In review (server)");
    expect(model.review.derived).toBe(false);
  });

  it("reports the server completed status as Completed", () => {
    const model = build({
      project: makeProject({ id: PROJECT_ID, status: "completed" }),
      tasks: [makeTask({ id: "t_1", title: "One", status: "completed" })],
    });

    expect(model.review.state).toBe("completed");
    expect(model.review.label).toBe("Completed");
    expect(model.review.derived).toBe(false);
  });

  it("counts open tasks when work remains", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "working" }),
        makeTask({ id: "t_3", title: "Three", status: "failed" }),
      ],
    });

    expect(model.review.state).toBe("open");
    expect(model.review.openTaskCount).toBe(2);
    expect(model.review.label).toBe("2 tasks still open");
  });

  it("uses singular wording for one open task", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "working" }),
      ],
    });

    expect(model.review.label).toBe("1 task still open");
  });
});

describe("progress", () => {
  it("derives progress from completed over total tasks", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "completed" }),
        makeTask({ id: "t_3", title: "Three", status: "working" }),
        makeTask({ id: "t_4", title: "Four", status: "failed" }),
      ],
    });

    expect(model.progress).toEqual({
      completed: 2,
      total: 4,
      ratio: 0.5,
      percent: 50,
    });
  });

  it("returns zero progress and does not divide by zero", () => {
    const model = build({ tasks: [] });

    expect(model.progress).toEqual({
      completed: 0,
      total: 0,
      ratio: 0,
      percent: 0,
    });
  });

  it("reaches full progress when all tasks are complete", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({ id: "t_2", title: "Two", status: "completed" }),
      ],
    });

    expect(model.progress.ratio).toBe(1);
    expect(model.progress.percent).toBe(100);
  });
});
describe("approvals", () => {
  it("filters approvals strictly by project id", () => {
    const model = build({
      approvals: [
        makeApproval({ id: "ap_pending", title: "B pending" }),
        makeApproval({
          id: "ap_approved",
          title: "A approved",
          status: "approved",
        }),
        makeApproval({
          id: "ap_other",
          title: "Other project",
          projectId: "proj_other",
        }),
      ],
    });

    expect(model.approvals.pending.map((item) => item.id)).toEqual([
      "ap_pending",
    ]);
    expect(model.approvals.decided.map((item) => item.id)).toEqual([
      "ap_approved",
    ]);
    expect(model.approvals.pendingCount).toBe(1);
    expect(model.approvals.decidedCount).toBe(1);
    expect(model.approvals.state).toBe("available");
  });

  it("never lets another project's approval affect task classification", () => {
    const model = build({
      tasks: [
        makeTask({
          id: "t_gated",
          title: "Gated",
          status: "waiting_approval",
          approvalGateId: "ap_other_pending",
        }),
      ],
      approvals: [
        makeApproval({
          id: "ap_other_pending",
          projectId: "proj_other",
          status: "pending",
        }),
      ],
    });

    const view = taskView(model, "t_gated");
    expect(view.lane).toBe("attention");
    expect(view.gateState).toBe("missing");
    expect(view.gate).toBeNull();
    expect(model.approvals.pending).toEqual([]);
    expect(model.approvals.state).toBe("empty");
  });

  it("honours the approval.gatesTaskId link when the task has no direct gate id", () => {
    const model = build({
      tasks: [makeTask({ id: "t_linked", title: "Linked" })],
      approvals: [
        makeApproval({
          id: "ap_linked",
          status: "pending",
          gatesTaskId: "t_linked",
        }),
      ],
    });

    const view = taskView(model, "t_linked");
    expect(view.gateState).toBe("pending");
    expect(view.gate?.id).toBe("ap_linked");
    expect(view.lane).toBe("waiting_user");
  });

  it("does not use another project's gatesTaskId link", () => {
    const model = build({
      tasks: [makeTask({ id: "t_linked", title: "Linked" })],
      approvals: [
        makeApproval({
          id: "ap_other_linked",
          projectId: "proj_other",
          status: "pending",
          gatesTaskId: "t_linked",
        }),
      ],
    });

    const view = taskView(model, "t_linked");
    expect(view.gateState).toBe("none");
    expect(view.lane).toBe("ready");
  });

  it("accepts explicit partial provenance for a caller-supplied LIVE page", () => {
    const model = build({
      mode: "live",
      approvalsCompleteness: "partial",
    });

    expect(model.limitations.join(" ")).toContain(
      "completeness is not verifiable",
    );
    expect(model.approvals.completeness).toBe("partial");
    expect(model.approvals.completenessNote).toContain(
      "project-wide total is not confirmed",
    );
  });

  it("accepts explicit complete provenance for the full DEMO dataset", () => {
    const model = build({
      mode: "demo",
      approvalsCompleteness: "complete",
    });

    expect(model.approvals.completeness).toBe("complete");
    expect(model.approvals.completenessNote).toBe(
      "Complete local dataset confirmed by the caller for this project.",
    );
  });

  it("accepts explicit partial provenance for a DEMO subset", () => {
    const model = build({
      mode: "demo",
      approvalsCompleteness: "partial",
      approvals: [makeApproval({ id: "ap_subset" })],
    });

    expect(model.approvals.completeness).toBe("partial");
    expect(model.approvals.completeness).not.toBe("complete");
    expect(model.approvals.completenessNote).toContain(
      "project-wide total is not confirmed",
    );
    expect(model.approvals.completenessNote).not.toContain(
      "Complete local dataset confirmed",
    );
  });

  it("defaults omitted completeness to partial even in DEMO mode", () => {
    const model = build({ mode: "demo" });

    expect(model.approvals.completeness).toBe("partial");
    expect(model.approvals.completenessNote).toContain(
      "project-wide total is not confirmed",
    );
  });

  it("keeps approvals unavailable when no project is selected", () => {
    const model = build({
      project: null,
      mode: "demo",
      approvalsCompleteness: "complete",
    });

    expect(model.approvals.state).toBe("unavailable");
    expect(model.approvals.completeness).toBe("unavailable");
    expect(model.approvals.completenessNote).toBe("No project selected.");
  });
});

describe("activity", () => {
  it("preserves supplied order and caps the preview at eight entries", () => {
    const events = Array.from({ length: 10 }, (_, index) =>
      makeEvent(`evt_${index}`),
    );
    const model = build({ events });

    expect(model.activity.providedCount).toBe(10);
    expect(model.activity.preview).toHaveLength(8);
    expect(model.activity.preview.map((event) => event.id)).toEqual([
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

  it("does not re-sort supplied events by their timestamps", () => {
    const events = [
      makeEvent("evt_late", "2026-05-01T12:00:00Z"),
      makeEvent("evt_early", "2020-01-01T00:00:00Z"),
      makeEvent("evt_middle", "2023-01-01T00:00:00Z"),
    ];
    const model = build({ events });

    expect(model.activity.preview.map((event) => event.id)).toEqual([
      "evt_late",
      "evt_early",
      "evt_middle",
    ]);
  });

  it("marks demo activity as business-wide", () => {
    const model = build({
      mode: "demo",
      activityScope: "business",
      events: [makeEvent("evt_demo")],
    });

    expect(model.activity.scope).toBe("business");
    expect(model.activity.scopedToProject).toBe(false);
    expect(model.activity.label).toBe("Business activity (demo)");
    expect(model.activity.note).toContain("business-wide");
    expect(model.limitations.join(" ")).toContain("business-wide");
  });

  it("normalizes a contradictory demo project scope to business-wide", () => {
    const model = build({
      mode: "demo",
      activityScope: "project",
      events: [makeEvent("evt_demo")],
    });

    expect(model.activity.scope).toBe("business");
    expect(model.activity.scopedToProject).toBe(false);
    expect(model.activity.label).toBe("Business activity (demo)");
    expect(model.activity.note).toContain("business-wide");
    expect(model.activity.note).not.toContain("Project-scoped");
    expect(model.limitations.join(" ")).toContain("business-wide");
  });

  it("marks live activity as project-scoped when the caller supplies project scope", () => {
    const model = build({
      mode: "live",
      activityScope: "project",
      events: [makeEvent("evt_live")],
    });

    expect(model.activity.scope).toBe("project");
    expect(model.activity.scopedToProject).toBe(true);
    expect(model.activity.label).toBe("Project activity");
  });

  it("does not claim business-scoped live activity is project-specific", () => {
    const model = build({
      mode: "live",
      activityScope: "business",
      events: [makeEvent("evt_live")],
    });

    expect(model.activity.scopedToProject).toBe(false);
    expect(model.activity.label).toBe("Business activity");
    expect(model.activity.note).toContain("not project-scoped");
    expect(model.limitations.join(" ")).toContain("not project-scoped");
  });
});

describe("demo and live parity", () => {
  it("derives identical task structure for the same domain inputs", () => {
    const shared = {
      project: PROJECT,
      tasks: [
        makeTask({ id: "t_1", title: "One", status: "completed" }),
        makeTask({
          id: "t_2",
          title: "Two",
          status: "waiting_approval",
          approvalGateId: "ap_pending",
        }),
        makeTask({
          id: "t_3",
          title: "Three",
          status: "waiting_dependency",
          dependsOn: ["t_1"],
        }),
      ],
      agents: [
        makeAgent({ id: "ag_lead", name: "Atlas", role: "Team Lead" }),
      ],
      approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
      events: [makeEvent("evt_1")],
    };

    const demo = build({
      ...shared,
      mode: "demo",
      activityScope: "business",
      approvalsCompleteness: "complete",
    });
    const live = build({ ...shared, mode: "live", activityScope: "project" });

    expect(demo.tasks).toEqual(live.tasks);
    expect(demo.lanes).toEqual(live.lanes);
    expect(demo.counts).toEqual(live.counts);
    expect(demo.progress).toEqual(live.progress);
    expect(demo.review).toEqual(live.review);
    expect(demo.goal).toEqual(live.goal);
    expect(demo.teamLead).toEqual(live.teamLead);
    expect(demo.approvals.pending).toEqual(live.approvals.pending);
    expect(demo.approvals.decided).toEqual(live.approvals.decided);
    expect(demo.approvals.unknown).toEqual(live.approvals.unknown);
    expect(demo.approvals.pendingCount).toBe(live.approvals.pendingCount);
    expect(demo.approvals.decidedCount).toBe(live.approvals.decidedCount);
    expect(demo.approvals.completeness).toBe("complete");
    expect(live.approvals.completeness).toBe("partial");
  });
});

describe("deterministic ordering", () => {
  it("sorts tasks by title and then id without changing lanes", () => {
    const model = build({
      tasks: [
        makeTask({ id: "t_beta", title: "Beta" }),
        makeTask({ id: "t_alpha_z", title: "Alpha" }),
        makeTask({ id: "t_alpha_a", title: "Alpha" }),
      ],
    });

    expect(model.tasks.map((view) => view.task.title)).toEqual([
      "Alpha",
      "Alpha",
      "Beta",
    ]);
    expect(model.tasks.map((view) => view.task.id)).toEqual([
      "t_alpha_a",
      "t_alpha_z",
      "t_beta",
    ]);
  });

  it("keeps the stable primary then secondary lane order", () => {
    const model = build();

    expect(laneKeys(model)).toEqual([...WORKFLOW_LANE_ORDER]);
    expect(model.lanes.slice(0, 4).every((lane) => lane.kind === "primary")).toBe(
      true,
    );
    expect(
      model.lanes.slice(4).every((lane) => lane.kind === "secondary"),
    ).toBe(true);
  });
});

describe("input immutability", () => {
  it("leaves every supplied object and array unchanged", () => {
    const tasks = [
      makeTask({
        id: "t_2",
        title: "Two",
        dependsOn: ["t_3"],
        approvalGateId: "ap_pending",
      }),
      makeTask({ id: "t_1", title: "One", status: "completed" }),
      makeTask({ id: "t_3", title: "Three" }),
    ];
    const agents = [
      makeAgent({ id: "ag_lead", name: "Atlas", role: "Team Lead" }),
      makeAgent({ id: "ag_worker", name: "Worker", role: "Engineer" }),
    ];
    const approvals = [
      makeApproval({ id: "ap_pending", status: "pending" }),
      makeApproval({
        id: "ap_other",
        projectId: "proj_other",
        status: "approved",
      }),
    ];
    const events = [makeEvent("evt_2"), makeEvent("evt_1")];
    const project = makeProject({ id: PROJECT_ID });
    const before = JSON.stringify({
      project,
      tasks,
      agents,
      approvals,
      events,
    });

    build({ project, tasks, agents, approvals, events });

    expect(
      JSON.stringify({ project, tasks, agents, approvals, events }),
    ).toBe(before);
  });
});
