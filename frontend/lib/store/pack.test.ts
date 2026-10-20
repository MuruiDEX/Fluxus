/**
 * LIVE pack install tests (Stage 15) — mocked fetch, no network.
 *
 * Covers: DEMO ban before any request, exact install POST shape (empty body),
 * success sync (team + packs reload, no optimistic install), idempotent
 * already_installed, 409 version conflict, single flight, describePackInstall
 * mapping, and closed-state static markup.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setCsrfToken } from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import { useLiveAgents, useLiveAgentsList, useLiveMarketplace, useLivePacks } from "@/lib/store/live";
import { useLiveWrites } from "@/lib/store/writes";
import { PackInstallButton } from "@/components/system/HireButton";
import type { Pack } from "@/lib/domain/types";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
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

function packObj(installed = false): Pack {
  return {
    slug: "fitness-pack",
    version: "1.0.0",
    name: "Fitness Business Pack",
    description: "Installs a sales and support team plus onboarding.",
    blurb: "Complete AI team.",
    price: "$99/month",
    priceNote: "one-click install",
    rating: 4.9,
    installs: "2.1k",
    installed,
    installedVersion: installed ? "1.0.0" : null,
    installedAt: installed ? "2026-10-19T00:00:00Z" : null,
    agents: [
      {
        slug: "sales-manager",
        name: "AI Sales Manager",
        role: "Sales",
        capabilities: ["CRM"],
        agentId: installed ? "ag_sales" : null,
        action: installed ? "reuse" : "create",
      },
      {
        slug: "support-ai",
        name: "Customer Support AI",
        role: "Support",
        capabilities: ["Tickets"],
        agentId: installed ? "ag_support" : null,
        action: installed ? "reuse" : "create",
      },
    ],
    workflow: {
      ref: "fitness-client-onboarding",
      title: "Fitness Client Onboarding",
      goal: "Qualify and onboard.",
      projectId: installed ? "proj_pack" : null,
      tasks: [
        { key: "qualify", title: "Qualify new lead", ownerAgentSlug: "sales-manager", dependsOn: [] },
        {
          key: "book",
          title: "Book onboarding session",
          ownerAgentSlug: "support-ai",
          dependsOn: ["qualify"],
        },
      ],
    },
  };
}

function packJson(installed = false) {
  return { ...packObj(installed), contract: "v1" };
}

function installJson(outcome: "installed" | "already_installed" = "installed") {
  const installed = true;
  return {
    outcome,
    pack: packJson(installed),
    installationId: "pi_1",
    installedAt: "2026-10-19T00:00:00Z",
    agents: packObj(installed).agents,
    project: {
      id: "proj_pack",
      businessId: "biz_test",
      title: "Fitness Client Onboarding",
      goal: "Qualify and onboard.",
      status: "planning",
      progress: 0,
      budget: "0",
      aiBudget: "0",
      aiSpent: "0",
      deadline: "",
      teamIds: ["ag_sales", "ag_support"],
      milestones: [],
      contract: "v1",
    },
    tasks: [],
    contract: "v1",
  };
}

function agentPage(names: string[]) {
  return {
    items: names.map((name, i) => ({
      id: `ag_${i}`,
      businessId: "biz_test",
      name,
      role: "Agent",
      responsibilities: [],
      tools: [],
      permissions: { read: [], write: [], actions: [], denied: [] },
      status: "available",
      currentTask: null,
      costPerMonth: 0,
      performance: {},
      hired: true,
      avatarHue: 1,
      contract: "v1",
    })),
    total: names.length,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function marketplacePage(installed: boolean) {
  return {
    items: [
      {
        slug: "fitness-pack",
        name: "Fitness Business Pack",
        role: "Business pack",
        blurb: "Complete AI team.",
        price: "$99/month",
        priceNote: "one-click install",
        rating: 4.9,
        installs: "2.1k",
        badge: "Popular",
        capabilities: [],
        permissions: { read: [], write: [], actions: [], denied: [] },
        category: "pack",
        hired: false,
        installed,
        installedVersion: installed ? "1.0.0" : null,
        contract: "v1",
      },
    ],
    total: 1,
    limit: 200,
    offset: 0,
    contract: "v1",
  };
}

function packPage(installed: boolean) {
  return { items: [packJson(installed)], total: 1, limit: 200, offset: 0, contract: "v1" };
}

function seedLoaded() {
  useLiveAgentsList.setState({ status: "ready", businessId: "biz_test", agents: [], total: 0, error: null });
  useLiveMarketplace.setState({
    status: "ready",
    businessId: "biz_test",
    listings: [],
    total: 0,
    error: null,
  });
  useLivePacks.setState({
    status: "ready",
    businessId: "biz_test",
    packs: [packObj(false)],
    total: 1,
    error: null,
  });
  useLiveAgents.setState({ status: "ready", businessId: "biz_test", agents: [], error: null });
}

function packRecord(slug = "fitness-pack") {
  return useLiveWrites.getState().hires[`biz_test/${slug}`];
}

function standardScenarios(installStatus: number, installBody: unknown) {
  return mockFetch((url, init) => {
    if (url.endsWith("/install") && init?.method === "POST") return jsonResponse(installStatus, installBody);
    if (url.includes("/packs")) return jsonResponse(200, packPage(true));
    if (url.includes("/agents")) return jsonResponse(200, agentPage(["AI Sales Manager"]));
    if (url.includes("/marketplace")) return jsonResponse(200, marketplacePage(true));
    return jsonResponse(500, { error: { code: "internal", message: "unstubbed" } });
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  signOut();
  useLiveWrites.setState({ projects: {}, tasks: {}, brains: {}, hires: {}, agents: {} });
  useLiveAgentsList.setState({ status: "idle", businessId: null, agents: [], total: 0, error: null });
  useLiveMarketplace.setState({ status: "idle", businessId: null, listings: [], total: 0, error: null });
  useLivePacks.setState({ status: "idle", businessId: null, packs: [], total: 0, error: null });
  useLiveAgents.setState({ status: "idle", businessId: null, agents: [], error: null });
});

describe("installPack", () => {
  it("refuses DEMO and foreign contexts without any request", async () => {
    const calls = standardScenarios(201, installJson());
    await useLiveWrites.getState().installPack("biz_test", "fitness-pack");
    expect(calls).toHaveLength(0);
    expect(packRecord().errorCode).toBe("unauthorized");

    signIn(["biz_other"]);
    await useLiveWrites.getState().installPack("biz_test", "fitness-pack");
    expect(calls).toHaveLength(0);
    expect(packRecord().errorCode).toBe("forbidden");
  });

  it("POSTs empty body, then syncs team + catalog + packs from server truth", async () => {
    signIn();
    seedLoaded();
    const calls = standardScenarios(201, installJson("installed"));
    await useLiveWrites.getState().installPack("biz_test", "fitness-pack");
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/packs/fitness-pack/install",
    );
    expect(JSON.parse(post?.init?.body as string)).toEqual({});
    const rec = packRecord();
    expect(rec.phase).toBe("saved");
    expect(rec.message).toContain("Fitness Business Pack");
    expect(useLivePacks.getState().packs[0]?.installed).toBe(true);
    expect(useLiveMarketplace.getState().listings[0]?.installed).toBe(true);
    expect(useLiveAgents.getState().status).toBe("idle");
  });

  it("reports already_installed from a 200 without inventing a second install", async () => {
    signIn();
    seedLoaded();
    standardScenarios(200, installJson("already_installed"));
    await useLiveWrites.getState().installPack("biz_test", "fitness-pack");
    const rec = packRecord();
    expect(rec.phase).toBe("saved");
    expect(rec.message).toContain("already installed");
  });

  it("surfaces a 409 version conflict honestly", async () => {
    signIn();
    seedLoaded();
    standardScenarios(409, {
      error: { code: "pack_version_conflict", message: "Installed at 0.9.0." },
    });
    await useLiveWrites.getState().installPack("biz_test", "fitness-pack");
    const rec = packRecord();
    expect(rec.phase).toBe("error");
    expect(rec.errorCode).toBe("pack_version_conflict");
  });

  it("sends exactly one POST under concurrency", async () => {
    signIn();
    seedLoaded();
    const calls = standardScenarios(201, installJson());
    const store = useLiveWrites.getState();
    await Promise.all([
      store.installPack("biz_test", "fitness-pack"),
      store.installPack("biz_test", "fitness-pack"),
    ]);
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(1);
  });
});

describe("describePackInstall", () => {
  it("maps installed/installing/error/saved/idle branches", async () => {
    const { describePackInstall } = await import("@/components/system/HireButton");
    expect(describePackInstall(undefined, true).kind).toBe("installed");
    expect(
      describePackInstall({ phase: "saving", message: null, errorCode: null, updatedAt: 0 }, false).kind,
    ).toBe("installing");
    const err = describePackInstall(
      { phase: "error", message: "Nope.", errorCode: "pack_version_conflict", updatedAt: 0 },
      false,
    );
    expect(err.kind).toBe("error");
    expect(err.message).toBe("Nope.");
    expect(
      describePackInstall({ phase: "saved", message: "Done.", errorCode: null, updatedAt: 0 }, false).message,
    ).toBe("Done.");
    expect(describePackInstall(undefined, false).kind).toBe("idle");
  });
});

describe("PackInstallButton closed static markup", () => {
  it("renders Install for open and Installed for installed, with no requests", () => {
    const open = renderToStaticMarkup(
      createElement(PackInstallButton, {
        businessId: "biz_test",
        slug: "fitness-pack",
        installed: false,
        price: "$99/month",
      }),
    );
    expect(open).toContain("Install Pack");
    const done = renderToStaticMarkup(
      createElement(PackInstallButton, {
        businessId: "biz_test",
        slug: "fitness-pack",
        installed: true,
        price: "$99/month",
      }),
    );
    expect(done).toContain("Installed");
    expect(done).not.toContain("Install Pack");
  });
});
