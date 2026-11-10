import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const read = (file: string) => readFileSync(path.join(root, file), "utf8");

const css = read("app/globals.css");

const semanticTokens = [
  "canvas",
  "surface",
  "surface-raised",
  "surface-sunken",
  "surface-hover",
  "surface-selected",
  "surface-inverse",
  "overlay",
  "line-subtle",
  "line",
  "line-strong",
  "fg",
  "fg-muted",
  "fg-subtle",
  "fg-inverse",
  "brand",
  "brand-hover",
  "brand-solid",
  "brand-solid-hover",
  "brand-soft",
  "brand-soft-hover",
  "on-brand",
  "success",
  "success-soft",
  "success-fg",
  "success-bg",
  "success-line",
  "warning",
  "warning-soft",
  "warning-fg",
  "warning-bg",
  "warning-line",
  "danger",
  "danger-soft",
  "danger-fg",
  "danger-bg",
  "danger-line",
  "info",
  "info-soft",
  "info-fg",
  "info-bg",
  "info-line",
  "focus",
  "disabled-bg",
  "disabled-fg",
  "disabled-line",
  "selection",
];

const canonicalTypeRoles = [
  "micro",
  "label",
  "caption",
  "body-sm",
  "body",
  "body-lg",
  "heading-sm",
  "heading-md",
  "heading-lg",
  "display",
];

/**
 * Legacy utilities remain valid for existing call sites. Each one delegates to
 * a canonical role, so changing the role updates both names together.
 */
const legacyTypeAliases: Record<string, string> = {
  "micro-sm": "micro",
  "label-sm": "label",
  small: "caption",
  "body-md": "body",
  "heading-xs": "heading-sm",
  "display-sm": "display",
};

/** Slice the stylesheet so light and dark values are checked separately. */
function block(start: string, end?: string): string {
  const from = css.indexOf(start);
  if (from === -1) return "";
  const to = end ? css.indexOf(end, from) : -1;
  return css.slice(from, to === -1 ? css.length : to);
}

const lightBlock = block(":root {", "\n.dark {");
const darkBlock = block("\n.dark {");

describe("semantic design tokens", () => {
  it("declares every semantic color through @theme inline", () => {
    for (const token of semanticTokens) {
      expect(css).toContain(`--color-${token}: var(--flux-${token});`);
    }
  });

  it("defines a light and a dark value for every semantic token", () => {
    for (const token of semanticTokens) {
      expect(lightBlock).toContain(`--flux-${token}:`);
      expect(darkBlock).toContain(`--flux-${token}:`);
    }
  });

  it("keeps the raw palette scales as the source of truth", () => {
    expect(css).toContain("--color-ink-950: #0f1e3d;");
    expect(css).toContain("--color-ink-500: #5b6b8c;");
    expect(css).toContain("--color-mist-200: #e3eaf6;");
    expect(css).toContain("--color-brand-500: #2563eb;");
    expect(css).toContain("--color-night-950: #070d24;");
  });

  it("keeps shell geometry in one place and consumes the variables", () => {
    expect(lightBlock).toContain("--flux-sidebar-w: 232px;");
    expect(lightBlock).toContain("--flux-topbar-h: 60px;");
    expect(lightBlock).toContain("--flux-page-max: 1440px;");

    expect(read("app/(app)/layout.tsx")).toContain("max-w-(--flux-page-max)");
    expect(read("components/system/Sidebar.tsx")).toContain("w-(--flux-sidebar-w)");
    expect(read("components/system/TopBar.tsx")).toContain("h-(--flux-topbar-h)");
  });

  it("gives the radius tokens real consumers in shared primitives", () => {
    const primitives = read("components/system/ui.tsx");
    expect(primitives).toContain("rounded-card");
    // Button and form-chrome radius live in the shared controls module.
    expect(read("components/system/controls.tsx")).toContain("rounded-control");
    // The old panel radius had no consumer and was removed in Slice 1.
    expect(css).not.toContain("--radius-panel");
  });

  it("resolves body background and foreground through the semantic tokens", () => {
    expect(lightBlock).toContain("--background: var(--flux-canvas);");
    expect(lightBlock).toContain("--foreground: var(--flux-fg);");
    // The old dark override was a second foreground value that drifted from text-fg.
    expect(darkBlock).not.toContain("--foreground: #e8eefc;");
  });

  it("declares a matching color-scheme for each theme", () => {
    expect(lightBlock).toContain("color-scheme: light;");
    expect(darkBlock).toContain("color-scheme: dark;");
  });

  it("connects the global focus ring to the semantic focus token", () => {
    expect(css).toContain("outline: 2px solid var(--flux-focus);");
    expect(css).not.toContain("outline: 2px solid var(--color-brand-500);");
  });

  it("uses the semantic selection token", () => {
    expect(css).toContain("background: var(--flux-selection);");
  });

  it("keeps legacy status aliases pointing at the new status triads", () => {
    for (const status of ["success", "warning", "danger", "info"]) {
      expect(css).toContain(`--flux-${status}: var(--flux-${status}-fg);`);
      expect(css).toContain(`--flux-${status}-soft: var(--flux-${status}-bg);`);
    }
  });
});

describe("typography tokens", () => {
  it("declares every canonical role with an explicit line height", () => {
    for (const role of canonicalTypeRoles) {
      expect(css).toContain(`--text-${role}:`);
      expect(css).toContain(`--text-${role}--line-height:`);
    }
  });

  it("keeps deprecated utilities as aliases to the canonical roles", () => {
    for (const [alias, role] of Object.entries(legacyTypeAliases)) {
      expect(css).toContain(`--text-${alias}: var(--text-${role});`);
      expect(css).toContain(`--text-${alias}--line-height: var(--text-${role}--line-height);`);
    }
  });

  it("keeps body typography on the body token", () => {
    expect(css).toContain("font-size: var(--text-body);");
    expect(css).toContain("line-height: var(--text-body--line-height);");
  });

  it("lets demo and live badges follow the surrounding leading", () => {
    // A unitless title ratio would make the badge inherit 1.4 and collapse the
    // 28px title line; px preserves the pre-token leading for inline children.
    expect(css).toContain("--text-heading-md--line-height: 28px;");
    const primitives = read("components/system/ui.tsx");
    for (const badge of ["DemoBadge", "LiveBadge"]) {
      const start = primitives.indexOf(`export function ${badge}`);
      const end = primitives.indexOf("\nexport function", start + 1);
      expect(start).toBeGreaterThan(-1);
      expect(primitives.slice(start, end)).toContain('lineHeight: "inherit"');
    }
  });

  it("removes arbitrary type sizes from the shared primitives and chrome", () => {
    for (const file of [
      "components/system/ui.tsx",
      "components/system/badges.tsx",
      "components/system/Sidebar.tsx",
      "components/system/TopBar.tsx",
    ]) {
      expect(read(file)).not.toMatch(/text-\[\d/);
    }
  });
});

describe("card and badge primitives", () => {
  it("maps every badge tone onto the semantic status tokens", () => {
    const badges = read("components/system/badges.tsx");
    const start = badges.indexOf("const badgeTones");
    const end = badges.indexOf("const badgeSizes", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);

    const toneBlock = badges.slice(start, end);
    const tonePairs: Record<string, [string, string]> = {
      neutral: ["bg-surface-sunken", "text-fg-muted"],
      success: ["bg-success-soft", "text-success-fg"],
      warning: ["bg-warning-soft", "text-warning-fg"],
      danger: ["bg-danger-soft", "text-danger-fg"],
      info: ["bg-info-soft", "text-info-fg"],
      brand: ["bg-brand-soft", "text-brand"],
    };
    for (const [tone, [surface, ink]] of Object.entries(tonePairs)) {
      expect(toneBlock, tone).toContain(surface);
      expect(toneBlock, tone).toContain(ink);
    }
  });

  it("removes the raw status palette from the badge module", () => {
    expect(read("components/system/badges.tsx")).not.toMatch(
      /\b(?:emerald|violet|blue|amber|orange|cyan|red|slate)-\d{2,3}\b/,
    );
  });

  it("keeps Card on the historical surface with only the accepted variants", () => {
    const ui = read("components/system/ui.tsx");
    expect(ui).toContain('"rounded-card border border-line bg-surface shadow-card"');

    const start = ui.indexOf("const cardVariants");
    const end = ui.indexOf("export function Card", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);

    const variantBlock = ui.slice(start, end);
    expect(variantBlock).toContain("default:");
    expect(variantBlock).toContain("interactive:");
    for (const deferred of ["elevated", "subtle", "outlined"]) {
      expect(variantBlock, deferred).not.toContain(deferred);
    }
  });

  it("keeps variant classes ahead of caller overrides", () => {
    const ui = read("components/system/ui.tsx");
    const cardStart = ui.indexOf("export function Card");
    const callStart = ui.indexOf("cn(", cardStart);
    const callEnd = ui.indexOf(")", callStart);
    const classCall = ui.slice(callStart, callEnd);

    expect(classCall.indexOf("cardVariants[variant]")).toBeGreaterThan(-1);
    expect(classCall.indexOf("cardVariants[variant]")).toBeLessThan(
      classCall.indexOf("className"),
    );
  });
});

/**
 * Phase B: the first page consumers migrated off hand-rolled pills and hover
 * lifts. These guards keep the adoption from silently regressing back to raw
 * palette classes or duplicate transforms.
 */
describe("page consumers adopt the shared primitives", () => {
  const projects = read("app/(app)/projects/page.tsx");
  const agents = read("app/(app)/agents/page.tsx");
  const marketplace = read("app/(app)/marketplace/page.tsx");
  const detail = read("app/(app)/marketplace/[slug]/view.tsx");

  it("moves project status pills onto Badge tones", () => {
    expect(projects).not.toContain("STATUS_CLASS");
    expect(projects).toMatch(/\bBadge\b[^;]*from "@\/components\/system\/badges"/);
    expect(projects).toContain("<Badge tone={STATUS_TONE[status.tone]}");

    const start = projects.indexOf("const STATUS_TONE");
    const end = projects.indexOf("};", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);

    const toneBlock = projects.slice(start, end);
    const tones: Record<string, string> = {
      planning: "neutral",
      active: "brand",
      review: "warning",
      done: "success",
    };
    for (const [status, tone] of Object.entries(tones)) {
      expect(toneBlock, status).toContain(`${status}: "${tone}"`);
    }

    // The label still comes from projectStatusView, never a local literal.
    expect(projects).toContain("{status.label}");
    expect(projects).not.toMatch(/\b(?:slate|amber|emerald)-\d{2,3}\b/);
  });

  it("moves the agent availability pill onto Badge", () => {
    expect(agents).toContain("<Badge tone=\"neutral\" size=\"xs\">");
    expect(agents).toContain("\"MARKETPLACE\"");
    expect(agents).toContain("\"DISABLED\"");
    expect(agents).not.toContain("text-[10.5px]");
    expect(agents).not.toContain("bg-mist-100");
  });

  it("moves both marketplace listing badges onto Badge verbatim", () => {
    expect(marketplace).not.toContain("bg-violet-");
    expect(marketplace).not.toContain("text-[10.5px]");
    // Two listing cards (demo + live) and two badges rendered unchanged.
    expect((marketplace.match(/<Badge tone="brand" size="sm">/g) ?? []).length).toBe(2);
    expect((marketplace.match(/\{l\.badge\}/g) ?? []).length).toBe(2);
    // Filtering still runs on the listing data, not on rendered markup.
    expect(marketplace).toContain("selectListings(listings, tab, q)");
  });

  it("moves only the informational pills in the listing detail", () => {
    expect(detail).not.toContain("bg-mist-100");
    expect((detail.match(/<Badge\b/g) ?? []).length).toBe(4);
    // Pack action states map 1:1 onto the semantic tones, labels unchanged.
    expect(detail).toContain(
      'tone={a.action === "blocked" ? "danger" : a.action === "reuse" ? "info" : "success"}',
    );
    expect(detail).toContain(
      '? "Existing" : a.action === "blocked" ? "Disabled" : "Install"',
    );
    // Action colours were the only palette removed; permission rows stay.
    expect(detail).not.toMatch(/bg-(?:red|blue|emerald)-100\b/);
    expect(detail).toContain("bg-emerald-50");
    expect(detail).toContain("bg-blue-50");
    expect(detail).toContain("bg-red-50");
    // Non-pill notes and the action controls are untouched.
    expect(detail).toContain("Not available for this listing type.");
    expect(detail).toContain("<HireButton");
    expect(detail).toContain("<PackInstallButton");
  });

  it("uses the interactive Card variant without duplicate transforms", () => {
    for (const [file, source, count] of [
      ["projects/page.tsx", projects, 1],
      ["agents/page.tsx", agents, 1],
      ["marketplace/page.tsx", marketplace, 2],
      ["marketplace/[slug]/view.tsx", detail, 0],
    ] as const) {
      expect(source, file).not.toContain("hover:-translate-y-0.5");
      expect((source.match(/variant="interactive"/g) ?? []).length, file).toBe(count);
    }
  });
});
