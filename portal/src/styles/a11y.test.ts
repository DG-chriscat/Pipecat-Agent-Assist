import { describe, expect, it } from "vitest";
import appCss from "./app.css?raw";
import tokensCss from "./tokens.css?raw";

/** Keyboard first (DESIGN.md principle 5, P5-3): every control shows where focus is. */
describe("visible focus", () => {
  it("has a fuchsia focus ring for every focusable control by default", () => {
    const m = tokensCss.match(/([^{}]*:focus-visible[^{}]*)\{([^}]*)\}/g) ?? [];
    const global = m.find((r) => /^\s*:where\(/.test(r.split("{")[0].trim().split("\n").at(-1) ?? ""));
    expect(global, "a :where(...):focus-visible rule in tokens.css").toBeDefined();
    expect(global).toMatch(/outline\s*:\s*2px solid var\(--fuchsia\)/);
  });

  it("never removes the outline without a replacement on focus-visible", () => {
    for (const r of appCss.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
      if (!/:focus-visible/.test(r[1]) || !/outline\s*:\s*none/.test(r[2])) continue;
      expect(r[2], r[1]).toMatch(/background|box-shadow/);
    }
  });
});

describe("reduced motion (DESIGN.md Motion)", () => {
  it("stops animations and transitions, and smooth scrolling, for prefers-reduced-motion", () => {
    const m = tokensCss.match(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/animation-duration/);
    expect(m![1]).toMatch(/transition-duration/);
    expect(m![1]).toMatch(/scroll-behavior\s*:\s*auto/);
  });
});
