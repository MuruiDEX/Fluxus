import { describe, expect, it } from "vitest";
import { cn } from "@/lib/cn";

describe("cn", () => {
  it("keeps a semantic type size alongside a text colour", () => {
    expect(cn("text-body-sm", "text-ink-700")).toBe("text-body-sm text-ink-700");
    expect(cn("text-caption", "text-emerald-600")).toBe("text-caption text-emerald-600");
    expect(cn("text-label-sm", "text-ink-500")).toBe("text-label-sm text-ink-500");
  });

  it("still resolves conflicting sizes and colours", () => {
    expect(cn("text-caption", "text-body-sm")).toBe("text-body-sm");
    expect(cn("text-ink-500", "text-fg-muted")).toBe("text-fg-muted");
  });
});
