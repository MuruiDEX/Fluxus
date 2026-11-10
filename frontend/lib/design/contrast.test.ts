import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Token-level contrast guard. It resolves the semantic variables exactly as
 * the browser would for the pairs we actually ship: text on a surface, action
 * labels on a brand fill, status text on a status background, and the focus
 * ring against a surface.
 *
 * This is deliberately a CSS-source test, not a browser test: it catches a
 * bad token value before any component consumes it.
 */

const css = readFileSync(path.join(process.cwd(), "app/globals.css"), "utf8");

function block(start: string, end?: string): string {
  const from = css.indexOf(start);
  if (from === -1) return "";
  const to = end ? css.indexOf(end, from) : -1;
  return css.slice(from, to === -1 ? css.length : to);
}

type Tokens = Map<string, string>;

function parseTokens(source: string): Tokens {
  const tokens: Tokens = new Map();
  for (const match of source.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    tokens.set(match[1], match[2].trim());
  }
  return tokens;
}

function resolve(name: string, tokens: Tokens, seen = new Set<string>()): string {
  if (seen.has(name)) throw new Error(`Circular token reference: ${name}`);
  const raw = tokens.get(name);
  if (!raw) throw new Error(`Missing token: --${name}`);
  const reference = raw.match(/^var\(--([\w-]+)\)$/);
  if (!reference) return raw;
  seen.add(name);
  return resolve(reference[1], tokens, seen);
}

function hexToRgb(value: string): [number, number, number] {
  const hex = value.trim().replace(/^#/, "");
  const expanded =
    hex.length === 3
      ? hex
          .split("")
          .map((char) => char + char)
          .join("")
      : hex;
  if (!/^[0-9a-f]{6}$/i.test(expanded)) {
    throw new Error(`Expected an opaque hex color, received: ${value}`);
  }
  return [
    Number.parseInt(expanded.slice(0, 2), 16),
    Number.parseInt(expanded.slice(2, 4), 16),
    Number.parseInt(expanded.slice(4, 6), 16),
  ];
}

function luminance([r, g, b]: [number, number, number]): number {
  const channel = (value: number) => {
    const srgb = value / 255;
    return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(foreground: string, background: string): number {
  const light = Math.max(luminance(hexToRgb(foreground)), luminance(hexToRgb(background)));
  const dark = Math.min(luminance(hexToRgb(foreground)), luminance(hexToRgb(background)));
  return (light + 0.05) / (dark + 0.05);
}

const light = parseTokens(block(":root {", "\n.dark {"));
const dark = new Map([...light, ...parseTokens(block("\n.dark {"))]);

function expectPair(
  tokens: Tokens,
  theme: string,
  foreground: string,
  background: string,
  minimum: number,
) {
  const value = contrast(resolve(foreground, tokens), resolve(background, tokens));
  expect(
    Number(value.toFixed(2)),
    `${theme}: --${foreground} on --${background} is ${value.toFixed(2)}:1`,
  ).toBeGreaterThanOrEqual(minimum);
}

describe("semantic token parity", () => {
  // Shell geometry is theme-independent and intentionally declared once.
  const geometryRoles = new Set(["flux-sidebar-w", "flux-topbar-h", "flux-page-max"]);
  const colorRoles = (tokens: Tokens) =>
    [...tokens.keys()]
      .filter((key) => key.startsWith("flux-") && !geometryRoles.has(key))
      .sort();

  it("defines the same --flux-* color roles in light and dark", () => {
    const darkBlockTokens = parseTokens(block("\n.dark {"));
    expect(colorRoles(darkBlockTokens)).toEqual(colorRoles(light));
  });
});

describe("foreground and background relationships", () => {
  it("keeps the body aliases tied to the semantic tokens", () => {
    expect(resolve("background", light)).toBe(resolve("flux-canvas", light));
    expect(resolve("foreground", light)).toBe(resolve("flux-fg", light));
    expect(resolve("background", dark)).toBe(resolve("flux-canvas", dark));
    expect(resolve("foreground", dark)).toBe(resolve("flux-fg", dark));
  });
});

describe("WCAG contrast", () => {
  const bodyPairs: [string, string, number][] = [
    ["flux-fg", "flux-surface", 4.5],
    ["flux-fg", "flux-canvas", 4.5],
    ["flux-fg-muted", "flux-surface", 4.5],
    ["flux-fg-muted", "flux-canvas", 4.5],
    ["flux-fg-muted", "flux-surface-sunken", 4.5],
    ["flux-fg-subtle", "flux-surface", 4.5],
    ["flux-fg-subtle", "flux-canvas", 4.5],
    ["flux-fg-subtle", "flux-surface-sunken", 4.5],
    ["flux-brand", "flux-surface", 4.5],
    ["flux-brand", "flux-brand-soft", 4.5],
    ["flux-on-brand", "flux-brand-solid", 4.5],
    ["flux-fg-inverse", "flux-surface-inverse", 4.5],
  ];

  const statusPairs: [string, string, number][] = [
    ["flux-success-fg", "flux-success-bg", 4.5],
    ["flux-warning-fg", "flux-warning-bg", 4.5],
    ["flux-danger-fg", "flux-danger-bg", 4.5],
    ["flux-info-fg", "flux-info-bg", 4.5],
  ];

  it("passes body and action pairs in light mode", () => {
    for (const [fg, bg, minimum] of [...bodyPairs, ...statusPairs]) {
      expectPair(light, "light", fg, bg, minimum);
    }
  });

  it("passes body and action pairs in dark mode", () => {
    for (const [fg, bg, minimum] of [...bodyPairs, ...statusPairs]) {
      expectPair(dark, "dark", fg, bg, minimum);
    }
  });

  it("keeps the focus ring distinguishable from both surfaces", () => {
    expectPair(light, "light", "flux-focus", "flux-surface", 3);
    expectPair(light, "light", "flux-focus", "flux-canvas", 3);
    expectPair(dark, "dark", "flux-focus", "flux-surface", 3);
    expectPair(dark, "dark", "flux-focus", "flux-canvas", 3);
  });
});
