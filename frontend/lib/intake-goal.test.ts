import { describe, expect, it } from "vitest";
import {
  GOAL_MAX_LENGTH,
  adoptGoalParam,
  clampGoal,
  goalHref,
  readGoalParam,
} from "@/lib/intake-goal";

/** Read the way the app does: a real URL decoded once by URLSearchParams. */
function fromUrl(url: string) {
  return new URL(url, "http://127.0.0.1:3000").searchParams;
}

describe("readGoalParam", () => {
  it("reads a percent-encoded goal", () => {
    expect(readGoalParam(fromUrl("/projects/new?goal=Launch%20a%20fitness%20studio"))).toBe(
      "Launch a fitness studio",
    );
  });

  it("keeps Cyrillic intact", () => {
    const goal = "Запустить фитнес-студию в Кракове";
    expect(readGoalParam(fromUrl(goalHref(goal)))).toBe(goal);
  });

  it("keeps emoji intact", () => {
    const goal = "Launch 🚀 a studio — target 🎯 100 members";
    expect(readGoalParam(fromUrl(goalHref(goal)))).toBe(goal);
  });

  it("round-trips the reserved characters & ? # +", () => {
    const goal = "Q&A: what? #launch + pricing 1+1";
    const params = fromUrl(goalHref(goal));
    expect(params.getAll("goal")).toHaveLength(1);
    expect(readGoalParam(params)).toBe(goal);
  });

  it("treats a missing parameter as no goal", () => {
    expect(readGoalParam(fromUrl("/projects/new"))).toBe("");
    expect(readGoalParam(fromUrl("/projects/new?brief=b1"))).toBe("");
  });

  it("treats empty and whitespace-only values as no goal", () => {
    expect(readGoalParam(fromUrl("/projects/new?goal="))).toBe("");
    expect(readGoalParam(fromUrl("/projects/new?goal=%20%20%20"))).toBe("");
  });

  it("resolves repeated parameters to the first non-blank value", () => {
    expect(readGoalParam(fromUrl("/projects/new?goal=first&goal=second"))).toBe("first");
    expect(readGoalParam(fromUrl("/projects/new?goal=&goal=second"))).toBe("second");
  });

  it("decodes exactly once", () => {
    // %2520 is the encoded form of the literal text "%20" — not a space.
    expect(readGoalParam(fromUrl("/projects/new?goal=%2520"))).toBe("%20");
  });

  it("survives a very long value by clamping to the backend limit", () => {
    const huge = "a".repeat(5000);
    const value = readGoalParam(fromUrl(goalHref(huge)));
    expect(value).toHaveLength(GOAL_MAX_LENGTH);
    expect(value).toBe("a".repeat(GOAL_MAX_LENGTH));
  });

  it("does not split an emoji at the clamp boundary", () => {
    const value = readGoalParam(fromUrl(goalHref("a".repeat(1999) + "😀😀")));
    expect(Array.from(value)).toHaveLength(GOAL_MAX_LENGTH);
    expect(value).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
  });
});

describe("clampGoal", () => {
  it("leaves values at or below the limit untouched", () => {
    expect(clampGoal("short goal")).toBe("short goal");
    expect(clampGoal("b".repeat(GOAL_MAX_LENGTH))).toBe("b".repeat(GOAL_MAX_LENGTH));
  });
});

describe("goalHref", () => {
  it("produces a single usable parameter", () => {
    const params = fromUrl(goalHref("a & b? #c + d"));
    expect(params.getAll("goal")).toEqual(["a & b? #c + d"]);
  });
});

describe("adoptGoalParam", () => {
  it("keeps the typed text when the parameter is unchanged (rerender)", () => {
    expect(adoptGoalParam("my own words", "seeded goal", "seeded goal")).toEqual({
      seeded: "seeded goal",
      value: "my own words",
    });
  });

  it("adopts a new goal while the field is untouched", () => {
    expect(adoptGoalParam("seeded goal", "seeded goal", "brand new goal")).toEqual({
      seeded: "brand new goal",
      value: "brand new goal",
    });
  });

  it("adopts a new goal into an empty field", () => {
    expect(adoptGoalParam("", "", "first goal")).toEqual({
      seeded: "first goal",
      value: "first goal",
    });
  });

  it("never overwrites text the user edited", () => {
    expect(adoptGoalParam("typed by user", "old seed", "new seed")).toEqual({
      seeded: "new seed",
      value: "typed by user",
    });
  });

  it("adopts a new goal into a field the user emptied (nothing to overwrite)", () => {
    expect(adoptGoalParam("", "old seed", "new seed")).toEqual({
      seeded: "new seed",
      value: "new seed",
    });
  });
});
