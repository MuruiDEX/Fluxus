import { describe, expect, it } from "vitest";
import { metricState, projectTotals } from "@/lib/live/analytics-metrics";
import type { Project } from "@/lib/domain/types";

describe("metricState", () => {
  it("reports a failed load as unavailable, never as a zero-ready value", () => {
    expect(metricState("error")).toBe("unavailable");
    expect(metricState("error")).not.toBe("ready");
  });

  it("only reports ready once the server answered", () => {
    expect(metricState("ready")).toBe("ready");
    expect(metricState("idle")).toBe("pending");
    expect(metricState("loading")).toBe("pending");
  });
});

describe("projectTotals", () => {
  const project = (id: string, aiBudget: number, aiSpent: number) =>
    ({ id, aiBudget, aiSpent }) as Project;

  it("sums the server rows it was given", () => {
    expect(projectTotals([project("a", 20, 1.5), project("b", 5, 0.5)])).toEqual({
      budget: 25,
      spent: 2,
    });
  });

  it("returns real zeros only for an empty list", () => {
    expect(projectTotals([])).toEqual({ budget: 0, spent: 0 });
  });
});
