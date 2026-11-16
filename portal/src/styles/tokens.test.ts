import { describe, expect, it } from "vitest";
import tokensCss from "./tokens.css?raw";

// DESIGN.md "Brand" table (binding, AP-15).
const BRAND: Record<string, string> = {
  "--fuchsia": "#E042FF",
  "--led": "#7F39FF",
  "--off-black": "#1D1D1D",
  "--off-white": "#FCFAF8",
  "--clay": "#EB2A2E",
  "--bakelite": "#FA8100",
  "--phosphor": "#7CD941",
  "--glass-blue": "#00B2FF",
  "--brass": "#F7F300",
};

// DESIGN.md "Themes and materials" (AP-16), values from docs/design/studio/*.html.
const LIGHT: Record<string, string> = {
  "--ground": "#F4F0EA",
  "--text": "#1D1D1D",
  "--text-muted": "rgba(29,29,29,.62)",
  "--text-faint": "rgba(29,29,29,.44)",
  "--panel-bg": "rgba(255,255,255,.58)",
  "--panel-line": "rgba(255,255,255,.9)",
  "--panel-blur": "blur(36px) saturate(170%)",
  "--hero-bg": "rgba(255,255,255,.72)",
  "--orb-opacity": ".55",
  "--horizon-opacity": ".55",
  "--ok": "#2f7a12",
  "--warn": "#a14d00",
  "--bad": "#b3161a",
  "--ai": "#9b14b8",
  "--info": "#00669f",
};

const DARK: Record<string, string> = {
  "--ground": "#0e0d10",
  "--text": "#FCFAF8",
  "--text-muted": "rgba(252,250,248,.66)",
  "--text-faint": "rgba(252,250,248,.44)",
  "--panel-bg": "rgba(30,28,34,.52)",
  "--panel-line": "rgba(255,255,255,.08)",
  "--hero-bg": "rgba(34,31,38,.60)",
  "--island-bg": "rgba(255,255,255,.08)",
  "--orb-opacity": ".42",
  "--ok": "#a6ec78",
  "--warn": "#ffb66a",
  "--bad": "#ff8a8c",
  "--ai": "#f0a6ff",
  "--info": "#7fd6ff",
};

function block(css: string, selector: RegExp): Map<string, string> {
  const m = css.match(selector);
  expect(m, String(selector)).not.toBeNull();
  const vars = new Map<string, string>();
  for (const v of m![1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) vars.set(v[1], v[2].trim().replace(/\s+/g, " "));
  return vars;
}

const norm = (v: string) => v.replace(/\s+/g, "").toLowerCase();

describe("design tokens (DESIGN.md Studio)", () => {
  const root = block(tokensCss, /:root\s*\{([^}]*)\}/);
  const light = block(tokensCss, /:root,\s*\[data-theme="light"\]\s*\{([^}]*)\}/);
  const dark = block(tokensCss, /\[data-theme="dark"\]\s*\{([^}]*)\}/);

  it.each(Object.entries(BRAND))("brand color %s is %s", (name, value) => {
    expect(norm(root.get(name) ?? "")).toBe(norm(value));
  });

  it.each(Object.entries(LIGHT))("light %s is %s", (name, value) => {
    expect(norm(light.get(name) ?? "")).toBe(norm(value));
  });

  it.each(Object.entries(DARK))("dark %s is %s", (name, value) => {
    expect(norm(dark.get(name) ?? "")).toBe(norm(value));
  });

  it("both themes define the same variables (same layout, two materials)", () => {
    expect([...dark.keys()].sort()).toEqual([...light.keys()].sort());
  });

  it("uses the brand material textures: paper and stone in light, vinyl in dark", () => {
    expect(light.get("--ground-tex")).toContain("paper_tile.jpg");
    expect(light.get("--horizon-tex")).toContain("stone_tile.jpg");
    expect(dark.get("--ground-tex")).toContain("vinyl_tile.jpg");
    expect(dark.get("--horizon-tex")).toContain("vinyl_tile.jpg");
  });

  it("uses only Work Sans and Azeret Mono", () => {
    expect(root.get("--font-sans")).toMatch(/^'Work Sans'/);
    expect(root.get("--font-mono")).toMatch(/^'Azeret Mono'/);
    const families = [...tokensCss.matchAll(/font-family\s*:\s*([^;]+);/g)].map((m) => m[1]);
    for (const f of families) expect(f).toMatch(/var\(--font-(sans|mono)\)/);
  });

  it("uses no hex colors outside the brand palette and the Studio theme values", () => {
    const allowed = new Set(
      [
        ...Object.values(BRAND),
        ...Object.values(LIGHT).filter((v) => v.startsWith("#")),
        ...Object.values(DARK).filter((v) => v.startsWith("#")),
        // Mockup tints: copper avatar, bakelite bar top, agent avatar ink, ring gaps.
        "#c8825e", "#a1595a", "#ffb066", "#5a22c9", "#d9c7ff", "#1b1a1f", "#141317", "#0a090c",
        "#fff", "#ffffff",
      ].map((v) => v.toLowerCase()),
    );
    const hexes = [...tokensCss.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toLowerCase());
    expect(hexes.filter((h) => !allowed.has(h))).toEqual([]);
  });

  it("respects prefers-reduced-motion", () => {
    expect(tokensCss).toContain("prefers-reduced-motion");
  });
});
