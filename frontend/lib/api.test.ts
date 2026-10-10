import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, onUnauthorized, setCsrfToken } from "@/lib/api";

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

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  onUnauthorized(null);
});

describe("api client", () => {
  it("sends credentials, contract header, and CSRF only on mutations", async () => {
    const calls = mockFetchOnce(() => jsonResponse(200, { ok: true }));
    setCsrfToken("csrf-123");
    await api("/api/v1/auth/me");
    expect(calls[0].init?.credentials).toBe("include");
    expect((calls[0].init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();

    await api("/api/v1/auth/logout", { method: "POST" });
    expect((calls[1].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-123");
  });

  it("parses the contract error envelope", async () => {
    mockFetchOnce(() =>
      jsonResponse(401, { error: { code: "unauthorized", message: "Nope", trace_id: "x" } }),
    );
    const err = await api("/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).code).toBe("unauthorized");
  });

  it("fires the unauthorized hook and clears CSRF on 401", async () => {
    mockFetchOnce(() => jsonResponse(401, { error: { code: "unauthorized", message: "x" } }));
    const seen: string[] = [];
    onUnauthorized(() => seen.push("fired"));
    setCsrfToken("csrf-123");
    await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toEqual(["fired"]);
    // CSRF cleared: next mutation sends no token.
    const calls = mockFetchOnce(() => jsonResponse(200, {}));
    await api("/y", { method: "POST" });
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("maps network failure to network_unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const err = await api("/x").catch((e) => e);
    expect((err as ApiError).code).toBe("network_unreachable");
    expect((err as ApiError).status).toBe(0);
  });
});
