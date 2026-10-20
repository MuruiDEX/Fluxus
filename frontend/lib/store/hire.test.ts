/**
 * LIVE hire tests (Stage 8.3) — mocked fetch, no network.
 *
 * Covers: DEMO ban without any request, exact POST shape (empty body),
 * success sync (team + catalog reload, cache invalidate), 409/422/401/
 * network records, single flight under concurrency, describeHire mapping,
 * and closed-state static markup (no DOM test infra in this repo).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setCsrfToken } from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import {
  useLiveAgents,
  useLiveAgentsList,
  useLiveMarketplace,
} from "@/lib/store/live";
import { useLiveWrites } from "@/lib/store/writes";
import { HireButton } from "@/components/system/HireButton";
import type { MarketplaceAgent } from "@/lib/domain/types";

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

function listingObj(hired = false): MarketplaceAgent {
  return {
    slug: "seo-specialist",
    name: "SEO Specialist AI",
    role: "SEO",
    blurb: "Keywords and audits.",
    price: "$19/month",
    priceNote: "per month",
    rating: 4.8,
    installs: "1.8k",
    badge: "New",
    permissions: { read: ["Web"], write: [], actions: [], denied: [] },
    capabilities: ["Keywords", "Briefs"],
    category: "employee",
    hired,
    installed: false,
    installedVersion: null,
  };
}

function agentJson() {
  return {
    id: "ag_hired_1",
    businessId: "biz_test",
    name: "SEO Specialist AI",
    role: "SEO",
    responsibilities: ["Keywords", "Briefs"],
    tools: [],
    permissions: { read: ["Web"], write: [], actions: [], denied: [] },
    status: "available",
    currentTask: null,
    costPerMonth: 19.0,
    performance: {},
    hired: true,
    avatarHue: 42,
    contract: "v1",
  };
}

function agentPage(names: string[]) {
  return {
    items: names.map((name, i) => ({ ...agentJson(), id: `ag_${i}`, name })),
    total: names.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function listingJson(hired: boolean) {
  return {
    slug: "seo-specialist",
    name: "SEO Specialist AI",
    role: "SEO",
    blurb: "Keywords and audits.",
    price: "$19/month",
    priceNote: "per month",
    rating: 4.8,
    installs: "1.8k",
    badge: "New",
    capabilities: ["Keywords", "Briefs"],
    permissions: { read: ["Web"], write: [], actions: [], denied: [] },
    category: "employee",
    hired,
    installed: false,
    installedVersion: null,
    contract: "v1",
  };
}

function marketplacePage(hired: boolean) {
  return { items: [listingJson(hired)], total: 1, limit: 200, offset: 0, contract: "v1" };
}

function seedLoaded() {
  useLiveAgentsList.setState({
    status: "ready",
    businessId: "biz_test",
    agents: [],
    total: 0,
    error: null,
  });
  useLiveMarketplace.setState({
    status: "ready",
    businessId: "biz_test",
    listings: [listingObj(false)],
    total: 1,
    error: null,
  });
  useLiveAgents.setState({
    status: "ready",
    businessId: "biz_test",
    agents: [],
    error: null,
  });
}

function hireRecord() {
  return useLiveWrites.getState().hires["biz_test/seo-specialist"];
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveWrites.setState({ projects: {}, tasks: {}, brains: {}, hires: {} });
  useLiveAgentsList.setState({ status: "idle", businessId: null, agents: [], total: 0, error: null });
  useLiveMarketplace.setState({
    status: "idle",
    businessId: null,
    listings: [],
    total: 0,
    error: null,
  });
  useLiveAgents.setState({ status: "idle", businessId: null, agents: [], error: null });
});

describe("hireAgent", () => {
  it("refuses DEMO and foreign contexts without any request", async () => {
    const calls = mockFetch([{ test: () => true, status: 201, body: agentJson() }]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    expect(calls).toHaveLength(0);
    expect(hireRecord().errorCode).toBe("unauthorized");

    signIn(["biz_other"]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    expect(calls).toHaveLength(0);
    expect(hireRecord().errorCode).toBe("forbidden");
  });

  it("POSTs empty body and syncs team + catalog from server truth", async () => {
    signIn();
    seedLoaded();
    const calls = mockFetch([
      {
        test: (u, init) => u.endsWith("/hire") && init?.method === "POST",
        status: 201,
        body: agentJson(),
      },
      {
        test: (u, init) => u.includes("/agents") && (init?.method ?? "GET") === "GET",
        status: 200,
        body: agentPage(["SEO Specialist AI"]),
      },
      {
        test: (u, init) => u.includes("/marketplace") && (init?.method ?? "GET") === "GET",
        status: 200,
        body: marketplacePage(true),
      },
    ]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/marketplace/seo-specialist/hire",
    );
    expect(JSON.parse(post?.init?.body as string)).toEqual({});
    const rec = hireRecord();
    expect(rec.phase).toBe("saved");
    expect(rec.message).toContain("SEO Specialist AI");
    // Team list reloaded (never fabricated), catalog marks hired, display
    // cache invalidated so the next ensure() refetches.
    expect(useLiveAgentsList.getState().agents.map((a) => a.id)).toEqual(["ag_0"]);
    expect(useLiveMarketplace.getState().listings[0]?.hired).toBe(true);
    expect(useLiveAgents.getState().status).toBe("idle");
  });

  it("reloads on 409 and reports already_hired honestly", async () => {
    signIn();
    seedLoaded();
    mockFetch([
      {
        test: (u) => u.endsWith("/hire"),
        status: 409,
        body: { error: { code: "already_hired", message: "Already hired." } },
      },
      {
        test: (u) => u.includes("/agents"),
        status: 200,
        body: agentPage(["SEO Specialist AI"]),
      },
      {
        test: (u) => u.includes("/marketplace"),
        status: 200,
        body: marketplacePage(true),
      },
    ]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    const rec = hireRecord();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("already_hired");
    expect(useLiveMarketplace.getState().listings[0]?.hired).toBe(true);
  });

  it("records 422 pack, 401, and network loss without fake success", async () => {
    signIn();
    mockFetch([
      {
        test: () => true,
        status: 422,
        body: { error: { code: "listing_not_hireable", message: "Pack." } },
      },
    ]);
    await useLiveWrites.getState().hireAgent("biz_test", "fitness-pack");
    expect(useLiveWrites.getState().hires["biz_test/fitness-pack"].errorCode).toBe(
      "listing_not_hireable",
    );

    mockFetch([
      { test: () => true, status: 401, body: { error: { code: "unauthorized", message: "Auth." } } },
    ]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    expect(hireRecord().phase).toBe("error");

    // The 401 signed the session out (shared session handler) — sign back in
    // so the network case exercises the transport, not the DEMO ban.
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    const rec = hireRecord();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("network_unreachable");
  });

  it("sends exactly one POST under concurrency", async () => {
    signIn();
    seedLoaded();
    const calls = mockFetch([
      { test: (u) => u.endsWith("/hire"), status: 201, body: agentJson() },
      { test: (u) => u.includes("/agents"), status: 200, body: agentPage(["SEO"]) },
      { test: (u) => u.includes("/marketplace"), status: 200, body: marketplacePage(true) },
    ]);
    const store = useLiveWrites.getState();
    await Promise.all([
      store.hireAgent("biz_test", "seo-specialist"),
      store.hireAgent("biz_test", "seo-specialist"),
    ]);
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(1);
  });

  it("clearHire drops the record", async () => {
    signIn();
    seedLoaded();
    mockFetch([
      { test: (u) => u.endsWith("/hire"), status: 201, body: agentJson() },
      { test: (u) => u.includes("/agents"), status: 200, body: agentPage(["SEO"]) },
      { test: (u) => u.includes("/marketplace"), status: 200, body: marketplacePage(true) },
    ]);
    await useLiveWrites.getState().hireAgent("biz_test", "seo-specialist");
    expect(hireRecord()).toBeDefined();
    useLiveWrites.getState().clearHire("biz_test", "seo-specialist");
    expect(hireRecord()).toBeUndefined();
  });
});

describe("describeHire", () => {
  it("maps hired/saving/error/saved/idle branches", async () => {
    const { describeHire: describe } = await import("@/components/system/HireButton");
    expect(describe(undefined, true).kind).toBe("hired");
    expect(describe({ phase: "saving", message: null, errorCode: null, updatedAt: 0 }, false).kind).toBe(
      "hiring",
    );
    const err = describe(
      { phase: "error", message: "Nope.", errorCode: "already_hired", updatedAt: 0 },
      false,
    );
    expect(err.kind).toBe("error");
    expect(err.showRetry).toBe(true);
    const saved = describe(
      { phase: "saved", message: "Joined.", errorCode: null, updatedAt: 0 },
      false,
    );
    expect(saved.kind).toBe("hired");
    expect(describe(undefined, false).kind).toBe("idle");
  });
});

describe("HireButton closed static markup", () => {
  it("renders Hire for hireable, Hired for hired, without firing requests", () => {
    const open = renderToStaticMarkup(
      createElement(HireButton, {
        businessId: "biz_test",
        slug: "seo-specialist",
        hired: false,
        price: "$19/month",
      }),
    );
    expect(open).toContain("Hire");
    const hired = renderToStaticMarkup(
      createElement(HireButton, {
        businessId: "biz_test",
        slug: "seo-specialist",
        hired: true,
        price: "$19/month",
      }),
    );
    expect(hired).toContain("On your team");
    expect(hired).not.toContain("Hire —");
  });
});

describe("marketplace api client", () => {
  it("lists with paging, gets one listing, hires with POST + CSRF", async () => {
    const { apiGetListing, apiHireAgent, apiListMarketplace } = await import("@/lib/api");
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url.includes("/hire")) return jsonResponse(201, agentJson());
        if (url.includes("/marketplace/seo")) return jsonResponse(200, listingJson(false));
        return jsonResponse(200, marketplacePage(false));
      }),
    );
    setCsrfToken("csrf-123");
    const page = await apiListMarketplace("biz_test", { limit: 10 });
    expect(page.total).toBe(1);
    expect(calls[0].url).toContain("/marketplace?limit=10");
    const one = await apiGetListing("biz_test", "seo-specialist");
    expect(one.slug).toBe("seo-specialist");
    const hired = await apiHireAgent("biz_test", "seo-specialist");
    expect(hired.id).toBe("ag_hired_1");
    const post = calls.find((c) => c.init?.method === "POST");
    expect((post?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-123");
    expect((post?.init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");
  });
});
