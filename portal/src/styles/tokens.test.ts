import { describe, expect, it } from "vitest";
import tokensCss from "./tokens.css?raw";

// DESIGN.md "Brand" table and "Surfaces and materials". Values are binding (AP-15).
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
  "--gray": "#C4C4C4",
  "--bg": "#0b0b0c",
};

const MATERIALS: Record<string, string> = {
  "--glass-bg": "rgba(255,255,255,.055)",
  "--glass-line": "rgba(255,255,255,.12)",
  "--glass-blur": "blur(24px) saturate(140%)",
  "--radius": "22px",
  "--text": "#FCFAF8",
  "--text-muted": "rgba(252,250,248,.58)",
  "--text-faint": "rgba(252,250,248,.38)",
  "--hero-line": "rgba(224,66,255,.45)",
  "--hero-glow": "0 30px 80px -30px rgba(224,66,255,.45)",
};

function rootVars(css: string): Map<string, string> {
  const root = css.match(/:root\s*\{([^}]*)\}/);
  expect(root, ":root block").not.toBeNull();
  const vars = new Map<string, string>();
  for (const m of root![1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    vars.set(m[1], m[2].trim().replace(/\s+/g, " "));
  }
  return vars;
}

const norm = (v: string) => v.replace(/\s+/g, "").toLowerCase();

describe("design tokens (DESIGN.md)", () => {
  const vars = rootVars(tokensCss);

  it.each(Object.entries(BRAND))("brand color %s is %s", (name, value) => {
    expect(norm(vars.get(name) ?? "")).toBe(norm(value));
  });

  it.each(Object.entries(MATERIALS))("material %s is %s", (name, value) => {
    expect(norm(vars.get(name) ?? "")).toBe(norm(value));
  });

  it("uses only Work Sans and Azeret Mono", () => {
    expect(vars.get("--font-sans")).toMatch(/^'Work Sans'/);
    expect(vars.get("--font-mono")).toMatch(/^'Azeret Mono'/);
    const families = [...tokensCss.matchAll(/font-family\s*:\s*([^;]+);/g)].map((m) => m[1]);
    for (const f of families) expect(f).toMatch(/var\(--font-(sans|mono)\)/);
  });

  it("has the two background glows and the paper grain", () => {
    expect(vars.get("--glow-led")).toContain("rgba(127,57,255,.30)");
    expect(vars.get("--glow-fuchsia")).toContain("rgba(224,66,255,.22)");
    expect(vars.get("--grain")).toContain("fractalNoise");
    expect(tokensCss).toMatch(/opacity\s*:\s*\.45/);
    expect(tokensCss).toMatch(/mix-blend-mode\s*:\s*overlay/);
  });

  it("uses no hex colors outside the brand palette", () => {
    const allowed = new Set(
      [...Object.values(BRAND), "#fff", "#ffffff", "#111", "#000"].map((v) => v.toLowerCase()),
    );
    const hexes = [...tokensCss.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toLowerCase());
    expect(hexes.filter((h) => !allowed.has(h))).toEqual([]);
  });

  it("respects prefers-reduced-motion", () => {
    expect(tokensCss).toContain("prefers-reduced-motion");
  });
});
