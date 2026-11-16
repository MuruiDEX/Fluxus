import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { LiveOffice } from "@/components/live/LiveOffice";
import type {
  ActivityEvent,
  Agent,
  Approval,
  Project,
  Task,
} from "@/lib/domain/types";

type SourceStatus = "idle" | "loading" | "ready" | "error";

const store = vi.hoisted(() => ({
  current: {
    projects: {
      status: "idle" as SourceStatus,
      businessId: null as string | null,
      projects: [] as Project[],
      total: 0,
      error: null as string | null,
    },
    agents: {
      status: "idle" as SourceStatus,
      businessId: null as string | null,
      agents: [] as Agent[],
      total: 0,
      error: null as string | null,
    },
    approvals: {
      status: "idle" as SourceStatus,
      businessId: null as string | null,
      approvals: [] as Approval[],
      total: 0,
      error: null as string | null,
    },
    activity: {
      status: "idle" as SourceStatus,
      businessId: null as string | null,
      projectId: null as string | null,
      events: [] as ActivityEvent[],
      total: 0,
      error: null as string | null,
    },
    tasks: {
      status: "idle" as SourceStatus,
      key: null as string | null,
      tasks: [] as Task[],
      error: null as string | null,
    },
  },
}));

vi.mock("@/lib/store/live", () => ({
  useLiveProjects: (
    selector: (value: typeof store.current.projects) => unknown,
  ) => selector(store.current.projects),
  useLiveAgentsList: (
    selector: (value: typeof store.current.agents) => unknown,
  ) => selector(store.current.agents),
  useLiveApprovals: (
    selector: (value: typeof store.current.approvals) => unknown,
  ) => selector(store.current.approvals),
  useLiveActivity: (
    selector: (value: typeof store.current.activity) => unknown,
  ) => selector(store.current.activity),
  useLiveTasks: (
    selector: (value: typeof store.current.tasks) => unknown,
  ) => selector(store.current.tasks),
}));

const BIZ = "biz_live";
const BIZ_B = "biz_other";
const PROJECT_A = "proj_a";
const PROJECT_B = "proj_b";

function makeProject(
  id: string,
  overrides: Partial<Project> = {},
): Project {
  return {
    id,
    businessId: BIZ,
    title: `Project ${id}`,
    goal: `Goal for ${id}`,
    status: "in_progress",
    progress: 0,
    budget: 10_000,
    aiBudget: 1_000,
    aiSpent: 100,
    deadline: "2026-12-31",
    teamIds: [],
    milestones: [],
    ...overrides,
  };
}

function makeTask(
  id: string,
  projectId: string,
  overrides: Partial<Task> = {},
): Task {
  return {
    id,
    businessId: BIZ,
    projectId,
    title: `Task ${id}`,
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

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "ap_1",
    businessId: BIZ,
    projectId: PROJECT_A,
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

const AGENT: Agent = {
  id: "ag_worker",
  businessId: BIZ,
  name: "Worker",
  role: "Engineer",
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
};

function seedLive({
  projects = [makeProject(PROJECT_A)],
  projectsStatus = "ready" as const,
  projectsBusinessId = BIZ,
  projectsError = null,
  tasks = [makeTask("task_a", PROJECT_A)],
  tasksKey = `${BIZ}/${PROJECT_A}`,
  tasksStatus = "ready" as const,
  tasksError = null,
  approvals = [] as Approval[],
  approvalsStatus = "ready" as const,
  approvalsError = null,
  agentsStatus = "ready" as const,
  agentsBusinessId = BIZ,
  agentsError = null,
  activityStatus = "ready" as const,
  activityError = null,
}: {
  projects?: Project[];
  projectsStatus?: "idle" | "loading" | "ready" | "error";
  projectsBusinessId?: string | null;
  projectsError?: string | null;
  tasks?: Task[];
  tasksKey?: string | null;
  tasksStatus?: "idle" | "loading" | "ready" | "error";
  tasksError?: string | null;
  approvals?: Approval[];
  approvalsStatus?: "idle" | "loading" | "ready" | "error";
  approvalsError?: string | null;
  agentsStatus?: "idle" | "loading" | "ready" | "error";
  agentsBusinessId?: string | null;
  agentsError?: string | null;
  activityStatus?: "idle" | "loading" | "ready" | "error";
  activityError?: string | null;
} = {}) {
  store.current.projects = {
    status: projectsStatus,
    businessId: projectsBusinessId,
    projects,
    total: projects.length,
    error: projectsError,
  };
  store.current.agents = {
    status: agentsStatus,
    businessId: agentsBusinessId,
    agents: [AGENT],
    total: 1,
    error: agentsError,
  };
  store.current.approvals = {
    status: approvalsStatus,
    businessId: BIZ,
    approvals,
    total: approvals.length,
    error: approvalsError,
  };
  store.current.activity = {
    status: activityStatus,
    businessId: BIZ,
    projectId: null,
    events: [],
    total: 0,
    error: activityError,
  };
  store.current.tasks = {
    status: tasksStatus,
    key: tasksKey,
    tasks,
    error: tasksError,
  };
}

function renderOffice(businessId = BIZ): string {
  return renderToStaticMarkup(<LiveOffice businessId={businessId} />);
}

beforeEach(() => {
  store.current.projects = {
    status: "idle",
    businessId: null,
    projects: [],
    total: 0,
    error: null,
  };
  store.current.agents = {
    status: "idle",
    businessId: null,
    agents: [],
    total: 0,
    error: null,
  };
  store.current.approvals = {
    status: "idle",
    businessId: null,
    approvals: [],
    total: 0,
    error: null,
  };
  store.current.activity = {
    status: "idle",
    businessId: null,
    projectId: null,
    events: [],
    total: 0,
    error: null,
  };
  store.current.tasks = {
    status: "idle",
    key: null,
    tasks: [],
    error: null,
  };
});

describe("LiveOffice integration", () => {
  it("renders loading without a stale workflow", () => {
    seedLive({ projectsStatus: "loading", projects: [] });
    const markup = renderOffice();

    expect(markup).toContain("Loading AI office");
    expect(markup).not.toContain("data-stage=");
  });

  it("renders project and task failures with their matching retry", () => {
    seedLive({
      projectsStatus: "error",
      projectsError: "Projects failed.",
      projects: [],
    });
    let markup = renderOffice();
    expect(markup).toContain("Projects failed.");
    expect(markup).toContain("Retry projects");

    seedLive({
      tasksStatus: "error",
      tasksError: "Tasks failed.",
    });
    markup = renderOffice();
    expect(markup).toContain("Tasks failed.");
    expect(markup).toContain("Retry tasks");
  });

  it("renders an honest no-project state", () => {
    seedLive({ projects: [], tasks: [], tasksKey: null });
    const markup = renderOffice();

    expect(markup).toContain("No project available");
    expect(markup).toContain('href="/projects"');
    expect(markup).not.toContain("data-stage=");
  });

  it("renders partial approval absence as unverified and not Ready", () => {
    seedLive();
    const markup = renderOffice();

    expect(markup).toContain("Gate unverified");
    expect(markup).toContain("unverified, not gate-free");
    expect(markup).not.toContain("Gate missing");
    expect(markup).not.toContain('data-lane="ready"');
  });

  it("uses a discovered approved gate but still applies task precedence", () => {
    seedLive({
      approvals: [makeApproval({ id: "ap_ok", status: "approved" })],
      tasks: [
        makeTask("task_ready", PROJECT_A, { approvalGateId: "ap_ok" }),
        makeTask("task_working", PROJECT_A, {
          status: "working",
          approvalGateId: "ap_ok",
        }),
      ],
    });
    const markup = renderOffice();

    expect(markup).toContain("Approval approved");
    expect(markup).toContain('data-lane="ready"');
    expect(markup).toContain('data-task-id="task_ready"');
    expect(markup).toContain('data-lane="running"');
    expect(markup).toContain('data-task-id="task_working"');
  });

  it("keeps approvals failure nonblocking and ignores retained records", () => {
    seedLive({
      approvalsStatus: "error",
      approvalsError: "Approvals failed.",
      approvals: [makeApproval({ id: "ap_stale", status: "approved" })],
      tasks: [
        makeTask("task_stale", PROJECT_A, {
          approvalGateId: "ap_stale",
        }),
      ],
    });
    const markup = renderOffice();

    expect(markup).toContain("Approvals unavailable");
    expect(markup).toContain("Retry approvals");
    expect(markup).toContain("Gate unverified");
    expect(markup).not.toContain("Approval approved");
    expect(markup).not.toContain('data-lane="ready"');
    expect(markup).toContain("data-stage=");
  });

  it("suppresses the old task key while the project selection changes", () => {
    const projectB = makeProject(PROJECT_B);
    seedLive();
    let markup = renderOffice();
    expect(markup).toContain("Goal for proj_a");
    expect(markup).toContain('data-task-id="task_a"');

    store.current.projects = {
      status: "ready",
      businessId: BIZ,
      projects: [
        makeProject(PROJECT_A, { status: "completed" }),
        projectB,
      ],
      total: 2,
      error: null,
    };
    markup = renderOffice();

    expect(markup).toContain("Loading AI office");
    expect(markup).not.toContain('data-task-id="task_a"');
    expect(markup).not.toContain("Goal for proj_b");

    store.current.tasks = {
      status: "ready",
      key: `${BIZ}/${PROJECT_B}`,
      tasks: [makeTask("task_b", PROJECT_B)],
      error: null,
    };
    markup = renderOffice();

    expect(markup).toContain("Goal for proj_b");
    expect(markup).toContain('data-task-id="task_b"');
    expect(markup).not.toContain('data-task-id="task_a"');
  });

  it("isolates a business transition until the new business is proven", () => {
    const projectB = makeProject(PROJECT_B, { businessId: BIZ_B });

    seedLive({
      projects: [makeProject(PROJECT_A)],
      projectsBusinessId: BIZ,
      tasks: [makeTask("task_a", PROJECT_A)],
      tasksKey: `${BIZ}/${PROJECT_A}`,
      agentsStatus: "ready",
      agentsBusinessId: BIZ,
    });
    let markup = renderOffice(BIZ_B);

    expect(markup).not.toContain("Goal for proj_a");
    expect(markup).not.toContain('data-task-id="task_a"');
    expect(markup).not.toContain("1 AI employee");
    expect(markup).toContain("Loading AI team");

    seedLive({
      projects: [],
      projectsStatus: "loading",
      projectsBusinessId: BIZ_B,
      tasks: [],
      tasksKey: null,
      agentsStatus: "loading",
      agentsBusinessId: BIZ_B,
    });
    markup = renderOffice(BIZ_B);

    expect(markup).toContain("Loading AI office");
    expect(markup).not.toContain('data-task-id="task_a"');

    seedLive({
      projects: [projectB],
      projectsBusinessId: BIZ_B,
      tasks: [makeTask("task_a", PROJECT_A)],
      tasksKey: `${BIZ}/${PROJECT_A}`,
      agentsStatus: "ready",
      agentsBusinessId: BIZ_B,
    });
    markup = renderOffice(BIZ_B);

    expect(markup).toContain("Loading AI office");
    expect(markup).not.toContain('data-task-id="task_a"');
    expect(markup).not.toContain("Goal for proj_b");

    seedLive({
      projects: [projectB],
      projectsBusinessId: BIZ_B,
      tasks: [makeTask("task_b", PROJECT_B, { businessId: BIZ_B })],
      tasksKey: `${BIZ_B}/${PROJECT_B}`,
      agentsStatus: "ready",
      agentsBusinessId: BIZ_B,
    });
    markup = renderOffice(BIZ_B);

    expect(markup).toContain("Goal for proj_b");
    expect(markup).toContain('data-task-id="task_b"');
    expect(markup).toContain("1 AI employee");
    expect(markup).not.toContain('data-task-id="task_a"');
  });

  it("keeps activity scope unknown while loading and business after success", () => {
    seedLive({ activityStatus: "loading" });
    let markup = renderOffice();
    expect(markup).toContain('data-activity-scope="unknown"');

    seedLive({ activityStatus: "ready" });
    markup = renderOffice();
    expect(markup).toContain('data-activity-scope="business"');
  });
});
