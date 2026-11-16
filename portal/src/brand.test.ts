import { describe, expect, it } from "vitest";
import html from "../index.html?raw";

describe("product name (ADR 0003)", () => {
  it("the page title is Deepgram Vitals", () => {
    expect(html).toMatch(/<title>Agent · Deepgram Vitals<\/title>/);
    expect(html).not.toMatch(/agent assist/i);
  });
});
