import { afterEach, describe, expect, it, vi } from "vitest";

const seen: (string | null)[] = [];
vi.mock("html-to-image", () => ({
  toPng: vi.fn(async (el: HTMLElement) => {
    seen.push(el.querySelector("path")!.getAttribute("style"));
    return "data:image/png;base64,AAAA";
  }),
}));

import { exportPng } from "./exportPng";

describe("exportPng (AP-21 Export)", () => {
  afterEach(() => {
    document.head.querySelectorAll("style[data-test]").forEach((s) => s.remove());
    document.body.innerHTML = "";
    seen.length = 0;
  });

  it("inlines the edges' stylesheet paint for the capture, restores it, and downloads the PNG", async () => {
    const style = document.createElement("style");
    style.dataset.test = "1";
    style.textContent = ".edge { stroke: rgb(224, 66, 255); stroke-dasharray: 5 6; }";
    document.head.appendChild(style);
    const el = document.createElement("div");
    el.innerHTML = '<svg><path class="edge" d="M0 0 L10 10" /></svg>';
    document.body.appendChild(el);
    const clicks: { href: string; download: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push({ href: this.href, download: this.download });
    });

    await exportPng(el, "vitals-architecture-business.png");

    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatch(/stroke: rgb\(224, 66, 255\)/);
    expect(seen[0]).toMatch(/stroke-dasharray: 5 6/);
    expect(el.querySelector("path")!.getAttribute("style")).toBeNull();
    expect(clicks).toEqual([{ href: "data:image/png;base64,AAAA", download: "vitals-architecture-business.png" }]);
  });
});
