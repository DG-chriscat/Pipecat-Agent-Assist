import type { IdentityResult } from "../api/types";

const CHIPS: Record<IdentityResult, { cls: string; text: string }> = {
  verified: { cls: "chip-ok", text: "Verified" },
  not_verified: { cls: "chip-warn", text: "Not verified" },
  not_found: { cls: "chip-warn", text: "Member not found" },
};

/** Identity result chip: phosphor when verified, bakelite otherwise (DESIGN.md chips). */
export function VerifiedChip({ result }: { result: IdentityResult }) {
  const c = CHIPS[result];
  return <span className={`chip ${c.cls}`}>{c.text}</span>;
}
