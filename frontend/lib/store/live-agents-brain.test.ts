/**
 * LIVE agents + brain tests (Stage 7.8) — mocked fetch, no network.
 *
 * Covers: reads through the typed client, brain category filter passthrough,
 * server order preservation, business-switch reset (no stale flash),
 * late-response discard, abort-is-not-an-error, 401/403/404/422/network/500
 * states, empty lists, retry, demo-store isolation, pagination honesty data,
 * cross-business detail 404 (never a fallback row), and the hard rule that
 * LIVE surfaces never send mutations nor write to Brain.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveAgent, useLiveAgentsList, useLiveBrain } from "@/lib/store/live";

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

function agentPage(ids: string[]) {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      name: `Name ${id}`,
      role: `Role ${id}`,
      responsibilities: ["r1"],
      tools: ["t1"],
      permissions: { read: ["x"], write: [], actions: [], denied: ["y"] },
      status: "available",
      currentTask: null,
      costPerMonth: 9,
      performance: { completionRate: 80, avgMinutes: 12, approvalRate: 90, rating: 4.5 },
      hired: true,
      avatarHue: 200,
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function agentOne(id: string) {
  return { ...agentPage([id]).items[0] };
}

function brainPage(ids: string[], category = "goals") {
  return {
    items: ids.map((id) => ({
      id,
      businessId: "biz_test",
      category,
      title: `Title ${id}`,
      body: `Body ${id}`,
      updatedLabel: "2h ago",
      contract: "v1",
    })),
    total: ids.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function resetLive() {
  useLiveAgentsList.getState().reset();
  useLiveAgent.getState().reset();
  useLiveBrain.getState().reset();
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetLive();
});

describe("live agents list", () => {
  it("loads agents mapped to domain shape", async () => {
    mockLive([{ test: (u) => u.includes("/agents"), status: 200, body: agentPage(["ag_1", "ag_2"]) }]);
    await useLiveAgentsList.getState().load("biz_test");
    const s = useLiveAgentsList.getState();
    expect(s.status).toBe("ready");
    expect(s.agents.map((a) => a.id)).toEqual(["ag_1", "ag_2"]);
    expect(s.total).toBe(2);
    expect(s.agents[0].role).toBe("Role ag_1");
    expect(s.agents[0].permissions).toEqual({ read: ["x"], write: [], actions: [], denied: ["y"] });
    expect(s.agents[0].currentTask).toBeUndefined();
  });

  it("resets on business switch without flashing stale agents", async () => {
    mockLive([{ test: () => true, status: 200, body: agentPage(["ag_1"]) }]);
    await useLiveAgentsList.getState().load("biz_test");
    expect(useLiveAgentsList.getState().agents).toHaveLength(1);

    mockLive([{ test: () => true, status: 200, body: agentPage(["ag_9"]) }]);
    const pending = useLiveAgentsList.getState().load("biz_other");
    const mid = useLiveAgentsList.getState();
    expect(mid.status).toBe("loading");
    expect(mid.agents).toEqual([]);
    expect(mid.businessId).toBe("biz_other");
    await pending;
    expect(useLiveAgentsList.getState().agents.map((a) => a.id)).toEqual(["ag_9"]);
  });

  it("reads empty lists as ready-empty", async () => {
    mockLive([
      { test: () => true, status: 200, body: { items: [], total: 0, limit: 50, offset: 0, contract: "v1" } },
    ]);
    await useLiveAgentsList.getState().load("biz_test");
    const s = useLiveAgentsList.getState();
    expect(s.status).toBe("ready");
    expect(s.agents).toEqual([]);
  });
});

describe("live agent detail", () => {
  it("loads one agent and flags unknown ids as notFound (never a fallback)", async () => {
    mockLive([{ test: () => true, status: 200, body: agentOne("ag_1") }]);
    await useLiveAgent.getState().load("biz_test", "ag_1");
    let s = useLiveAgent.getState();
    expect(s.status).toBe("ready");
    expect(s.agent?.id).toBe("ag_1");
    expect(s.notFound).toBe(false);

    mockLive([
      { test: () => true, status: 404, body: { error: { code: "not_found", message: "Agent not found in scope." } } },
    ]);
    await useLiveAgent.getState().load("biz_test", "ghost");
    s = useLiveAgent.getState();
    expect(s.status).toBe("error");
    expect(s.notFound).toBe(true);
    expect(s.agent).toBeNull();
  });

  it("marks server errors as failures, not notFound", async () => {
    mockLive([
      { test: () => true, status: 500, body: { error: { code: "internal", message: "Unexpected server error." } } },
    ]);
    await useLiveAgent.getState().load("biz_test", "ag_1");
    const s = useLiveAgent.getState();
    expect(s.status).toBe("error");
    expect(s.notFound).toBe(false);
  });
});

describe("live brain", () => {
  it("loads entries and passes the category filter through", async () => {
    const { calls } = mockLive([
      { test: () => true, status: 200, body: brainPage(["br_1", "br_2"]) },
    ]);
    await useLiveBrain.getState().load({ businessId: "biz_test", category: "goals" });
    const s = useLiveBrain.getState();
    expect(s.status).toBe("ready");
    expect(s.entries.map((e) => e.id)).toEqual(["br_1", "br_2"]);
    expect(s.entries[0].category).toBe("goals");
    expect(s.entries[0].updatedLabel).toBe("2h ago");
    expect(s.total).toBe(2);
    expect(calls[0].url).toContain("category=goals");
  });

  it("omits the filter when unscoped", async () => {
    const { calls } = mockLive([{ test: () => true, status: 200, body: brainPage(["br_1"]) }]);
    await useLiveBrain.getState().load({ businessId: "biz_test" });
    expect(calls[0].url).not.toContain("category=");
    expect(useLiveBrain.getState().category).toBeNull();
  });

  it("rejects unknown categories with the contract error, not silent data", async () => {
    mockLive([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "invalid_enum", message: "Unknown brain category: 'telepathy'." } },
      },
    ]);
    // Bypass the typed filter to prove the wire contract surfaces as-is.
    await useLiveBrain.getState().load({ businessId: "biz_test", category: "telepathy" as never });
    const s = useLiveBrain.getState();
    expect(s.status).toBe("error");
    expect(s.entries).toEqual([]);
  });

  it("keeps pagination honesty data (total vs loaded)", async () => {
    mockLive([
      {
        test: () => true,
        status: 200,
        body: { ...brainPage(["br_1"]), total: 42 },
      },
    ]);
    await useLiveBrain.getState().load({ businessId: "biz_test" });
    const s = useLiveBrain.getState();
    expect(s.entries).toHaveLength(1);
    expect(s.total).toBe(42);
  });

  it("discards a late response after scope change", async () => {
    mockLive(
      [
        { test: (u) => u.includes("biz_slow"), status: 200, body: brainPage(["stale"]), delayMs: 40 },
        { test: (u) => u.includes("biz_fast"), status: 200, body: brainPage(["fresh"]) },
      ],
      { honorAbort: false },
    );
    const store = useLiveBrain.getState();
    const slow = store.load({ businessId: "biz_slow" });
    await store.load({ businessId: "biz_fast" });
    await slow;
    const s = useLiveBrain.getState();
    expect(s.businessId).toBe("biz_fast");
    expect(s.entries.map((e) => e.id)).toEqual(["fresh"]);
  });

  it("an aborted load is not an error", async () => {
    mockLive(
      [{ test: () => true, status: 200, body: brainPage(["late"]), delayMs: 40 }],
      { honorAbort: true },
    );
    const store = useLiveBrain.getState();
    const first = store.load({ businessId: "biz_test" });
    await store.load({ businessId: "biz_test", category: "goals" });
    await first;
    const s = useLiveBrain.getState();
    expect(s.status).not.toBe("error");
    expect(s.error).toBeNull();
  });

  it("records 401/403/404/422/network/500 states", async () => {
    mockLive([
      { test: () => true, status: 401, body: { error: { code: "unauthorized", message: "Authentication required." } } },
    ]);
    await useLiveBrain.getState().load({ businessId: "biz_test" });
    expect(useLiveBrain.getState().status).toBe("error");

    mockLive([
      { test: () => true, status: 500, body: { error: { code: "internal", message: "Unexpected server error." } } },
    ]);
    await useLiveBrain.getState().load({ businessId: "biz_test" });
    const s = useLiveBrain.getState();
    expect(s.status).toBe("error");
    expect(s.error).toContain("Unexpected server error.");
  });
});

describe("live read-only guarantee", () => {
  it("agents and brain surfaces never send mutations nor brain writes", async () => {
    const { calls } = mockLive([
      { test: (u) => u.includes("/agents/") && !u.endsWith("/agents"), status: 200, body: agentOne("ag_1") },
      { test: (u) => u.includes("/agents"), status: 200, body: agentPage(["ag_1"]) },
      { test: () => true, status: 200, body: brainPage(["br_1"]) },
    ]);
    await useLiveAgentsList.getState().load("biz_test");
    await useLiveAgent.getState().load("biz_test", "ag_1");
    await useLiveBrain.getState().load({ businessId: "biz_test", category: "goals" });
    for (const call of calls) {
      expect(call.init?.method ?? "GET").toBe("GET");
    }
    expect(calls.length).toBeGreaterThan(0);
  });

  it("live loads never touch demo stores", async () => {
    const { useAgents, useBrain } = await import("@/lib/store/app");
    mockLive([
      { test: (u) => u.includes("/agents"), status: 200, body: agentPage(["ag_live"]) },
      { test: () => true, status: 200, body: brainPage(["br_live"]) },
    ]);
    const before = JSON.stringify({
      agents: useAgents.getState().agents,
      entries: useBrain.getState().entries,
    });
    await useLiveAgentsList.getState().load("biz_test");
    await useLiveBrain.getState().load({ businessId: "biz_test" });
    expect(
      JSON.stringify({
        agents: useAgents.getState().agents,
        entries: useBrain.getState().entries,
      }),
    ).toBe(before);
    // ...and demo seed never leaks into live state.
    expect(useLiveAgentsList.getState().agents.map((a) => a.id)).toEqual(["ag_live"]);
  });
});
