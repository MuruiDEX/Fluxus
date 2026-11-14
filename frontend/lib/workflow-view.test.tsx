import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { WorkflowView } from "@/components/workflow/WorkflowView";
import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
} from "@/lib/domain/types";
import {
  activity as seedActivity,
  agents as seedAgents,
  approvals as seedApprovals,
  project as seedProject,
  tasks as seedTasks,
} from "@/lib/data/seed-northstar";
import { buildWorkflowModel } from "@/lib/workflow";
import type {
  WorkflowInput,
  WorkflowModel,
  WorkflowTaskInput,
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

function makeEvent(id: string): ActivityEvent {
  return { id, at: "just now", agentName: "Atlas", text: `Event ${id}`, kind: "work" };
}

function build(overrides: Partial<WorkflowInput> = {}): WorkflowModel {
  return buildWorkflowModel({
    project: PROJECT,
    tasks: [],
    agents: [],
    approvals: [],
    events: [],
    mode: "demo",
    activityScope: "business",
    ...overrides,
  });
}

const html = (model: WorkflowModel) =>
  renderToStaticMarkup(<WorkflowView model={model} />);

const occurrences = (markup: string, needle: string) =>
  markup.split(needle).length - 1;

const taskViewKeys = (markup: string) =>
  Array.from(markup.matchAll(/data-task-view-key="([^"]+)"/g)).map(
    (match) => match[1],
  );

describe("WorkflowView spine", () => {
  it("renders the six stages in the approved order", () => {
    const markup = html(build());
    const stages = Array.from(markup.matchAll(/data-stage="([^"]+)"/g)).map(
      (match) => match[1],
    );

    expect(stages).toEqual([
      "goal",
      "team_lead",
      "parallel_work",
      "approvals",
      "review",
      "brain",
    ]);
    expect(markup).toContain(
      "Goal → Team Lead → Parallel work → Approvals → Review → Brain",
    );
    expect(markup).toContain('aria-label="Workflow stages from Goal to Brain"');
    expect(markup).toContain("Read-only office view");
  });

  it("shows an honest unavailable Team Lead instead of inventing one", () => {
    const worker = makeAgent({
      id: "ag_worker",
      name: "Worker",
      role: "Engineer",
    });
    const markup = html(
      build({
        project: makeProject({ id: PROJECT_ID, teamIds: [worker.id] }),
        agents: [worker],
      }),
    );

    expect(markup).toContain("Not assigned");
    expect(markup).toContain("No Team Lead role in this project");
    expect(markup).toContain("roster.");
    expect(markup).not.toContain("Matched project role");
    expect(markup).toContain('href="/agents"');
  });

  it("shows the matching project Team Lead without adding a second roster", () => {
    const lead = makeAgent({
      id: "ag_lead",
      name: "Atlas",
      role: "Team Lead",
    });
    const markup = html(build({ agents: [lead] }));

    expect(markup).toContain("Atlas");
    expect(markup).toContain("Matched project role");
    expect(markup).not.toContain("Standing by");
  });
});

describe("WorkflowView lanes", () => {
  it("always renders the four primary lanes and hides conditional strips when empty", () => {
    const markup = html(build({ tasks: [] }));

    expect(occurrences(markup, "data-lane=")).toBe(4);
    expect(markup).toContain('data-lane="running"');
    expect(markup).toContain('data-lane="waiting_dependency"');
    expect(markup).toContain('data-lane="waiting_user"');
    expect(markup).toContain('data-lane="completed"');
    expect(markup).not.toContain('data-lane="ready"');
    expect(markup).not.toContain('data-lane="attention"');
    expect(markup).not.toContain("Ready for review (derived)");
  });

  it("renders conditional strips only when they contain tasks", () => {
    const markup = html(
      build({
        tasks: [
          makeTask({ id: "t_ready", title: "Ready task" }),
          makeTask({ id: "t_failed", title: "Failed task", status: "failed" }),
        ],
      }),
    );

    expect(markup).toContain('data-lane="ready"');
    expect(markup).toContain('data-lane="attention"');
    expect(markup).toContain("Ready task");
    expect(markup).toContain("Failed task");
  });

  it("renders every task exactly once across the six lanes", () => {
    const tasks = [
      makeTask({ id: "t_running", title: "Running task", status: "working" }),
      makeTask({
        id: "t_blocked",
        title: "Blocked task",
        status: "waiting_dependency",
        dependsOn: ["t_missing"],
      }),
      makeTask({
        id: "t_waiting_user",
        title: "Waiting on you task",
        approvalGateId: "ap_pending",
      }),
      makeTask({ id: "t_completed", title: "Completed task", status: "completed" }),
      makeTask({ id: "t_ready", title: "Ready task" }),
      makeTask({ id: "t_failed", title: "Failed task", status: "failed" }),
    ];
    const markup = html(
      build({
        tasks,
        approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
      }),
    );

    for (const task of tasks) {
      expect(occurrences(markup, `data-task-id="${task.id}"`)).toBe(1);
    }
    expect(occurrences(markup, "data-task-id=")).toBe(tasks.length);
    expect(new Set(taskViewKeys(markup)).size).toBe(tasks.length);
  });

  it("renders duplicate task ids as separate cards with distinct identities", () => {
    const markup = html(
      build({
        tasks: [
          makeTask({ id: "t_dup", title: "First", status: "working" }),
          makeTask({ id: "t_dup", title: "Second", status: "failed" }),
        ],
      }),
    );
    const keys = taskViewKeys(markup);

    expect(occurrences(markup, 'data-task-id="t_dup"')).toBe(2);
    expect(markup).toContain("First");
    expect(markup).toContain("Second");
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(2);
  });

  it("retains a task's rendered identity when it moves lanes", () => {
    const base = makeTask({ id: "t_move", title: "Move" });
    const running = html(build({ tasks: [{ ...base, status: "working" }] }));
    const completed = html(build({ tasks: [{ ...base, status: "completed" }] }));

    expect(occurrences(running, 'data-lane="running"')).toBe(1);
    expect(occurrences(completed, 'data-lane="completed"')).toBe(1);
    expect(taskViewKeys(running)).toEqual(taskViewKeys(completed));
  });

  it("uses progressive disclosure for dependencies and gates without task controls", () => {
    const markup = html(
      build({
        tasks: [
          makeTask({
            id: "t_gated",
            title: "Gated task",
            status: "waiting_dependency",
            dependsOn: ["t_missing"],
            approvalGateId: "ap_pending",
          }),
        ],
        approvals: [makeApproval({ id: "ap_pending", status: "pending" })],
      }),
    );

    expect(markup).toContain("<details");
    expect(markup).toContain("<summary");
    expect(markup).toContain("t_missing (not loaded)");
    expect(markup).toContain("Approval pending");
    expect(markup).not.toContain("<button");
    expect(markup).not.toContain("Run task");
  });

  it("renders the real DEMO dataset with each of its seven tasks exactly once", () => {
    const model = build({
      project: seedProject,
      tasks: seedTasks,
      agents: seedAgents,
      approvals: seedApprovals,
      events: seedActivity,
      mode: "demo",
      activityScope: "business",
      approvalsCompleteness: "complete",
    });
    const markup = html(model);

    for (const task of seedTasks) {
      expect(occurrences(markup, `data-task-id="${task.id}"`)).toBe(1);
    }
    expect(markup).toContain("Atlas");
    expect(markup).toContain("Business activity (demo)");
  });
});

describe("WorkflowView review and empty states", () => {
  it("distinguishes server-reported review from derived readiness", () => {
    const server = html(
      build({
        project: makeProject({ id: PROJECT_ID, status: "review" }),
        tasks: [makeTask({ id: "t_open", title: "Open", status: "working" })],
      }),
    );
    expect(server).toContain("In review (server)");
    expect(server).toContain("Server-reported");
    expect(server).not.toContain("Derived readiness");

    const derived = html(
      build({
        project: makeProject({ id: PROJECT_ID, status: "in_progress" }),
        tasks: [
          makeTask({ id: "t_done_a", title: "Done A", status: "completed" }),
          makeTask({ id: "t_done_b", title: "Done B", status: "completed" }),
        ],
      }),
    );
    expect(derived).toContain("Ready for review (derived)");
    expect(derived).toContain("Derived readiness");
    expect(derived).toContain("server status was not changed");
    expect(derived).not.toContain("Open review");
    expect(occurrences(derived, "Open project")).toBe(3);
  });

  it("renders an honest empty project without a task board or review claim", () => {
    const markup = html(
      build({
        project: null,
        tasks: [makeTask({ id: "t_ignored", title: "Ignored" })],
        approvals: [makeApproval({ id: "ap_ignored" })],
      }),
    );

    expect(markup).toContain("No active project");
    expect(markup).not.toContain("data-lane=");
    expect(markup).toContain("No tasks yet");
    expect(markup).not.toContain("Ready for review (derived)");
    expect(markup).toContain('href="/brain"');
  });
});

describe("WorkflowView provenance", () => {
  it("normalizes contradictory demo project activity to a business-wide label", () => {
    const model = build({
      mode: "demo",
      activityScope: "project",
      events: [makeEvent("evt_demo")],
    });
    const markup = html(model);

    expect(model.activity.scope).toBe("business");
    expect(markup).toContain('data-activity-scope="business"');
    expect(markup).toContain("Business activity (demo)");
    expect(markup).toContain("business-wide");
    expect(markup).not.toContain("Project activity");
  });

  it("uses caller-supplied complete provenance for the full demo dataset", () => {
    const pending = makeApproval({ id: "ap_pending", status: "pending" });
    const demo = html(
      build({
        mode: "demo",
        approvals: [pending],
        approvalsCompleteness: "complete",
      }),
    );
    expect(demo).toContain('data-approval-completeness="complete"');
    expect(demo).toContain(
      "Complete local dataset confirmed by the caller for this project.",
    );
    expect(demo).not.toContain("project-wide total is not confirmed");
    expect(demo).toContain("1 pending");
    expect(demo).not.toContain("Loaded page: 1 pending");
  });

  it("keeps a partial LIVE page conservative", () => {
    const pending = makeApproval({ id: "ap_pending", status: "pending" });
    const live = html(
      build({
        mode: "live",
        activityScope: "project",
        approvals: [pending],
        approvalsCompleteness: "partial",
      }),
    );
    expect(live).toContain('data-approval-completeness="partial"');
    expect(live).toContain(
      "Counts reflect only the supplied dataset or page; a project-wide total is not confirmed.",
    );
    expect(live).toContain("Loaded page: 1 pending");
    expect(live).toContain("Loaded: 1 pending");
    expect(live).not.toContain("1 pending approval for this project");
  });

  it("never labels a partial DEMO subset as complete", () => {
    const pending = makeApproval({ id: "ap_pending", status: "pending" });
    const markup = html(
      build({
        mode: "demo",
        approvals: [pending],
        approvalsCompleteness: "partial",
      }),
    );

    expect(markup).toContain('data-approval-completeness="partial"');
    expect(markup).toContain(
      "Counts reflect only the supplied dataset or page; a project-wide total is not confirmed.",
    );
    expect(markup).not.toContain('data-approval-completeness="complete"');
    expect(markup).not.toContain(
      "Complete local dataset confirmed by the caller for this project.",
    );
  });

  it("defaults omitted completeness to partial in DEMO mode", () => {
    const markup = html(
      build({ mode: "demo", approvals: [makeApproval({ id: "ap_pending" })] }),
    );

    expect(markup).toContain('data-approval-completeness="partial"');
    expect(markup).not.toContain('data-approval-completeness="complete"');
    expect(markup).toContain(
      "Counts reflect only the supplied dataset or page; a project-wide total is not confirmed.",
    );
  });

  it("keeps activity to a compact summary instead of the old full feed", () => {
    const events = Array.from({ length: 6 }, (_, index) =>
      makeEvent(`evt_${index}`),
    );
    const markup = html(build({ events }));

    expect(markup).toContain("Showing 4 of 6 supplied events.");
    expect(markup).toContain("Event evt_0");
    expect(markup).not.toContain("Event evt_4");
  });

  it("treats Brain as a destination only, never a completed handoff", () => {
    const markup = html(build());

    expect(markup).toContain('href="/brain"');
    expect(markup).toContain("Open Business Brain");
    expect(markup).toContain(
      "No report or Brain handoff is recorded by this view.",
    );
    expect(markup).not.toContain("report received");
    expect(markup).not.toContain("handoff complete");
  });

  it("keeps approval, activity, and limits as peer h2 sections", () => {
    const markup = html(
      build({
        tasks: [makeTask({ id: "t_one", title: "One" })],
        approvals: [makeApproval({ id: "ap_pending" })],
      }),
    );
    const headings = Array.from(
      markup.matchAll(/<h2[^>]*>(.*?)<\/h2>/g),
      (match) => match[1],
    );

    expect(headings).toEqual([
      "Workflow",
      "Parallel work",
      "Approval queue",
      "Recent activity",
      "Known limits",
    ]);
  });

  it("keeps responsive and accessibility structure without motion loops", () => {
    const markup = html(
      build({
        tasks: [makeTask({ id: "t_one", title: "One" })],
      }),
    );

    expect(markup).toContain("<ol");
    expect(markup).toContain("lg:flex-row");
    expect(markup).toContain("lg:overflow-x-auto");
    expect(markup).toContain("<details");
    expect(markup).toContain("<summary");
    expect(markup).not.toContain("animate-");
    expect(markup).not.toContain("<button");
  });
});