import { describe, expect, it } from "vitest";
import { NEW_PROJECT_PATH, newProjectHref } from "./new-project";
import { GOAL_PARAM, GOAL_MAX_LENGTH, readGoalParam } from "./intake-goal";

const base = "http://localhost:3000";

/** Parse a href the way the intake does (URLSearchParams, single decode). */
function params(href: string) {
  return new URL(href, base).searchParams;
}

describe("newProjectHref", () => {
  it("points at the one route both contexts share", () => {
    // LIVE renders the server intake and DEMO the simulated wizard from this
    // same path, so the control must not send LIVE users anywhere else.
    expect(NEW_PROJECT_PATH).toBe("/projects/new");
    expect(newProjectHref()).toBe(NEW_PROJECT_PATH);
    expect(new URL(newProjectHref(), base).pathname).toBe("/projects/new");
  });

  it("carries no action: a bare navigation cannot create a project", () => {
    const url = new URL(newProjectHref(), base);
    expect(url.search).toBe("");
    expect([...url.searchParams.keys()]).toEqual([]);
  });

  it("treats a missing or blank goal as no seed", () => {
    for (const value of [undefined, null, "", "   ", "\n\t "]) {
      expect(newProjectHref(value)).toBe(NEW_PROJECT_PATH);
    }
  });

  it("prefills exactly one goal parameter when a goal is given", () => {
    const href = newProjectHref("Launch a fitness studio");
    expect(href.startsWith("/projects/new?")).toBe(true);
    const search = params(href);
    // Only `goal` — no create/launch flag travels with the navigation.
    expect([...search.keys()]).toEqual([GOAL_PARAM]);
    expect(search.getAll(GOAL_PARAM)).toEqual(["Launch a fitness studio"]);
  });

  it("round-trips unicode, separators and reserved characters once", () => {
    const goal = "Запустить студию 🏋️ в Kraków & Co? 100% + #1";
    const href = newProjectHref(goal);
    expect(href).toContain("%26");
    expect(href).toContain("%23");
    // `#` must be encoded, otherwise everything after it is a fragment.
    expect(href.split("#").length).toBe(1);
    expect(readGoalParam(params(href))).toBe(goal);
  });

  it("keeps the backend goal limit across the link", () => {
    const long = "я".repeat(GOAL_MAX_LENGTH + 3000);
    const read = readGoalParam(params(newProjectHref(long)));
    expect(Array.from(read).length).toBe(GOAL_MAX_LENGTH);
  });

  it("trims outer spaces but keeps the inner text", () => {
    expect(readGoalParam(params(newProjectHref("  Fix onboarding  ")))).toBe("Fix onboarding");
  });

  it("is deterministic: repeated use adds no state or counters", () => {
    const first = newProjectHref("Launch a studio");
    const second = newProjectHref("Launch a studio");
    expect(second).toBe(first);
    expect(newProjectHref()).toBe(newProjectHref());
  });
});
