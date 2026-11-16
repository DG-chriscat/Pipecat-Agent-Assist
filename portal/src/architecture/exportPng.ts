/** Download a DOM node as a PNG (AP-21 Export). html-to-image (MIT, no dependencies,
 * ~300 kB unpacked, loaded only when Export is pressed) renders the node through an SVG
 * foreignObject, so the diagram keeps its fonts, textures, and theme. */

const SVG_PAINT = ["fill", "stroke", "stroke-width", "stroke-dasharray", "opacity"] as const;

/** html-to-image copies computed styles for HTML but not for SVG children, so the
 * stylesheet's edge colors and dashes would be lost: inline them for the capture. */
function inlineSvgPaint(root: HTMLElement): () => void {
  const saved: [Element, string | null][] = [];
  root.querySelectorAll("svg *").forEach((el) => {
    const cs = getComputedStyle(el);
    const before = el.getAttribute("style");
    saved.push([el, before]);
    const paint = SVG_PAINT.map((p) => [p, cs.getPropertyValue(p)] as const)
      .filter(([, v]) => v)
      .map(([p, v]) => `${p}: ${v}`)
      .join("; ");
    el.setAttribute("style", before ? `${before}; ${paint}` : paint);
  });
  return () => {
    for (const [el, style] of saved) {
      if (style === null) el.removeAttribute("style");
      else el.setAttribute("style", style);
    }
  };
}

export async function exportPng(el: HTMLElement, filename: string): Promise<void> {
  const { toPng } = await import("html-to-image");
  const ground = getComputedStyle(document.documentElement).getPropertyValue("--ground").trim() || undefined;
  const restore = inlineSvgPaint(el);
  let url: string;
  try {
    url = await toPng(el, { pixelRatio: 2, backgroundColor: ground, cacheBust: true });
  } finally {
    restore();
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}
