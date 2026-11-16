import { describe, expect, it } from "vitest";
import appCss from "./app.css?raw";
import tokensCss from "./tokens.css?raw";

/**
 * WCAG AA for body text in both Studio themes (DESIGN.md "Themes and materials", P5-3).
 * Colors are composited the way the screen draws them: chip tint over the panel glass,
 * panel glass over the ground. Labels (10 px mono caps) keep the DESIGN.md faint ink;
 * body text, chips, and data must reach 4.5:1.
 */

type RGB = [number, number, number];
type RGBA = [number, number, number, number];

function block(selector: RegExp): Map<string, string> {
  const m = tokensCss.match(selector);
  expect(m, String(selector)).not.toBeNull();
  const vars = new Map<string, string>();
  for (const v of m![1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) vars.set(v[1], v[2].trim());
  return vars;
}

function parse(c: string): RGBA {
  const s = c.trim();
  if (s.startsWith("#")) {
    const h = s.slice(1);
    const full = h.length === 3 ? [...h].map((x) => x + x).join("") : h;
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)).concat(1) as RGBA;
  }
  const m = s.match(/rgba?\(([^)]+)\)/);
  if (!m) throw new Error(`not a color: ${c}`);
  const p = m[1].split(",").map((x) => parseFloat(x));
  return [p[0], p[1], p[2], p[3] ?? 1];
}

const over = (fg: RGBA, bg: RGB): RGB => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])) as RGB;

function lum([r, g, b]: RGB): number {
  const f = (c: number) => {
    const x = c / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = {
  light: block(/:root,\s*\[data-theme="light"\]\s*\{([^}]*)\}/),
  dark: block(/\[data-theme="dark"\]\s*\{([^}]*)\}/),
};

function resolve(vars: Map<string, string>, name: string): string {
  const v = vars.get(name);
  expect(v, name).toBeDefined();
  const ref = v!.match(/^var\((--[\w-]+)\)$/);
  return ref ? resolve(vars, ref[1]) : v!;
}

describe.each(Object.entries(THEMES))("%s theme meets WCAG AA for body text", (_theme, vars) => {
  const ground = over(parse(resolve(vars, "--ground")), [0, 0, 0]);
  const panel = over(parse(resolve(vars, "--panel-bg")), ground);
  const hero = over(parse(resolve(vars, "--hero-bg")), ground);

  it.each([
    ["--text", "panel"],
    ["--text-muted", "panel"],
    ["--text-muted", "hero"],
  ])("%s on the %s glass", (ink, surface) => {
    const bg = surface === "panel" ? panel : hero;
    expect(contrast(over(parse(resolve(vars, ink)), bg), bg)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(["ok", "warn", "bad", "ai", "info", "neutral"])("chip %s on the panel and hero glass", (tone) => {
    for (const surface of [panel, hero]) {
      const bg = over(parse(resolve(vars, `--${tone}-bg`)), surface);
      const ink = over(parse(resolve(vars, tone === "neutral" ? "--neutral-ink" : `--${tone}`)), bg);
      expect(contrast(ink, bg)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

/** Rule bodies for a selector in app.css (every rule whose selector list contains it). */
function rules(selector: string): string[] {
  const out: string[] = [];
  for (const m of appCss.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const sels = m[1].split(",").map((s) => s.trim().replace(/\s+/g, " "));
    if (sels.includes(selector)) out.push(m[2]);
  }
  return out;
}

describe("body text never uses the faint ink", () => {
  it.each([
    ".muted-note",
    ".tile.is-empty p",
    ".wrapup-draft.is-empty p",
    ".foot",
    ".caller-id",
    ".time",
  ])("%s", (selector) => {
    const bodies = rules(selector);
    expect(bodies.length, selector).toBeGreaterThan(0);
    for (const b of bodies) expect(b).not.toMatch(/color\s*:\s*var\(--text-faint\)/);
  });

  it(".chip-neutral uses the neutral ink", () => {
    expect(tokensCss).toMatch(/\.chip-neutral\s*\{[^}]*color\s*:\s*var\(--neutral-ink\)/);
  });
});
