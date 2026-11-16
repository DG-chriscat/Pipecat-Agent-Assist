import { describe, expect, it } from "vitest";
import { pageFromPath, pathForPage } from "./route";

describe("portal pages in the URL (AP-21: /agent/architecture)", () => {
  it.each([
    ["/agent/architecture", "architecture"],
    ["/agent/architecture/", "architecture"],
    ["/agent/settings", "settings"],
    ["/agent/", "calls"],
    ["/agent", "calls"],
    ["/", "calls"],
    ["/agent/unknown", "calls"],
  ])("%s is %s", (path, page) => {
    expect(pageFromPath(path)).toBe(page);
  });

  it("round trips every page", () => {
    for (const p of ["calls", "settings", "architecture"] as const) expect(pageFromPath(pathForPage(p))).toBe(p);
    expect(pathForPage("architecture")).toBe("/agent/architecture");
    expect(pathForPage("calls")).toBe("/agent/");
  });
});
