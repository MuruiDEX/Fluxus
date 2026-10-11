/**
 * Session + business-context store tests (Stage 7.2).
 *
 * Drives the real `useSession` store with a mocked fetch backend. Covers:
 * session load, anonymous/login routing state, empty/single/multi business
 * contexts, invalid-id rejection, 401 clearing, network error + retry,
 * logout cleanup, request dedupe (no fan-out on re-init), and lazy business
 * names. DEMO-journey behavior (unreachable stays usable) is pinned here;
 * the full DEMO flow remains covered by the Playwright journey spec.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSession } from "@/lib/store/auth";

const ME_SINGLE = {
  user: { id: "u1", email: "op@example.com", displayName: "Op" },
  businesses: ["biz_1"],
  contract: "v1",
};

const ME_MULTI = {
  user: { id: "u1", email: "op@example.com", displayName: "Op" },
  businesses: ["biz_1", "biz_2"],
  contract: "v1",
};

function businessBody(id: string, name: string) {
  return {
    id,
    name,
    tagline: "",
    industry: "",
    location: "",
    ownerName: "",
    healthScore: 0,
    healthBreakdown: [],
    metrics: {},
    revenueSeries: [],
    contract: "v1",
  };
}

interface FetchTap {
  calls: { url: string; init?: RequestInit }[];
  meBody: unknown;
  meStatus: number;
  loginBody: unknown;
  online: boolean;
}

function mockBackend(): FetchTap {
  const tap: FetchTap = {
    calls: [],
    meBody: ME_SINGLE,
    meStatus: 200,
    loginBody: ME_SINGLE,
    online: true,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      tap.calls.push({ url, init });
      if (!tap.online) throw new TypeError("fetch failed");
      if (url.endsWith("/auth/login")) {
        return new Response(JSON.stringify(tap.loginBody), { status: 200 });
      }
      if (url.endsWith("/auth/logout")) {
        return new Response(JSON.stringify({ contract: "v1" }), { status: 200 });
      }
      if (url.endsWith("/auth/me")) {
        return new Response(JSON.stringify(tap.meBody), { status: tap.meStatus });
      }
      const match = url.match(/\/businesses\/([^/?]+)$/);
      if (match) {
        const id = decodeURIComponent(match[1]);
        return new Response(JSON.stringify(businessBody(id, `Name of ${id}`)), {
          status: 200,
        });
      }
      return new Response(JSON.stringify({ error: { code: "x", message: "x" } }), {
        status: 500,
      });
    }),
  );
  return tap;
}

function resetSession() {
  useSession.setState({
    status: "unknown",
    user: null,
    businesses: [],
    currentBusinessId: null,
    businessNames: {},
    refreshing: false,
    error: null,
    sessionError: null,
  });
}

function meCalls(tap: FetchTap): number {
  return tap.calls.filter((c) => c.url.endsWith("/auth/me")).length;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetSession();
});

describe("session lifecycle", () => {
  it("loads session and auto-selects a single business", async () => {
    mockBackend();
    await useSession.getState().refresh();
    const s = useSession.getState();
    expect(s.status).toBe("authenticated");
    expect(s.user?.email).toBe("op@example.com");
    expect(s.businesses).toEqual(["biz_1"]);
    expect(s.currentBusinessId).toBe("biz_1");
    expect(s.refreshing).toBe(false);
  });

  it("login success selects context, 401 clears to anonymous with error", async () => {
    const tap = mockBackend();
    expect(await useSession.getState().login("op@example.com", "pw")).toBe(true);
    expect(useSession.getState().currentBusinessId).toBe("biz_1");

    tap.meStatus = 401;
    tap.meBody = { error: { code: "unauthorized", message: "Authentication required." } };
    await useSession.getState().refresh();
    const s = useSession.getState();
    expect(s.status).toBe("anonymous");
    expect(s.user).toBeNull();
    expect(s.businesses).toEqual([]);
    expect(s.currentBusinessId).toBeNull();
  });

  it("login 401 keeps error message, login network failure clears identity", async () => {
    const tap = mockBackend();
    tap.loginBody = { error: { code: "unauthorized", message: "Invalid email or password." } };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        tap.calls.push({ url, init });
        if (url.endsWith("/auth/login")) {
          return new Response(JSON.stringify(tap.loginBody), { status: 401 });
        }
        throw new Error("unexpected call");
      }),
    );
    expect(await useSession.getState().login("op@example.com", "bad")).toBe(false);
    let s = useSession.getState();
    expect(s.status).toBe("anonymous");
    expect(s.error).toBe("Invalid email or password.");

    tap.online = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(await useSession.getState().login("op@example.com", "pw")).toBe(false);
    s = useSession.getState();
    expect(s.status).toBe("unreachable");
    expect(s.user).toBeNull();
    expect(s.sessionError).toBe("Backend is not reachable.");
  });

  it("non-401 refresh failure stays usable with sessionError (not a logout)", async () => {
    const tap = mockBackend();
    tap.meStatus = 500;
    tap.meBody = { error: { code: "internal", message: "Unexpected server error." } };
    await useSession.getState().refresh();
    const s = useSession.getState();
    expect(s.status).toBe("unreachable");
    expect(s.sessionError).toBe("Unexpected server error.");
  });

  it("network failure then retry recovers without a second auth system", async () => {
    const tap = mockBackend();
    tap.online = false;
    await useSession.getState().refresh();
    expect(useSession.getState().status).toBe("unreachable");

    tap.online = true;
    await useSession.getState().retry();
    const s = useSession.getState();
    expect(s.status).toBe("authenticated");
    expect(s.currentBusinessId).toBe("biz_1");
    expect(s.sessionError).toBeNull();
  });

  it("logout clears user, businesses, and business context", async () => {
    mockBackend();
    const store = useSession.getState();
    await store.login("op@example.com", "pw");
    expect(useSession.getState().currentBusinessId).toBe("biz_1");
    await useSession.getState().logout();
    const s = useSession.getState();
    expect(s.status).toBe("anonymous");
    expect(s.businesses).toEqual([]);
    expect(s.currentBusinessId).toBeNull();
    expect(s.businessNames).toEqual({});
  });

  it("concurrent refreshes share one request (no fan-out)", async () => {
    const tap = mockBackend();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const origFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/auth/me")) await gate;
        return origFetch(url, init);
      }),
    );
    const s = useSession.getState();
    const first = s.refresh();
    const second = s.refresh();
    release();
    await Promise.all([first, second]);
    expect(meCalls(tap)).toBe(1);
    expect(useSession.getState().status).toBe("authenticated");
  });
});

describe("business context", () => {
  it("empty membership selects nothing and issues no business reads", async () => {
    const tap = mockBackend();
    tap.meBody = { ...ME_SINGLE, businesses: [] };
    await useSession.getState().refresh();
    const s = useSession.getState();
    expect(s.status).toBe("authenticated");
    expect(s.currentBusinessId).toBeNull();

    await useSession.getState().ensureBusinessNames();
    expect(tap.calls.some((c) => /\/businesses\//.test(c.url))).toBe(false);
  });

  it("multiple businesses default to first and switch explicitly", async () => {
    const tap = mockBackend();
    tap.meBody = ME_MULTI;
    await useSession.getState().refresh();
    expect(useSession.getState().currentBusinessId).toBe("biz_1");

    expect(useSession.getState().selectBusiness("biz_2")).toBe(true);
    expect(useSession.getState().currentBusinessId).toBe("biz_2");
    void tap;
  });

  it("rejects unknown business ids without touching state", async () => {
    mockBackend();
    await useSession.getState().refresh();
    expect(useSession.getState().selectBusiness("biz_ghost")).toBe(false);
    expect(useSession.getState().selectBusiness("biz_northstar")).toBe(false);
    expect(useSession.getState().currentBusinessId).toBe("biz_1");
  });

  it("preserves a still-valid selection, drops a lapsed one", async () => {
    const tap = mockBackend();
    tap.meBody = ME_MULTI;
    await useSession.getState().refresh();
    useSession.getState().selectBusiness("biz_2");

    await useSession.getState().refresh();
    expect(useSession.getState().currentBusinessId).toBe("biz_2");

    tap.meBody = ME_SINGLE;
    await useSession.getState().refresh();
    expect(useSession.getState().currentBusinessId).toBe("biz_1");
  });

  it("loads business names lazily and caches them", async () => {
    const tap = mockBackend();
    tap.meBody = ME_MULTI;
    await useSession.getState().refresh();
    await useSession.getState().ensureBusinessNames();
    expect(useSession.getState().businessNames).toEqual({
      biz_1: "Name of biz_1",
      biz_2: "Name of biz_2",
    });

    const before = tap.calls.length;
    await useSession.getState().ensureBusinessNames();
    expect(tap.calls.length).toBe(before);
  });

  it("markAnonymous clears the business context (401 hook path)", async () => {
    mockBackend();
    await useSession.getState().refresh();
    useSession.getState().markAnonymous();
    const s = useSession.getState();
    expect(s.status).toBe("anonymous");
    expect(s.currentBusinessId).toBeNull();
    expect(s.businesses).toEqual([]);
  });
});
