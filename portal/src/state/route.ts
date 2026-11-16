/** Portal pages in the URL (AP-21): /agent/architecture can be bookmarked and shared.
 * The server serves index.html for every path under /agent behind the shared login. */

export type Page = "calls" | "settings" | "architecture";

const PATHS: Record<Page, string> = {
  calls: "/agent/",
  settings: "/agent/settings",
  architecture: "/agent/architecture",
};

export function pageFromPath(path: string): Page {
  const p = path.replace(/\/+$/, "");
  if (p === PATHS.architecture) return "architecture";
  if (p === PATHS.settings) return "settings";
  return "calls";
}

export const pathForPage = (page: Page): string => PATHS[page];
