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
  "line",
  "line-strong",
  "fg",
  "fg-muted",
  "fg-subtle",
  "brand",
  "brand-hover",
  "brand-soft",
  "success",
  "success-soft",
  "warning",
  "warning-soft",
  "danger",
  "danger-soft",
  "info",
  "info-soft",
  "focus",
  "disabled-bg",
  "disabled-fg",
];

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
    expect(primitives).toContain("rounded-control");
  });
});

describe("typography tokens", () => {
  const typeRoles = [
    "micro",
    "micro-sm",
    "label",
    "label-sm",
    "caption",
    "small",
    "body-sm",
    "body",
    "body-md",
    "body-lg",
    "heading-xs",
    "heading-sm",
    "heading-md",
    "heading-lg",
    "display-sm",
  ];

  it("declares every role with an explicit line height", () => {
    for (const role of typeRoles) {
      expect(css).toContain(`--text-${role}:`);
      expect(css).toContain(`--text-${role}--line-height:`);
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
