/** PHI masking (AP-17) and the identity badge (AP-18). Pure helpers. */

import type { IdentityResult } from "../api/types";

/** A revealed field re-masks after this many seconds (AP-17). */
export const REVEAL_SECS = 30;

const DOT = "•";

/** "EVM100231" -> "••••31" (DESIGN.md caller panel). */
export function maskMemberId(id: string): string {
  const s = id.trim();
  return DOT.repeat(4) + (s.length > 2 ? s.slice(-2) : "");
}

/** "1984-03-12" -> "••/••/1984". */
export function maskDob(iso: string): string {
  const m = /^(\d{4})-\d{2}-\d{2}/.exec(iso.trim());
  return `${DOT}${DOT}/${DOT}${DOT}/${m ? m[1] : DOT.repeat(4)}`;
}

export type IdentityState = "unchecked" | "partial" | "confirmed" | "refused";

export interface IdentityBadge {
  state: IdentityState;
  label: string;
  /** Which checks passed (hover text). */
  detail: string;
}

const CHECKS = [
  ["full_name", "Name"],
  ["dob", "Date of birth"],
  ["member_id", "Member ID"],
] as const;

/**
 * Identity n of 3 from the intake result and summary.identity_checks (which of name,
 * date of birth, and member ID matched). Verified is always 3 of 3.
 */
export function identityBadge(result: IdentityResult | null, checks: unknown): IdentityBadge {
  if (!result) {
    return { state: "unchecked", label: "Identity not checked", detail: "Identity was not checked at intake" };
  }
  const listed = Array.isArray(checks) ? checks : [];
  const passed = new Set(
    result === "verified" ? CHECKS.map(([k]) => k) : CHECKS.map(([k]) => k).filter((k) => listed.includes(k)),
  );
  const detail = CHECKS.map(([k, label]) => `${label} ${passed.has(k) ? "✓" : "✗"}`).join(" · ");
  return {
    state: result === "verified" ? "confirmed" : "partial",
    label: `Identity ${passed.size} of 3`,
    detail,
  };
}

/**
 * The agent's decision on the verification step (IN-13) wins over the intake badge:
 * Mark verified is "Verified by agent" (phosphor, the agent's name on hover); Could not
 * verify is "Not verified by agent" (clay).
 */
export function agentIdentityBadge(
  base: IdentityBadge,
  decision: { state: "verified" | "pending" | "not_verified"; by: string | null } | null,
): IdentityBadge {
  if (!decision || decision.state === "pending") return base;
  const who = decision.by || "the agent";
  if (decision.state === "verified") {
    // Verified at intake: keep "Identity 3 of 3".
    if (base.state === "confirmed" && !decision.by) return base;
    return { state: "confirmed", label: "Verified by agent", detail: `Verified by ${who} on the call` };
  }
  return { state: "refused", label: "Not verified by agent", detail: `${who} could not verify the caller` };
}
