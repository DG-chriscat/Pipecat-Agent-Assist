import { useEffect, useState } from "react";
import { REVEAL_SECS } from "../state/privacy";

export type PhiFieldName = "member_id" | "dob";

interface Props {
  field: PhiFieldName;
  /** Used in the button's accessible name ("Reveal member ID"). */
  label: string;
  value: string;
  masked: string;
  /** Called once per reveal; the parent logs it on the call record (AP-17). */
  onReveal: (field: PhiFieldName) => void;
  className?: string;
}

/**
 * A masked PHI value with a Reveal link (DESIGN.md "PHI field"). Masked by default;
 * Reveal is a deliberate click that logs an event and re-masks after 30 s.
 */
export function PhiField({ field, label, value, masked, onReveal, className }: Props) {
  const [shown, setShown] = useState(false);
  // Restarts on every reveal: each reveal gets its own 30 s.
  const [revealId, setRevealId] = useState(0);

  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(false), REVEAL_SECS * 1000);
    return () => clearTimeout(t);
  }, [shown, revealId]);

  function toggle() {
    if (shown) {
      setShown(false);
      return;
    }
    setShown(true);
    setRevealId((n) => n + 1);
    onReveal(field);
  }

  // "Member ID" -> "member ID", "Date of birth" -> "date of birth".
  const name = label.charAt(0).toLowerCase() + label.slice(1);
  return (
    <span className={`phi${shown ? " is-shown" : ""}${className ? ` ${className}` : ""}`}>
      <span className="phi-value mono" data-testid={`phi-${field}`}>
        {shown ? value : masked}
      </span>
      <button type="button" className="phi-reveal" aria-label={`${shown ? "Hide" : "Reveal"} ${name}`} onClick={toggle}>
        {shown ? "Hide" : "Reveal"}
      </button>
    </span>
  );
}
