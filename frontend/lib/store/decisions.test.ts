/**
 * LIVE approval decision tests (Stage 7.7) — mocked fetch, no network.
 *
 * Covers: client call shape (path/method/CSRF/contract), DEMO ban, terminal
 * precheck (no re-POST), single POST under concurrency, cache + counts sync,
 * already_* reconcile, 409 conflict reload, honest error records, business
 * switch staleness, activity reload only-when-loaded without fabrication,
 * never auto-running the linked task, and the decision-button UI states
 * (static render — no DOM test infra in this repo).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { apiApproveApproval, apiRejectApproval, setCsrfToken } from "@/lib/api";
import { DecisionButtons, describeDecision } from "@/components/system/DecisionButtons";
import { useSession } from "@/lib/store/auth";
import { useLiveDecisions } from "@/lib/store/decisions";
import { useLiveActivity, useLiveApprovals } from "@/lib/store/live";
import type { Approval } from "@/lib/domain/types";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function decisionJson(outcome: string, status: string | null, approvalId = "ap_1") {
  return { outcome, approvalId, status, contract: "v1" };
}

function approvalObj(id: string, status: Approval["status"]): Approval {
  return {
    id,
    businessId: "biz_test",
    projectId: "proj_1",
    title: `Approval ${id}`,
    requestedBy: "Atlas",
    requestedByAgentId: "ag_1",
    ageLabel: "2h",
    status,
    what: "W",
    expectedResult: "E",
    cost: "$10",
    affects: "A",
    rationale: "R",
  };
}

function approvalPage(ids: string[], status: Approval["status"] = "pending") {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      projectId: "proj_1",
      title: `Approval ${id}`,
      requestedBy: "Atlas",
      requestedByAgentId: "ag_1",
      ageLabel: "2h",
      status,
      what: "W",
      expectedResult: "E",
      cost: "$10",
      affects: "A",
      rationale: "R",
      gatesTaskId: null,
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function signIn(businesses: string[] = ["biz_test"]) {
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

function seedApprovalsCache(ids: string[], status: Approval["status"] = "pending") {
  useLiveApprovals.setState({
    status: "ready",
    businessId: "biz_test",
    approvals: ids.map((id) => approvalObj(id, status)),
    total: ids.length,
    error: null,
  });
}

function recordOf(approvalId = "ap_1") {
  return useLiveDecisions.getState().records[`biz_test/${approvalId}`];
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveDecisions.setState({ records: {} });
  useLiveApprovals.setState({ status: "idle", businessId: null, approvals: [], total: 0, error: null });
  useLiveActivity.setState({ status: "idle", businessId: null, projectId: null, events: [], total: 0, error: null });
});

describe("decision client", () => {
  it("approve POSTs the exact path with CSRF and contract header", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, decisionJson("approved", "approved"));
      }),
    );
    setCsrfToken("csrf-1");
    const answer = await apiApproveApproval("biz_test", "ap_1");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/approvals/ap_1/approve",
    );
    expect(calls[0].init?.method).toBe("POST");
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect((calls[0].init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");
    expect(answer.outcome).toBe("approved");
  });

  it("reject POSTs the reject path", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return jsonResponse(200, decisionJson("rejected", "rejected"));
      }),
    );
    await apiRejectApproval("biz_test", "ap_1");
    expect(calls).toEqual([
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/approvals/ap_1/reject",
    ]);
  });
});

describe("decision runner", () => {
  function mockFlow(routes: { test: (url: string) => boolean; respond: () => Response | Promise<Response> }[]) {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        const route = routes.find((r) => r.test(url));
        if (!route) return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
        const signal = init?.signal;
        if (signal?.aborted) throw new DOMException("aborted", "AbortError");
        return new Promise<Response>((resolve, reject) => {
          const onAbort = () => reject(new DOMException("aborted", "AbortError"));
          signal?.addEventListener("abort", onAbort, { once: true });
          Promise.resolve()
            .then(() => route.respond())
            .then(
              (res) => {
                signal?.removeEventListener("abort", onAbort);
                resolve(res);
              },
              (err) => {
                signal?.removeEventListener("abort", onAbort);
                reject(err);
              },
            );
        });
      }),
    );
    return { calls };
  }

  function postsTo(calls: { url: string; init?: RequestInit }[], part: string): number {
    return calls.filter((c) => (c.init?.method ?? "GET") === "POST" && c.url.includes(part)).length;
  }

  it("refuses DEMO and foreign business without any request", async () => {
    const { calls } = mockFlow([{ test: () => true, respond: () => jsonResponse(200, {}) }]);
    signOut();
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(calls).toHaveLength(0);
    expect(recordOf()?.message).toMatch(/sign in/i);

    signIn(["biz_other"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(calls).toHaveLength(0);
    expect(recordOf()?.message).toMatch(/no access/i);
  });

  it("never re-POSTs a terminal approval from the UI", async () => {
    const { calls } = mockFlow([{ test: () => true, respond: () => jsonResponse(200, {}) }]);
    signIn();
    seedApprovalsCache(["ap_1"], "approved");
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(calls).toHaveLength(0);
    expect(recordOf()?.phase).toBe("decided");

    await useLiveDecisions.getState().decide("biz_test", "ap_1", "rejected");
    expect(calls).toHaveLength(0);
    expect(recordOf()?.errorCode).toBe("conflict");
  });

  it("sends exactly one POST on concurrent decide calls", async () => {
    const gate = deferred<Response>();
    const { calls } = mockFlow([
      { test: (u) => u.includes("/approve"), respond: () => gate.promise },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"])) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    const store = useLiveDecisions.getState();
    const first = store.decide("biz_test", "ap_1", "approved");
    const second = store.decide("biz_test", "ap_1", "approved");
    gate.resolve(jsonResponse(200, decisionJson("approved", "approved")));
    await Promise.all([first, second]);
    expect(postsTo(calls, "/approve")).toBe(1);
  });

  it("success applies the decision to the cache and counts", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(200, decisionJson("approved", "approved")),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1", "ap_2"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    const run = recordOf();
    expect(run?.phase).toBe("decided");
    expect(run?.status).toBe("approved");
    const cached = useLiveApprovals.getState().approvals.find((a) => a.id === "ap_1");
    expect(cached?.status).toBe("approved");
    expect(useLiveApprovals.getState().approvals.find((a) => a.id === "ap_2")?.status).toBe("pending");
  });

  it("already_approved reconciles without duplicating", async () => {
    const { calls } = mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(200, decisionJson("already_approved", "approved")),
      },
      { test: (u) => u.includes("/approvals") && !(u.includes("/approve") || u.includes("/reject")), respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(postsTo(calls, "/approve")).toBe(1);
    const run = recordOf();
    expect(run?.phase).toBe("decided");
    expect(run?.status).toBe("approved");
    expect(run?.message).toMatch(/already approved/i);
    expect(useLiveApprovals.getState().approvals.find((a) => a.id === "ap_1")?.status).toBe("approved");
  });

  it("409 conflict reloads server state with a clear message", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/reject"),
        respond: () => jsonResponse(409, decisionJson("conflict", "approved")),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "rejected");
    const run = recordOf();
    expect(run?.phase).toBe("error");
    expect(run?.errorCode).toBe("conflict");
    expect(run?.message).toMatch(/already decided|changed on the server/i);
    expect(useLiveApprovals.getState().approvals.find((a) => a.id === "ap_1")?.status).toBe("approved");
  });

  it("maps 401/403/404/422/network/500 to honest error records", async () => {
    signIn();
    const cases: [number, unknown][] = [
      [401, { error: { code: "unauthorized", message: "Authentication required." } }],
      [403, { error: { code: "forbidden", message: "CSRF validation failed." } }],
      [404, { error: { code: "not_found", message: "Approval not found in scope." } }],
      [422, { error: { code: "validation_error", message: "Request failed validation." } }],
      [500, { error: { code: "internal", message: "Unexpected server error." } }],
    ];
    for (const [status, body] of cases) {
      mockFlow([
        { test: (u) => u.includes("/approve"), respond: () => jsonResponse(status, body) },
        { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"])) },
      ]);
      signIn(); // 401 cases log out via the shared flow — re-authenticate per case.
      useLiveDecisions.getState().clear("biz_test", "ap_1");
      seedApprovalsCache(["ap_1"]);
      await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
      const run = recordOf();
      expect(run?.phase).toBe("error");
      expect(run?.message).not.toBeNull();
    }

    mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => {
          throw new TypeError("fetch failed");
        },
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"])) },
    ]);
    signIn();
    useLiveDecisions.getState().clear("biz_test", "ap_1");
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    const run = recordOf();
    expect(run?.phase).toBe("error");
    expect(run?.message).toMatch(/reconcile|retry|contact/i);
  });

  it("401 flips the session to anonymous via the shared flow", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(401, { error: { code: "unauthorized", message: "Authentication required." } }),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"])) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(useSession.getState().status).toBe("anonymous");
  });

  it("drops late completions after clear", async () => {
    const gate = deferred<Response>();
    mockFlow([
      { test: (u) => u.includes("/approve"), respond: () => gate.promise },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"])) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    const pending = useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    await Promise.resolve();
    useLiveDecisions.getState().clear("biz_test", "ap_1");
    gate.resolve(jsonResponse(200, decisionJson("approved", "approved")));
    await pending;
    expect(recordOf()).toBeUndefined();
  });

  it("reloads a loaded activity feed from the server without fabrication", async () => {
    mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(200, decisionJson("approved", "approved")),
      },
      {
        test: (u) => u.includes("/activity"),
        respond: () =>
          jsonResponse(200, {
            items: [
              {
                id: "ev_new",
                at: "2026-10-11T06:00:00Z",
                agentName: "op@example.com",
                text: "Approval ap_1 approved by op@example.com.",
                kind: "approval",
                cost: null,
                contract: "v1",
              },
            ],
            total: 1,
            limit: 200,
            offset: 0,
            contract: "v1",
          }),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    useLiveActivity.setState({
      status: "ready",
      businessId: "biz_test",
      projectId: null,
      events: [],
      total: 0,
      error: null,
    });
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    const events = useLiveActivity.getState().events;
    expect(events).toHaveLength(1);
    expect(events[0].id).toBe("ev_new");
    expect(events[0].text).toBe("Approval ap_1 approved by op@example.com.");
  });

  it("skips activity reload when the feed was never loaded", async () => {
    const { calls } = mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(200, decisionJson("approved", "approved")),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(calls.some((c) => c.url.includes("/activity"))).toBe(false);
  });

  it("deciding never runs the linked task", async () => {
    const { calls } = mockFlow([
      {
        test: (u) => u.includes("/approve"),
        respond: () => jsonResponse(200, decisionJson("approved", "approved")),
      },
      { test: () => true, respond: () => jsonResponse(200, approvalPage(["ap_1"], "approved")) },
    ]);
    signIn();
    seedApprovalsCache(["ap_1"]);
    await useLiveDecisions.getState().decide("biz_test", "ap_1", "approved");
    expect(calls.some((c) => c.url.includes("/executions"))).toBe(false);
  });
});

describe("DecisionButtons states", () => {
  const ids = { businessId: "biz_test", approvalId: "ap_1" };

  function show() {
    return renderToStaticMarkup(createElement(DecisionButtons, { ...ids }));
  }

  it("idle offers Approve and Reject", () => {
    const html = show();
    expect(html).toContain("Approve");
    expect(html).toContain("Reject");
  });

  it("maps every decision phase without implying silent success", () => {
    const view = (record: Parameters<typeof describeDecision>[0]) => describeDecision(record);

    expect(view(undefined)).toMatchObject({ kind: "idle", showRetry: false });
    expect(
      view({ phase: "deciding", decision: "approved", status: null, message: null, errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "deciding", showRetry: false });
    expect(
      view({ phase: "decided", decision: "approved", status: "approved", message: "Recorded.", errorCode: null, updatedAt: 1 }),
    ).toMatchObject({ kind: "decided", tone: "ok", showRetry: false });
    expect(
      view({ phase: "error", decision: "approved", status: null, message: "Changed on the server.", errorCode: "conflict", updatedAt: 1 }),
    ).toMatchObject({ kind: "error", tone: "bad", showRetry: true });
  });
});
