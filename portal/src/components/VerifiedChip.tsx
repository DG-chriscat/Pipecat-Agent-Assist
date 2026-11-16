import type { IdentityResult } from "../api/types";

const CHIPS: Record<IdentityResult, { cls: string; text: string }> = {
  verified: { cls: "chip-ok", text: "Verified" },
  close_match: { cls: "chip-warn", text: "Close match" },
  not_verified: { cls: "chip-warn", text: "Not verified" },
  not_found: { cls: "chip-warn", text: "Member not found" },
  incomplete: { cls: "chip-warn", text: "Identity incomplete" },
};

/** The agent's human verification decision (IN-13), when one was made. */
export type AgentDecision = "verified" | "not_verified" | null;

/**
 * Identity result chip: phosphor when verified, bakelite otherwise (DESIGN.md chips).
 * The agent's decision wins: "Verified by agent" (phosphor) or "Not verified by agent".
 */
export function VerifiedChip({ result, decision = null }: { result: IdentityResult; decision?: AgentDecision }) {
  if (decision === "verified" && result !== "verified") return <span className="chip chip-ok">Verified by agent</span>;
  if (decision === "not_verified") return <span className="chip chip-bad">Not verified by agent</span>;
  const c = CHIPS[result] ?? CHIPS.not_verified;
  return <span className={`chip ${c.cls}`}>{c.text}</span>;
}
