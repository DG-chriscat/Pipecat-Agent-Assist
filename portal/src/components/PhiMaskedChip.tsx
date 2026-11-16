import { Lock } from "lucide-react";

/** Header chip (AP-17): PHI is masked by default on every screen. */
export function PhiMaskedChip() {
  return (
    <span className="chip chip-neutral" title="Member ID and date of birth are masked. Reveal is logged.">
      <Lock strokeWidth={1.7} aria-hidden="true" />
      PHI masked
    </span>
  );
}
