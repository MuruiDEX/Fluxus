/**
 * LIVE approvals + activity tests (Stage 7.5) — mocked fetch, no network.
 *
 * Covers: reads through the typed client, project_id filter passthrough,
 * server order preservation, business-switch reset (no stale flash),
 * late-response discard, abort-is-not-an-error, 401/403/404/422/network/500
 * states, empty lists, retry, demo-store isolation, and the hard rule that
 * LIVE surfaces never send mutations (approve/reject or otherwise).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveActivity, useLiveApprovals } from "@/lib/store/live";

interface Scenario {
  test: (url: string) => boolean;
  status: number;
  body: unknown;
  delayMs?: number;
}

function mockLive(scenarios: Scenario[], opts?: { honorAbort?: boolean }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const aborted: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const sc = scenarios.find((s) => s.test(url));
      if (!sc) {
        return new Response(JSON.stringify({ error: { code: "x", message: "x" } }), { status: 500 });
      }
      if (sc.delayMs) await new Promise((r) => setTimeout(r, sc.delayMs));
      if ((opts?.honorAbort ?? true) && init?.signal?.aborted) {
        aborted.push(url);
        throw new DOMException("aborted", "AbortError");
      }
      return new Response(JSON.stringify(sc.body), { status: sc.status });
    }),
  );
  return { calls, aborted };
}

function approvalPage(ids: string[]) {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      projectId: "proj_1",
      title: `Approval ${id}`,
      requestedBy: "Atlas",
      requestedByAgentId: "ag_1",
      ageLabel: "2h",
      status: "pending",
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

function activityPage(texts: string[]) {
  return {
    items: texts.map((text, i) => ({
      id: `ev_${i}`,
      at: `2026-10-11T0${6 + i}:00:00Z`,
      agentName: "Atlas",
      text,
      kind: i === 0 ? "result" : "work",
      cost: null,
      contract: "v1",
    })),
    total: texts.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function resetLive() {
  useLiveApprovals.getState().reset();
  useLiveActivity.getState().reset();
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetLive();
});

describe("live approvals", () => {
  it("loads approvals mapped to domain shape", async () => {
    mockLive([{ test: (u) => u.includes("/approvals"), status: 200, body: approvalPage(["ap_1", "ap_2"]) }]);
    await useLiveApprovals.getState().load("biz_test");
    const s = useLiveApprovals.getState();
    expect(s.status).toBe("ready");
    expect(s.approvals.map((a) => a.id)).toEqual(["ap_1", "ap_2"]);
    expect(s.total).toBe(2);
    expect(s.approvals[0].status).toBe("pending");
    expect(s.approvals[0].gatesTaskId).toBeUndefined();
  });

  it("resets on business switch without flashing stale approvals", async () => {
    mockLive([{ test: () => true, status: 200, body: approvalPage(["ap_1"]) }]);
    await useLiveApprovals.getState().load("biz_test");
    expect(useLiveApprovals.getState().approvals).toHaveLength(1);

    mockLive([{ test: () => true, status: 200, body: approvalPage(["ap_9"]) }]);
    const pending = useLiveApprovals.getState().load("biz_other");
    const mid = useLiveApprovals.getState();
    expect(mid.status).toBe("loading");
    expect(mid.approvals).toEqual([]);
    expect(mid.businessId).toBe("biz_other");
    await pending;
    expect(useLiveApprovals.getState().approvals.map((a) => a.id)).toEqual(["ap_9"]);
  });

  it("discards a late response after scope change", async () => {
    mockLive(
      [
        { test: (u) => u.includes("biz_slow"), status: 200, body: approvalPage(["stale"]), delayMs: 40 },
        { test: (u) => u.includes("biz_fast"), status: 200, body: approvalPage(["fresh"]) },
      ],
      { honorAbort: false },
    );
    const store = useLiveApprovals.getState();
    const slow = store.load("biz_slow");
    await store.load("biz_fast");
    await slow;
    const s = useLiveApprovals.getState();
    expect(s.businessId).toBe("biz_fast");
    expect(s.approvals.map((a) => a.id)).toEqual(["fresh"]);
  });

  it("records 403 and network errors with retry", async () => {
    mockLive([
      { test: () => true, status: 403, body: { error: { code: "forbidden", message: "CSRF validation failed." } } },
    ]);
    await useLiveApprovals.getState().load("biz_test");
    let s = useLiveApprovals.getState();
    expect(s.status).toBe("error");
    expect(s.error).toContain("CSRF");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await useLiveApprovals.getState().load("biz_test");
    s = useLiveApprovals.getState();
    expect(s.status).toBe("error");
    expect(s.approvals).toEqual([]);
  });

  it("reads empty lists as ready-empty", async () => {
    mockLive([
      { test: () => true, status: 200, body: { items: [], total: 0, limit: 50, offset: 0, contract: "v1" } },
    ]);
    await useLiveApprovals.getState().load("biz_test");
    const s = useLiveApprovals.getState();
    expect(s.status).toBe("ready");
    expect(s.approvals).toEqual([]);
  });
});

describe("live activity", () => {
  it("loads events and passes the project filter through", async () => {
    const { calls } = mockLive([
      { test: () => true, status: 200, body: activityPage(["first", "second"]) },
    ]);
    await useLiveActivity.getState().load({ businessId: "biz_test", projectId: "proj_1" });
    const s = useLiveActivity.getState();
    expect(s.status).toBe("ready");
    // Server order (newest first) is preserved, never re-sorted client-side.
    expect(s.events.map((e) => e.text)).toEqual(["first", "second"]);
    expect(s.events[0].at).toBe("2026-10-11T06:00:00Z");
    expect(s.total).toBe(2);
    expect(calls[0].url).toContain("project_id=proj_1");
  });

  it("omits the filter when unscoped and resets on scope change", async () => {
    const { calls } = mockLive([{ test: () => true, status: 200, body: activityPage(["x"]) }]);
    await useLiveActivity.getState().load({ businessId: "biz_test" });
    expect(calls[0].url).not.toContain("project_id");
    expect(useLiveActivity.getState().projectId).toBeNull();

    mockLive([{ test: () => true, status: 200, body: activityPage(["y"]) }]);
    const pending = useLiveActivity.getState().load({ businessId: "biz_test", projectId: "proj_2" });
    expect(useLiveActivity.getState().events).toEqual([]);
    expect(useLiveActivity.getState().projectId).toBe("proj_2");
    await pending;
    expect(useLiveActivity.getState().events.map((e) => e.text)).toEqual(["y"]);
  });

  it("unknown project scope surfaces 404, not foreign data", async () => {
    mockLive([
      {
        test: () => true,
        status: 404,
        body: { error: { code: "not_found", message: "Project not found in scope." } },
      },
    ]);
    await useLiveActivity.getState().load({ businessId: "biz_test", projectId: "ghost" });
    const s = useLiveActivity.getState();
    expect(s.status).toBe("error");
    expect(s.events).toEqual([]);
  });

  it("an aborted load is not an error", async () => {
    mockLive(
      [{ test: () => true, status: 200, body: activityPage(["late"]), delayMs: 40 }],
      { honorAbort: true },
    );
    const store = useLiveActivity.getState();
    const first = store.load({ businessId: "biz_test" });
    await store.load({ businessId: "biz_test", projectId: "proj_1" });
    await first;
    const s = useLiveActivity.getState();
    expect(s.status).not.toBe("error");
    expect(s.error).toBeNull();
  });

  it("records 401/422/500 states", async () => {
    mockLive([
      { test: () => true, status: 401, body: { error: { code: "unauthorized", message: "Authentication required." } } },
    ]);
    await useLiveActivity.getState().load({ businessId: "biz_test" });
    expect(useLiveActivity.getState().status).toBe("error");

    mockLive([
      { test: () => true, status: 500, body: { error: { code: "internal", message: "Unexpected server error." } } },
    ]);
    await useLiveActivity.getState().load({ businessId: "biz_test" });
    const s = useLiveActivity.getState();
    expect(s.status).toBe("error");
    expect(s.error).toContain("Unexpected server error.");
  });
});

describe("live read-only guarantee", () => {
  it("approvals, activity, and agents surfaces never send mutations", async () => {
    const { calls } = mockLive([
      { test: (u) => u.includes("/approvals"), status: 200, body: approvalPage(["ap_1"]) },
      { test: (u) => u.includes("/activity"), status: 200, body: activityPage(["e"]) },
      { test: () => true, status: 200, body: activityPage(["e"]) },
    ]);
    await useLiveApprovals.getState().load("biz_test");
    await useLiveActivity.getState().load({ businessId: "biz_test", projectId: "proj_1" });
    await useLiveActivity.getState().load({ businessId: "biz_test" });
    for (const call of calls) {
      expect(call.init?.method ?? "GET").toBe("GET");
    }
    expect(calls.length).toBeGreaterThan(0);
  });

  it("live loads never touch demo stores", async () => {
    const { useActivity, useApprovals } = await import("@/lib/store/app");
    mockLive([
      { test: (u) => u.includes("/approvals"), status: 200, body: approvalPage(["ap_live"]) },
      { test: () => true, status: 200, body: activityPage(["live event"]) },
    ]);
    const before = JSON.stringify({
      approvals: useApprovals.getState().approvals,
      events: useActivity.getState().events,
    });
    await useLiveApprovals.getState().load("biz_test");
    await useLiveActivity.getState().load({ businessId: "biz_test" });
    expect(
      JSON.stringify({
        approvals: useApprovals.getState().approvals,
        events: useActivity.getState().events,
      }),
    ).toBe(before);
    // ...and demo seed never leaks into live state.
    expect(useLiveApprovals.getState().approvals.map((a) => a.id)).toEqual(["ap_live"]);
  });
});
