import { describe, expect, it } from "vitest";
import { projectStatusView } from "@/lib/project-status";

describe("projectStatusView", () => {
  it("labels every known status from the row instead of a hardcoded default", () => {
    expect(projectStatusView("planning").label).toBe("Planning");
    expect(projectStatusView("in_progress").label).toBe("In Progress");
    expect(projectStatusView("review").label).toBe("In Review");
    expect(projectStatusView("completed").label).toBe("Completed");
  });

  it("never reports a completed project as In Progress", () => {
    expect(projectStatusView("completed").label).not.toBe("In Progress");
    expect(projectStatusView("completed").tone).toBe("done");
  });

  it("shows an unknown wire value verbatim rather than guessing", () => {
    expect(projectStatusView("archived")).toEqual({ label: "archived", tone: "planning" });
    expect(projectStatusView("").label).toBe("Unknown");
  });
});
