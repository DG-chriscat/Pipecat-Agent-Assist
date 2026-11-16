import type { AgentState } from "../api/types";

const LABELS: Record<AgentState, string> = {
  offline: "Not available",
  available: "Available",
  ringing: "Ringing",
  on_call: "On call",
  wrap_up: "Wrap up",
};

interface Props {
  state: AgentState;
  disabled: boolean;
  busy: boolean;
  onToggle: () => void;
}

/** AP-2 status toggle: available or not available. Service set states show their name. */
export function StatusToggle({ state, disabled, busy, onToggle }: Props) {
  const checked = state === "available";
  return (
    <div className={`status-toggle${checked ? " is-on" : ""}`}>
      <span className="status-text">{LABELS[state]}</span>
      <button
        type="button"
        role="switch"
        aria-label="Available"
        aria-checked={checked}
        aria-busy={busy}
        disabled={disabled}
        className="switch"
        onClick={onToggle}
      >
        <span className="switch-knob" />
      </button>
    </div>
  );
}
