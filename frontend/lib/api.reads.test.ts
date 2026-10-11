import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiGetAgent,
  apiGetApproval,
  apiGetBusiness,
  apiGetProject,
  apiGetTask,
  apiListActivity,
  apiListAgents,
  apiListApprovals,
  apiListBrain,
  apiListProjects,
  apiListTasks,
  onUnauthorized,
  parseIsoDate,
  parseMoney,
  setCsrfToken,
} from "@/lib/api";

function mockFetchOnce(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function headersOf(call: { init?: RequestInit }): Record<string, string> {
  return (call.init?.headers ?? {}) as Record<string, string>;
}

const TASK = {
  id: "t_1",
  businessId: "biz_test",
  projectId: "proj_1",
  title: "T",
  ownerAgentId: "ag_1",
  status: "not_started",
  progress: 0,
  costAccrued: "0.00",
  etaMinutes: 0,
  summary: "",
  attempts: 0,
  approvalGateId: null,
  gateAt: null,
  costRate: null,
  contract: "v1",
};

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  onUnauthorized(null);
});

describe("read api client urls", () => {
  it("builds single-get paths with encoded ids", async () => {
    const calls = mockFetchOnce(() => jsonResponse(200, TASK));
    await apiGetTask("biz:a", "proj_1", "t_1");
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz%3Aa/projects/proj_1/tasks/t_1",
    );
  });

  it("builds list paths with limit/offset and filters", async () => {
    const page = { items: [], total: 0, limit: 10, offset: 20, contract: "v1" };
    const calls = mockFetchOnce(() => jsonResponse(200, page));
    await apiListTasks("biz_test", "proj_1", { limit: 10, offset: 20 });
    expect(calls[0].url).toContain("/projects/proj_1/tasks?limit=10&offset=20");

    await apiListActivity("biz_test", { projectId: "proj_1", limit: 5 });
    expect(calls[1].url).toContain("/activity?limit=5&project_id=proj_1");

    await apiListBrain("biz_test", { category: "goals" });
    expect(calls[2].url).toContain("/brain?category=goals");

    await apiListProjects("biz_test");
    expect(calls[3].url).toBe("http://127.0.0.1:8000/api/v1/businesses/biz_test/projects");
  });

  it("sends contract header and credentials but never CSRF on GET", async () => {
    setCsrfToken("csrf-123");
    const calls = mockFetchOnce(() => jsonResponse(200, TASK));
    await apiGetBusiness("biz_test");
    await apiListAgents("biz_test", { limit: 1 });
    for (const call of calls) {
      expect(call.init?.credentials).toBe("include");
      expect(headersOf(call)["X-Client-Contract"]).toBe("1");
      expect(headersOf(call)["X-CSRF-Token"]).toBeUndefined();
      expect(call.init?.method).toBe("GET");
    }
  });
});

describe("read api client parsing", () => {
  it("parses task, envelope passthrough, and empty lists", async () => {
    mockFetchOnce((url) =>
      jsonResponse(
        200,
        url.includes("/tasks/") && !url.includes("?")
          ? TASK
          : { items: [TASK], total: 7, limit: 50, offset: 0, contract: "v1" },
      ),
    );
    const task = await apiGetTask("biz_test", "proj_1", "t_1");
    expect(task.status).toBe("not_started");
    expect(task.attempts).toBe(0);
    expect(task.approvalGateId).toBeNull();

    const page = await apiListTasks("biz_test", "proj_1");
    expect(page.total).toBe(7);
    expect(page.items).toHaveLength(1);
    expect(page.contract).toBe("v1");

    mockFetchOnce(() => jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 }));
    const empty = await apiListApprovals("biz_test");
    expect(empty.items).toEqual([]);
    expect(empty.total).toBe(0);
  });

  it("parses business, project, agent, approval, activity, brain shapes", async () => {
    const bodies: Record<string, unknown> = {
      business: {
        id: "b",
        name: "N",
        tagline: "",
        industry: "",
        location: "",
        ownerName: "O",
        healthScore: 80,
        healthBreakdown: [],
        metrics: {},
        revenueSeries: [],
        contract: "v1",
      },
      project: {
        id: "p",
        businessId: "b",
        title: "P",
        goal: "",
        status: "in_progress",
        progress: 10,
        budget: "100.00",
        aiBudget: "50.00",
        aiSpent: "2.20",
        deadline: "",
        teamIds: [],
        milestones: [],
        contract: "v1",
      },
      agent: {
        id: "a",
        businessId: "b",
        name: "A",
        role: "R",
        responsibilities: [],
        tools: [],
        permissions: {},
        status: "available",
        currentTask: null,
        costPerMonth: 9,
        performance: {},
        hired: true,
        avatarHue: 200,
        contract: "v1",
      },
      approval: {
        id: "ap",
        businessId: "b",
        projectId: "p",
        title: "T",
        requestedBy: "",
        requestedByAgentId: "a",
        ageLabel: "",
        status: "pending",
        what: "",
        expectedResult: "",
        cost: "",
        affects: "",
        rationale: "",
        gatesTaskId: null,
        contract: "v1",
      },
      event: {
        id: "e",
        at: "2026-10-11T06:00:00Z",
        agentName: "A",
        text: "hi",
        kind: "work",
        cost: null,
        contract: "v1",
      },
      entry: {
        id: "br",
        businessId: "b",
        category: "goals",
        title: "T",
        body: "B",
        updatedLabel: "",
        contract: "v1",
      },
    };
    mockFetchOnce((url) => {
      const key = url.includes("/approvals")
        ? "approval"
        : url.includes("/agents")
          ? "agent"
          : url.includes("/activity")
            ? "event"
            : url.includes("/brain")
              ? "entry"
              : "project";
      return jsonResponse(200, bodies[key]);
    });
    expect((await apiGetProject("b", "p")).aiSpent).toBe("2.20");
    expect((await apiGetAgent("b", "a")).hired).toBe(true);
    expect((await apiGetApproval("b", "ap")).status).toBe("pending");
    const events = await apiListActivity("b");
    expect(events.contract).toBe("v1");
    const brain = await apiListBrain("b", { category: "goals" });
    expect(brain.contract).toBe("v1");
  });
});

describe("read api client errors", () => {
  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [422, "validation_error"],
  ])("maps %i to code %s", async (status, code) => {
    mockFetchOnce(() =>
      jsonResponse(status, { error: { code, message: "m", trace_id: "t" } }),
    );
    const err = await apiGetBusiness("biz_test").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(status);
    expect((err as ApiError).code).toBe(code);
  });

  it("fires the 401 hook on reads and maps network failure", async () => {
    mockFetchOnce(() => jsonResponse(401, { error: { code: "unauthorized", message: "x" } }));
    const seen: string[] = [];
    onUnauthorized(() => seen.push("fired"));
    await expect(apiListProjects("biz_test")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toEqual(["fired"]);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const err = await apiGetBusiness("biz_test").catch((e) => e);
    expect((err as ApiError).code).toBe("network_unreachable");
    expect((err as ApiError).status).toBe(0);
  });
});

describe("wire value helpers", () => {
  it("parses money strings and numbers", () => {
    expect(parseMoney("0.00")).toBe(0);
    expect(parseMoney("2.20")).toBeCloseTo(2.2);
    expect(parseMoney(12.5)).toBe(12.5);
    expect(parseMoney("")).toBe(0);
  });

  it("parses RFC 3339 dates strictly", () => {
    expect(parseIsoDate("2026-10-11T06:00:00Z")?.toISOString()).toBe("2026-10-11T06:00:00.000Z");
    expect(parseIsoDate("")).toBeNull();
    expect(parseIsoDate("2h ago")).toBeNull();
  });
});
