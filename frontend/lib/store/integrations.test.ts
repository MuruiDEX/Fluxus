/**
 * LIVE integrations tests (Stage 19) — mocked fetch, no network.
 *
 * Covers: typed read shape, provider+connection pairing, membership gate
 * (nothing sent), honest load errors, late-response discard on business
 * switch, connect POST with the write flag and a server-issued URL, rejection
 * of a non-https authorization URL, single flight, 503 setup error, confirmed
 * disconnect removing exactly one row, 403 owner-only error, and network-loss
 * reporting without touching the cache.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiIntegrationConnection, ApiIntegrationProvider } from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import {
  useLiveIntegrations,
  useLiveIntegrationsActions,
} from "@/lib/store/integrations";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function provider(id = "gmail", configured = true): ApiIntegrationProvider {
  return {
    id,
    name: id === "gmail" ? "Gmail" : id,
    description: "Read mail and prepare drafts.",
    docsUrl: "https://developers.google.com/gmail/api/guides",
    readScopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    writeScopes: ["https://www.googleapis.com/auth/gmail.compose"],
    capabilities: [
      { id: `${id}.messages.list`, title: "List messages", risk: "read" },
      { id: `${id}.messages.send`, title: "Send message", risk: "write" },
    ],
    configured,
    missingConfiguration: configured ? [] : ["google_client_id"],
    contract: "v1",
  };
}

function connection(
  id = "ic_1",
  overrides: Partial<ApiIntegrationConnection> = {},
): ApiIntegrationConnection {
  return {
    id,
    provider: "gmail",
    providerName: "Gmail",
    externalAccountId: "ops@example.com",
    displayName: "ops@example.com",
    status: "connected",
    health: "ok",
    scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    capabilities: [
      { id: "gmail.messages.list", title: "List messages", risk: "read", granted: true },
      { id: "gmail.messages.send", title: "Send message", risk: "write", granted: false },
    ],
    expiresAt: "2026-01-01T00:00:00+00:00",
    lastRefreshedAt: "2025-12-31T23:00:00+00:00",
    lastError: "",
    canWrite: false,
    contract: "v1",
    ...overrides,
  };
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

function seed(connections: ApiIntegrationConnection[] = [connection()]) {
  useLiveIntegrations.setState({
    status: "ready",
    businessId: "biz_test",
    providers: [provider()],
    connections,
    error: null,
  });
}

function recordOf(key: string) {
  return useLiveIntegrationsActions.getState().records[key];
}

beforeEach(() => {
  vi.unstubAllGlobals();
  signOut();
  useLiveIntegrations.setState({
    status: "idle",
    businessId: null,
    providers: [],
    connections: [],
    error: null,
  });
  useLiveIntegrationsActions.setState({ records: {} });
});

describe("live integrations load", () => {
  it("loads providers and connections for the selected business", async () => {
    signIn();
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.endsWith("/integrations/providers")) {
          return jsonResponse(200, { providers: [provider()], contract: "v1" });
        }
        return jsonResponse(200, { connections: [connection()], contract: "v1" });
      }),
    );

    await useLiveIntegrations.getState().load("biz_test");

    expect(calls.sort()).toEqual([
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/integrations",
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/integrations/providers",
    ]);
    const state = useLiveIntegrations.getState();
    expect(state.status).toBe("ready");
    expect(state.providers).toHaveLength(1);
    expect(state.providers[0].configured).toBe(true);
    expect(state.connections).toHaveLength(1);
    expect(state.connections[0].canWrite).toBe(false);
  });

  it("sends nothing without an authenticated member session", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await useLiveIntegrations.getState().load("biz_test");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useLiveIntegrations.getState().status).toBe("error");

    signIn(["biz_other"]);
    await useLiveIntegrations.getState().load("biz_test");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces a server error instead of a partial catalog", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/providers")
          ? jsonResponse(200, { providers: [provider()], contract: "v1" })
          : jsonResponse(500, { error: { code: "internal", message: "Server exploded." } }),
      ),
    );

    await useLiveIntegrations.getState().load("biz_test");
    const state = useLiveIntegrations.getState();
    expect(state.status).toBe("error");
    expect(state.error).toBe("Server exploded.");
    expect(state.providers).toEqual([]);
  });

  it("drops a late response after a business switch", async () => {
    signIn(["biz_a", "biz_b"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const isA = url.includes("/biz_a/");
        if (isA) await new Promise((resolve) => setTimeout(resolve, 20));
        if (url.endsWith("/providers")) {
          return jsonResponse(200, {
            providers: [provider(isA ? "provider-a" : "provider-b")],
            contract: "v1",
          });
        }
        return jsonResponse(200, {
          connections: [connection(isA ? "ic_a" : "ic_b", { provider: isA ? "provider-a" : "provider-b" })],
          contract: "v1",
        });
      }),
    );

    const first = useLiveIntegrations.getState().load("biz_a");
    await useLiveIntegrations.getState().load("biz_b");
    await first;

    const state = useLiveIntegrations.getState();
    expect(state.businessId).toBe("biz_b");
    expect(state.providers[0].id).toBe("provider-b");
    expect(state.connections[0].id).toBe("ic_b");
  });

  it("never publishes an in-flight response after a refused load", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        if (url.endsWith("/providers")) {
          return jsonResponse(200, { providers: [provider()], contract: "v1" });
        }
        return jsonResponse(200, { connections: [connection()], contract: "v1" });
      }),
    );

    const first = useLiveIntegrations.getState().load("biz_test");
    signOut();
    await useLiveIntegrations.getState().load("biz_test");
    await first;

    const state = useLiveIntegrations.getState();
    expect(state.status).toBe("error");
    expect(state.providers).toEqual([]);
    expect(state.connections).toEqual([]);
  });
});

describe("integration actions", () => {
  it("connects with the write flag and returns the server authorization URL", async () => {
    signIn();
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, {
          authorizationUrl: "https://accounts.google.com/o/oauth2/v2/auth?state=x",
          provider: "gmail",
          scopes: ["gmail.compose"],
          expiresAt: "2026-01-01T00:00:00+00:00",
          contract: "v1",
        });
      }),
    );

    const url = await useLiveIntegrationsActions.getState().connect("biz_test", "gmail", true);

    expect(url).toBe("https://accounts.google.com/o/oauth2/v2/auth?state=x");
    expect(calls).toHaveLength(1);
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ includeWrites: true });
    expect(recordOf("biz_test/connect/gmail")?.phase).toBe("done");
  });

  it("never navigates to a non-https authorization URL", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, {
          authorizationUrl: "javascript:alert(1)",
          provider: "gmail",
          scopes: [],
          expiresAt: "2026-01-01T00:00:00+00:00",
          contract: "v1",
        }),
      ),
    );

    const url = await useLiveIntegrationsActions.getState().connect("biz_test", "gmail", false);
    expect(url).toBeNull();
    expect(recordOf("biz_test/connect/gmail")?.errorCode).toBe("invalid_authorization_url");
  });

  it("single-flights concurrent connect clicks", async () => {
    signIn();
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return jsonResponse(200, {
          authorizationUrl: "https://accounts.google.com/auth",
          provider: "gmail",
          scopes: [],
          expiresAt: "2026-01-01T00:00:00+00:00",
          contract: "v1",
        });
      }),
    );

    const a = useLiveIntegrationsActions.getState().connect("biz_test", "gmail", false);
    const b = useLiveIntegrationsActions.getState().connect("biz_test", "gmail", false);
    expect(await Promise.all([a, b])).toEqual([
      "https://accounts.google.com/auth",
      "https://accounts.google.com/auth",
    ]);
    expect(calls).toBe(1);
  });

  it("reports missing server configuration honestly", async () => {
    signIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(503, {
          error: { code: "not_configured", message: "Integration not configured." },
        }),
      ),
    );

    const url = await useLiveIntegrationsActions.getState().connect("biz_test", "gmail", false);
    expect(url).toBeNull();
    const record = recordOf("biz_test/connect/gmail");
    expect(record?.phase).toBe("error");
    expect(record?.errorCode).toBe("not_configured");
    expect(record?.message).toContain("not configured");
  });

  it("blocks actions without membership and sends nothing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await useLiveIntegrationsActions.getState().connect("biz_test", "gmail", false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(recordOf("biz_test/connect/gmail")?.errorCode).toBe("unauthorized");

    signIn(["biz_other"]);
    await useLiveIntegrationsActions.getState().disconnect("biz_test", "ic_1");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(recordOf("biz_test/disconnect/ic_1")?.errorCode).toBe("forbidden");
  });

  it("removes exactly one confirmed connection", async () => {
    signIn();
    seed([connection("ic_1"), connection("ic_2")]);
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, { outcome: "disconnected", connectionId: "ic_1", contract: "v1" });
      }),
    );

    await useLiveIntegrationsActions.getState().disconnect("biz_test", "ic_1");

    expect(calls[0].init?.method).toBe("DELETE");
    expect(useLiveIntegrations.getState().connections.map((row) => row.id)).toEqual(["ic_2"]);
    expect(recordOf("biz_test/disconnect/ic_1")?.phase).toBe("done");
  });

  it("keeps the row and surfaces the owner-only rule on 403", async () => {
    signIn();
    seed([connection("ic_1")]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(403, { error: { code: "forbidden", message: "Owner only." } }),
      ),
    );

    await useLiveIntegrationsActions.getState().disconnect("biz_test", "ic_1");

    expect(useLiveIntegrations.getState().connections).toHaveLength(1);
    const record = recordOf("biz_test/disconnect/ic_1");
    expect(record?.phase).toBe("error");
    expect(record?.message).toContain("owner");
  });

  it("reports network loss as unknown and never removes the row", async () => {
    signIn();
    seed([connection("ic_1")]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    await useLiveIntegrationsActions.getState().disconnect("biz_test", "ic_1");

    expect(useLiveIntegrations.getState().connections).toHaveLength(1);
    const record = recordOf("biz_test/disconnect/ic_1");
    expect(record?.phase).toBe("error");
    expect(record?.message).toContain("outcome unknown");
  });
});
