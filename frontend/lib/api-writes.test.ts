import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiActivateAgent,
  apiCreateBrain,
  apiDisableAgent,
  apiPatchProject,
  apiPatchTask,
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

function headersOf(calls: { init?: RequestInit }[], index = 0) {
  return calls[index].init?.headers as Record<string, string>;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("management writes (Stage 7.1)", () => {
  it("apiCreateBrain POSTs the entry with contract header and CSRF", async () => {
    const calls = mockFetchOnce(() =>
      jsonResponse(201, {
        id: "br_9",
        businessId: "biz_test",
        category: "goals",
        title: "Ship it",
        body: "Details",
        updatedLabel: "",
        contract: "v1",
      }),
    );
    setCsrfToken("csrf-123");
    const created = await apiCreateBrain("biz_test", {
      category: "goals",
      title: "Ship it",
      body: "Details",
    });
    expect(created.id).toBe("br_9");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://127.0.0.1:8000/api/v1/businesses/biz_test/brain");
    expect(calls[0].init?.method).toBe("POST");
    expect(headersOf(calls)["X-Client-Contract"]).toBe("1");
    expect(headersOf(calls)["X-CSRF-Token"]).toBe("csrf-123");
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      category: "goals",
      title: "Ship it",
      body: "Details",
    });
  });

  it("apiPatchProject PATCHes metadata with CSRF", async () => {
    const calls = mockFetchOnce(() =>
      jsonResponse(200, {
        id: "proj_1",
        businessId: "biz_test",
        title: "New title",
        contract: "v1",
      }),
    );
    setCsrfToken("csrf-123");
    const patched = await apiPatchProject("biz_test", "proj_1", {
      title: "New title",
      status: "review",
    });
    expect(patched.title).toBe("New title");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(calls[0].url).toBe("http://127.0.0.1:8000/api/v1/businesses/biz_test/projects/proj_1");
    expect(headersOf(calls)["X-CSRF-Token"]).toBe("csrf-123");
  });

  it("apiPatchTask PATCHes metadata with CSRF", async () => {
    const calls = mockFetchOnce(() =>
      jsonResponse(200, {
        id: "t_1",
        businessId: "biz_test",
        title: "Retitled",
        contract: "v1",
      }),
    );
    setCsrfToken("csrf-123");
    const patched = await apiPatchTask("biz_test", "proj_1", "t_1", {
      title: "Retitled",
      etaMinutes: 45,
    });
    expect(patched.title).toBe("Retitled");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/projects/proj_1/tasks/t_1",
    );
    expect(headersOf(calls)["X-CSRF-Token"]).toBe("csrf-123");
  });

  it("surfaces the 422 envelope for rejected writes", async () => {
    mockFetchOnce(() =>
      jsonResponse(422, { error: { code: "invalid_enum", message: "Bad status", trace_id: "x" } }),
    );
    setCsrfToken("csrf-123");
    const err = await apiPatchProject("biz_test", "proj_1", { status: "teleporting" }).catch(
      (e) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("invalid_enum");
  });

  it("mutations without a CSRF token send no token header", async () => {
    const calls = mockFetchOnce(() => jsonResponse(200, { id: "t_1", title: "T" }));
    await apiPatchTask("biz_test", "proj_1", "t_1", { title: "T" });
    expect(headersOf(calls)["X-CSRF-Token"]).toBeUndefined();
  });
});

describe("agent lifecycle (Stage 14)", () => {
  it("apiActivateAgent POSTs the activate path with CSRF", async () => {
    const calls = mockFetchOnce(() =>
      jsonResponse(200, { id: "ag_1", businessId: "biz_test", hired: true, contract: "v1" }),
    );
    setCsrfToken("csrf-123");
    const agent = await apiActivateAgent("biz_test", "ag_1");
    expect(agent.hired).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/agents/ag_1/activate",
    );
    expect(calls[0].init?.method).toBe("POST");
    expect(headersOf(calls)["X-CSRF-Token"]).toBe("csrf-123");
    expect(headersOf(calls)["X-Client-Contract"]).toBe("1");
  });

  it("apiDisableAgent POSTs the disable path", async () => {
    const calls = mockFetchOnce(() =>
      jsonResponse(200, { id: "ag_1", businessId: "biz_test", hired: false, contract: "v1" }),
    );
    setCsrfToken("csrf-123");
    const agent = await apiDisableAgent("biz_test", "ag_1");
    expect(agent.hired).toBe(false);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/agents/ag_1/disable",
    );
    expect(calls[0].init?.method).toBe("POST");
  });
});
