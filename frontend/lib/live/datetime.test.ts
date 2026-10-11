import { describe, expect, it } from "vitest";
import { formatWhen, isIsoDate } from "@/lib/live/datetime";

const NOW = Date.parse("2026-10-11T12:00:00Z");

describe("client-side timestamp derivation (contract §4)", () => {
  it("formats relative buckets", () => {
    expect(formatWhen("2026-10-11T11:59:30Z", NOW)).toBe("just now");
    expect(formatWhen("2026-10-11T11:55:00Z", NOW)).toBe("5m ago");
    expect(formatWhen("2026-10-11T09:00:00Z", NOW)).toBe("3h ago");
  });

  it("falls back to a UTC calendar date beyond a day", () => {
    expect(formatWhen("2026-05-30T07:00:00Z", NOW)).toBe("30 May 2026");
  });

  it("never invents timestamps for bad input", () => {
    expect(formatWhen("", NOW)).toBe("");
    expect(formatWhen("2h ago", NOW)).toBe("2h ago");
    expect(isIsoDate("")).toBe(false);
    expect(isIsoDate("2h ago")).toBe(false);
    expect(isIsoDate("2026-10-11T12:00:00Z")).toBe(true);
  });
});
