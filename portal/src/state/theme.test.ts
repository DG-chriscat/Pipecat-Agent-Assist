import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, initialTheme, saveTheme, themeKey } from "./theme";

const prefersDark = (dark: boolean) => (q: string) => ({ matches: dark && q === "(prefers-color-scheme: dark)" });

describe("theme (AP-16, DESIGN.md themes)", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to light", () => {
    expect(initialTheme("Ana", localStorage, undefined)).toBe("light");
    expect(initialTheme("Ana", localStorage, prefersDark(false))).toBe("light");
  });

  it("follows prefers-color-scheme on first visit", () => {
    expect(initialTheme("Ana", localStorage, prefersDark(true))).toBe("dark");
    expect(initialTheme(null, localStorage, prefersDark(true))).toBe("dark");
  });

  it("a saved choice wins, per agent", () => {
    saveTheme("Ana", "dark", localStorage);
    expect(localStorage.getItem(themeKey("Ana"))).toBe("dark");
    expect(initialTheme("Ana", localStorage, prefersDark(false))).toBe("dark");
    expect(initialTheme("Ben", localStorage, prefersDark(false))).toBe("light");
    saveTheme("Ben", "light", localStorage);
    expect(initialTheme("Ben", localStorage, prefersDark(true))).toBe("light");
  });

  it("ignores junk in storage", () => {
    localStorage.setItem(themeKey("Ana"), "neon");
    expect(initialTheme("Ana", localStorage, prefersDark(false))).toBe("light");
  });

  it("applies the theme to the document root", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
