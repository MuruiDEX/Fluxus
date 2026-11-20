import { describe, expect, it } from "vitest";
import {
  accountInitials,
  localisationCapabilities,
  notificationCapabilities,
  readThemePreference,
  securityCapabilities,
  shortAccountId,
  toggledTheme,
  workspaceSummary,
} from "@/lib/account";

describe("readThemePreference", () => {
  it("returns the three stored preferences", () => {
    expect(readThemePreference("light")).toBe("light");
    expect(readThemePreference("dark")).toBe("dark");
    expect(readThemePreference("system")).toBe("system");
  });

  it("reports unresolved and unknown values as null (no chip appears pressed)", () => {
    expect(readThemePreference(undefined)).toBeNull();
    expect(readThemePreference(null)).toBeNull();
    expect(readThemePreference("")).toBeNull();
    expect(readThemePreference("sepia")).toBeNull();
  });
});

describe("toggledTheme", () => {
  it("toggles away from the palette actually rendering", () => {
    expect(toggledTheme("dark")).toBe("light");
    expect(toggledTheme("light")).toBe("dark");
  });

  it("treats an unresolved palette as light", () => {
    expect(toggledTheme(undefined)).toBe("dark");
  });
});

describe("accountInitials", () => {
  it("uses at most two words of the display name", () => {
    expect(accountInitials("Ada Lovelace Byron", "ada@example.com")).toBe("AL");
    expect(accountInitials("operator", "operator@example.com")).toBe("O");
  });

  it("falls back to the email when no name is stored", () => {
    expect(accountInitials("", "ada@example.com")).toBe("A");
    expect(accountInitials("   ", "ada@example.com")).toBe("A");
    expect(accountInitials(null, null)).toBe("?");
  });

  it("trims surrounding whitespace", () => {
    expect(accountInitials("  grace hopper  ", "g@example.com")).toBe("GH");
  });
});

describe("shortAccountId", () => {
  it("shortens a uuid without hiding which account it is", () => {
    expect(shortAccountId("3f2a1c9e-7b4d-4c11-9f00-88aa11bb22cc")).toBe("3f2a1c9e…");
  });

  it("keeps short ids and replaces empty ones", () => {
    expect(shortAccountId("acct-1")).toBe("acct-1");
    expect(shortAccountId("")).toBe("—");
    expect(shortAccountId(null)).toBe("—");
  });
});

describe("workspaceSummary", () => {
  it("pluralises and guards bad input", () => {
    expect(workspaceSummary(0)).toBe("No workspaces");
    expect(workspaceSummary(1)).toBe("1 workspace");
    expect(workspaceSummary(4)).toBe("4 workspaces");
    expect(workspaceSummary(-2)).toBe("No workspaces");
    expect(workspaceSummary(Number.NaN)).toBe("No workspaces");
  });
});

describe("missing capability metadata", () => {
  const all = [...localisationCapabilities, ...notificationCapabilities, ...securityCapabilities];

  it("documents each unavailable setting with a reason", () => {
    expect(all.length).toBeGreaterThan(0);
    for (const capability of all) {
      expect(capability.label.trim().length).toBeGreaterThan(0);
      expect(capability.detail.trim().length).toBeGreaterThan(20);
    }
  });

  it("keeps labels unique across sections", () => {
    expect(new Set(all.map((capability) => capability.label)).size).toBe(all.length);
  });
});
