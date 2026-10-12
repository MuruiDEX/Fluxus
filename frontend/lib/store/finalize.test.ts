/**
 * Finalization UI tests (Stage 8.5) — mocked fetch, no network.
 *
 * Covers: finalize client call shape, success payload handling, the
 * single-flight guard (double submit = one POST), the pure finalize view
 * mapping (idle/pending/error/done), static report rendering (verified
 * results separated from recommendations, honest costs note), and that
 * rendering alone sends nothing (DEMO safety by construction — no effects
 * fetch on mount).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { apiFinalizeBrief, setCsrfToken, type ApiFinalize } from "@/lib/api";
import {
  FinalizeSection,
  FinalReportView,
  describeFinalize,
  shareFlight,
} from "@/components/system/PlanDriver";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function finalizeJson(): ApiFinalize {
  return {
    briefId: "brief_1",
    projectId: "proj_1",
    status: "completed",
    report: {
      goal: "Grow fast",
      completed_tasks: [{ id: "t_1", title: "Design", summary: "Done: Design" }],
      failed_tasks: [{ id: "t_2", title: "Build", reason: "Ran out of attempts" }],
      limitations: ["Limited sample"],
      recommendations: ["Keep going"],
    },
    learnings: [
      { id: "br_1", category: "results", title: "Small slices ship", body: "Small slices worked." },
    ],
    costs: {
      budget: "1000.00",
      ai_budget: "100.00",
      ai_spent: "0.00",
      note: "Configured ledger values, not measured costs: live pricing is not tracked in this version.",
    },
    contract: "v1",
  };
}

beforeEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("apiFinalizeBrief client", () => {
  it("POSTs the finalize path with CSRF and contract headers", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, finalizeJson());
      }),
    );
    setCsrfToken("csrf-1");
    const res = await apiFinalizeBrief("biz_test", "brief_1");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://127.0.0.1:8000/api/v1/businesses/biz_test/briefs/brief_1/finalize",
    );
    expect(calls[0].init?.method).toBe("POST");
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect((calls[0].init?.headers as Record<string, string>)["X-Client-Contract"]).toBe("1");
    expect(res.report.goal).toBe("Grow fast");
    expect(res.learnings).toHaveLength(1);
  });

  it("surfaces the safe 409 message on ineligible state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(409, {
          error: { code: "conflict", message: "Task t_1 is working, not terminal." },
        }),
      ),
    );
    const err = await apiFinalizeBrief("biz_test", "brief_1").catch((e) => e);
    expect(err.code).toBe("conflict");
    expect(String(err.message)).toContain("not terminal");
  });
});

describe("shareFlight", () => {
  it("shares one flight across concurrent calls and re-arms after settle", async () => {
    let runs = 0;
    const slot: { current: Promise<string> | null } = { current: null };
    const fn = async () => {
      runs += 1;
      await new Promise((r) => setTimeout(r, 5));
      return "ok";
    };
    const [a, b] = await Promise.all([shareFlight(fn, slot), shareFlight(fn, slot)]);
    expect(a).toBe("ok");
    expect(b).toBe("ok");
    expect(runs).toBe(1);
    expect(slot.current).toBeNull();
    await shareFlight(fn, slot);
    expect(runs).toBe(2);
  });

  it("re-arms after rejection", async () => {
    let runs = 0;
    const slot: { current: Promise<string> | null } = { current: null };
    const fail = async () => {
      runs += 1;
      throw new Error("nope");
    };
    await expect(shareFlight(fail, slot)).rejects.toThrow("nope");
    await expect(shareFlight(fail, slot)).rejects.toThrow("nope");
    expect(runs).toBe(2);
  });
});

describe("describeFinalize", () => {
  it("maps idle/pending/error/done to honest controls", () => {
    const idle = describeFinalize({ phase: "idle", result: null, message: null });
    expect(idle.showButton).toBe(true);
    expect(idle.buttonLabel).toBe("Finalize brief");
    const pending = describeFinalize({ phase: "pending", result: null, message: null });
    expect(pending.pending).toBe(true);
    const error = describeFinalize({ phase: "error", result: null, message: "Task t_1 is working." });
    expect(error.showRetry).toBe(true);
    expect(error.buttonLabel).toBe("Retry finalization");
    expect(error.headline).toContain("working");
    const done = describeFinalize({ phase: "done", result: finalizeJson(), message: null });
    expect(done.showButton).toBe(false);
  });
});

describe("FinalizeSection + FinalReportView", () => {
  it("renders the idle action without sending anything", () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return jsonResponse(200, finalizeJson());
      }),
    );
    const html = renderToStaticMarkup(
      createElement(FinalizeSection, { businessId: "biz_test", briefId: "brief_1" }),
    );
    expect(html).toContain("Finalize brief");
    expect(calls).toHaveLength(0);
  });

  it("renders verified results, learnings, and the honesty note statically", () => {
    const html = renderToStaticMarkup(createElement(FinalReportView, { result: finalizeJson() }));
    expect(html).toContain("Completed (1)");
    expect(html).toContain("Design — Done: Design");
    expect(html).toContain("Failed (1)");
    expect(html).toContain("Build — Ran out of attempts");
    expect(html).toContain("Recommendations (not verified results)");
    expect(html).toContain("Learnings saved to Brain (1)");
    expect(html).toContain("Small slices ship");
    expect(html).toContain("configured values, not measured");
    expect(html).toContain("live pricing is not tracked");
  });
});
