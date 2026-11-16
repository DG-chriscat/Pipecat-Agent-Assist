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
  /** IN-13: Reveal is off until the agent marks the caller verified. */
  disabled?: boolean;
}

/**
 * A masked PHI value with a Reveal link (DESIGN.md "PHI field"). Masked by default;
 * Reveal is a deliberate click that logs an event and re-masks after 30 s. Disabled
 * (caller not verified yet, IN-13) it stays masked.
 */
export function PhiField({ field, label, value, masked, onReveal, className, disabled = false }: Props) {
  const [shown, setShown] = useState(false);
  // Restarts on every reveal: each reveal gets its own 30 s.
  const [revealId, setRevealId] = useState(0);

  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(false), REVEAL_SECS * 1000);
    return () => clearTimeout(t);
  }, [shown, revealId]);

  function toggle() {
    if (disabled) return;
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
  const visible = shown && !disabled;
  return (
    <span className={`phi${visible ? " is-shown" : ""}${className ? ` ${className}` : ""}`}>
      <span className="phi-value mono" data-testid={`phi-${field}`}>
        {visible ? value : masked}
      </span>
      <button
        type="button"
        className="phi-reveal"
        aria-label={`${visible ? "Hide" : "Reveal"} ${name}`}
        onClick={toggle}
        disabled={disabled}
        title={disabled ? "Verify the caller to reveal" : undefined}
      >
        {visible ? "Hide" : "Reveal"}
      </button>
    </span>
  );
}
