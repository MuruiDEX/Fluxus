/**
 * LIVE intake tests (Stage 11) — mocked fetch, no network.
 *
 * Covers: DEMO ban without any request, exact request shapes (goal body,
 * snake_case answers, empty plan body), phase transitions from server
 * answers, resume mapping from persisted statuses, single flight under
 * concurrency, honest 401/403/404/409/422/network records, and the brief
 * client call shapes (path/method/CSRF/contract).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiAnswerBrief,
  apiCreateBrief,
  apiPlanBrief,
  setCsrfToken,
} from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import { useLiveIntake } from "@/lib/store/intake";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Scenario {
  test: (url: string, init?: RequestInit) => boolean;
  status: number;
  body: unknown;
}

function mockFetch(scenarios: Scenario[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const hit = scenarios.find((s) => s.test(url, init));
      if (!hit) return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
      return jsonResponse(hit.status, hit.body);
    }),
  );
  return calls;
}

function signIn(businesses: string[] = ["biz_test"]) {
  setCsrfToken("csrf-test");
  useSession.setState({
    status: "authenticated",
    user: { id: "u_1", email: "op@example.com", displayName: "Op" },
    businesses,
    currentBusinessId: businesses[0] ?? null,
    error: null,
    sessionError: null,
  });
}

function signOut() {
  useSession.setState({
    status: "anonymous",
    user: null,
    businesses: [],
    currentBusinessId: null,
    error: null,
    sessionError: null,
  });
}

function briefJson(status: string, over: Record<string, unknown> = {}) {
  return {
    id: "br_1",
    businessId: "biz_test",
    goal: "Launch it.",
    status,
    questions: [
      { id: "q1", text: "Who is it for?", options: ["A", "B"] },
      { id: "q2", text: "What counts as done?" },
    ],
    answers: [],
    summary: null,
    projectId: null,
    contract: "v1",
    ...over,
  };
}

function planJson() {
  return {
    briefId: "br_1",
    project: {
      id: "proj_9",
      businessId: "biz_test",
      title: "P",
      goal: "",
      status: "planning",
      progress: 0,
      budget: "0",
      aiBudget: "0",
      aiSpent: "0",
      deadline: "",
      teamIds: [],
      milestones: [],
      contract: "v1",
    },
    tasks: [],
    approvals: [],
    contract: "v1",
  };
}

function record() {
  return useLiveIntake.getState().records["biz_test"];
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveIntake.setState({ records: {} });
});

describe("intake client shapes", () => {
  it("POSTs goal / snake_case answers / empty plan with CSRF + contract", async () => {
    const calls = mockFetch([
      { test: (u) => u.endsWith("/briefs"), status: 200, body: briefJson("questions_ready") },
      { test: (u) => u.endsWith("/answers"), status: 200, body: briefJson("brief_ready") },
      { test: (u) => u.endsWith("/plan"), status: 200, body: planJson() },
    ]);
    setCsrfToken("csrf-123");
    await apiCreateBrief("biz_test", "Launch it.");
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].url).toBe("http://127.0.0.1:8000/api/v1/businesses/biz_test/briefs");
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({ goal: "Launch it." });
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-123");
    expect((calls[0].init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");

    await apiAnswerBrief("biz_test", "br_1", [{ question_id: "q1", text: "Everyone" }]);
    const answerCall = calls[1];
    expect(answerCall.url).toContain("/briefs/br_1/answers");
    // Backend wire names are snake_case (no camel alias on the schema).
    expect(JSON.parse(answerCall.init?.body as string)).toEqual({
      answers: [{ question_id: "q1", text: "Everyone" }],
    });

    const plan = await apiPlanBrief("biz_test", "br_1");
    expect(plan.project.id).toBe("proj_9");
    expect(calls[2].init?.method).toBe("POST");
  });
});

describe("intake runner", () => {
  it("refuses DEMO and foreign contexts without any request", async () => {
    const calls = mockFetch([{ test: () => true, status: 200, body: briefJson("questions_ready") }]);
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    expect(calls).toHaveLength(0);
    expect(record().errorCode).toBe("unauthorized");

    signIn(["biz_other"]);
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    expect(calls).toHaveLength(0);
    expect(record().errorCode).toBe("forbidden");
  });

  it("rejects a blank goal without any request", async () => {
    signIn();
    const calls = mockFetch([{ test: () => true, status: 200, body: briefJson("questions_ready") }]);
    await useLiveIntake.getState().start("biz_test", "   ");
    expect(calls).toHaveLength(0);
    expect(record().phase).toBe("error");
  });

  it("start stores server questions and moves to answering", async () => {
    signIn();
    const calls = mockFetch([
      { test: (u, init) => u.endsWith("/briefs") && init?.method === "POST", status: 200, body: briefJson("questions_ready") },
    ]);
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    expect(calls).toHaveLength(1);
    expect(record().phase).toBe("answering");
    expect(record().brief?.questions).toHaveLength(2);
  });

  it("records invalid generations honestly (422, nothing stored)", async () => {
    signIn();
    mockFetch([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "validation_error", message: "Model output failed validation." } },
      },
    ]);
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    const rec = record();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("validation_error");
    expect(rec.brief).toBeNull();
  });

  it("answer stores the brief summary and moves to reviewing", async () => {
    signIn();
    mockFetch([
      {
        test: (u) => u.endsWith("/answers"),
        status: 200,
        body: briefJson("brief_ready", { summary: { goal: "Launch it." } }),
      },
    ]);
    await useLiveIntake.getState().answer("biz_test", "br_1", [{ question_id: "q1", text: "A" }]);
    expect(record().phase).toBe("reviewing");
    expect(record().brief?.summary).toEqual({ goal: "Launch it." });
  });

  it("answer conflict surfaces honestly", async () => {
    signIn();
    mockFetch([
      {
        test: () => true,
        status: 409,
        body: { error: { code: "conflict", message: "Brief already finalized with different answers." } },
      },
    ]);
    await useLiveIntake.getState().answer("biz_test", "br_1", [{ question_id: "q1", text: "A" }]);
    expect(record().phase).toBe("error");
    expect(record().errorCode).toBe("conflict");
  });

  it("plan stores the graph and moves to done", async () => {
    signIn();
    mockFetch([
      { test: (u) => u.endsWith("/plan"), status: 200, body: planJson() },
      {
        test: (u) => u.includes("/briefs/br_1") && (u.endsWith("br_1") || u.includes("br_1?")),
        status: 200,
        body: briefJson("planned", { projectId: "proj_9" }),
      },
    ]);
    await useLiveIntake.getState().plan("biz_test", "br_1");
    const rec = record();
    expect(rec.phase).toBe("done");
    expect(rec.plan?.project.id).toBe("proj_9");
    expect(rec.brief?.projectId).toBe("proj_9");
  });

  it("resume maps persisted statuses to steps", async () => {
    signIn();
    for (const [status, phase] of [
      ["questions_ready", "answering"],
      ["brief_ready", "reviewing"],
      ["planned", "done"],
    ] as const) {
      mockFetch([{ test: () => true, status: 200, body: briefJson(status) }]);
      await useLiveIntake.getState().resume("biz_test", "br_1");
      expect(record().phase).toBe(phase);
    }
    mockFetch([{ test: () => true, status: 200, body: briefJson("draft") }]);
    await useLiveIntake.getState().resume("biz_test", "br_1");
    expect(record().phase).toBe("error");
    expect(record().errorCode).toBe("conflict");

    mockFetch([
      { test: () => true, status: 404, body: { error: { code: "not_found", message: "Nope." } } },
    ]);
    await useLiveIntake.getState().resume("biz_test", "ghost");
    expect(record().errorCode).toBe("not_found");
  });

  it("sends exactly one POST under concurrency", async () => {
    signIn();
    const calls = mockFetch([
      { test: () => true, status: 200, body: briefJson("questions_ready") },
    ]);
    const store = useLiveIntake.getState();
    await Promise.all([
      store.start("biz_test", "Launch it."),
      store.start("biz_test", "Launch it."),
    ]);
    expect(calls).toHaveLength(1);
  });

  it("reports network loss without fake success", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    const rec = record();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("network_unreachable");
    expect(rec.brief).toBeNull();
  });

  it("clear drops the record", async () => {
    signIn();
    mockFetch([{ test: () => true, status: 200, body: briefJson("questions_ready") }]);
    await useLiveIntake.getState().start("biz_test", "Launch it.");
    expect(record()).toBeDefined();
    useLiveIntake.getState().clear("biz_test");
    expect(record()).toBeUndefined();
  });
});
