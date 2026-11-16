/** Studio themes (AP-16, ADR 0005): light (default) and dark, same layout. */

export type Theme = "light" | "dark";

type MatchMedia = (query: string) => { matches: boolean };
type Store = Pick<Storage, "getItem" | "setItem">;

/** Saved per agent name: the demo sign in has no other stable identity. */
export const themeKey = (agent: string | null) => `vitals.theme.${agent ?? ""}`;

const isTheme = (v: unknown): v is Theme => v === "light" || v === "dark";

/** A saved choice for this agent, else prefers-color-scheme, else light. */
export function initialTheme(agent: string | null, store: Store | undefined, matchMedia: MatchMedia | undefined): Theme {
  const saved = agent ? store?.getItem(themeKey(agent)) : null;
  if (isTheme(saved)) return saved;
  return matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function saveTheme(agent: string, theme: Theme, store: Store | undefined): void {
  try {
    store?.setItem(themeKey(agent), theme);
  } catch {
    // Storage full or blocked: the theme still applies for this session.
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}
