import { Check, ClipboardCheck, Sparkles } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import type { WrapUpInfo } from "../api/types";
import { formatClock } from "../state/offer";
import { VerifiedChip } from "./VerifiedChip";

interface Props {
  info: WrapUpInfo | null;
  loadError: string | null;
  saving: boolean;
  saveError: string | null;
  onSave: (reasonCode: string, notes: string) => void;
  onRetry: () => void;
}

const ENDED: Record<"agent" | "caller", string> = {
  agent: "You ended the call",
  caller: "The caller hung up",
};

/**
 * Wrap up (P3-5): after End or a caller hang up. Call facts, the summary draft slot
 * (AP-9 fills it in Phase 4), a reason code from the pack, notes, then Save, which
 * writes the calls record and sets the agent available.
 */
export function WrapUp({ info, loadError, saving, saveError, onSave, onRetry }: Props) {
  const [code, setCode] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const canSave = !!info && !!code && !saving;

  function save() {
    if (canSave && code) onSave(code, notes);
  }

  function onNotesKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      save();
    }
  }

  return (
    <section className="panel card wrapup" role="region" aria-label="Wrap up">
      <div className="wrapup-head">
        <span className="chip chip-info">
          <ClipboardCheck strokeWidth={1.7} aria-hidden="true" />
          Wrap up
        </span>
        {info?.ended_by && <span className="label">{ENDED[info.ended_by]}</span>}
      </div>

      {!info ? (
        <div className="wrapup-loading">
          {loadError ? (
            <>
              <p className="error" role="alert">
                {loadError}
              </p>
              <button type="button" className="btn" onClick={onRetry}>
                Try again
              </button>
            </>
          ) : (
            <p className="muted-note">Loading the call</p>
          )}
        </div>
      ) : (
        <>
          <div className="wrapup-who">
            <div>
              <h1 className="wrapup-name">{info.caller_name}</h1>
              <div className="incoming-chips">
                {info.identity_result && <VerifiedChip result={info.identity_result} />}
                <span className="chip chip-neutral">
                  Call <span className="mono">{formatClock(info.duration_secs)}</span>
                </span>
              </div>
            </div>
          </div>

          {info.reason && (
            <div className="why">
              <div className="label">Why they called</div>
              <p>{info.reason}</p>
            </div>
          )}

          <div className={`wrapup-draft${info.summary_draft ? "" : " is-empty"}`} data-testid="summary-draft">
            <div className="wrapup-draft-head">
              <span className="chip chip-ai">
                <Sparkles strokeWidth={1.7} aria-hidden="true" />
                Summary draft
              </span>
              {!info.summary_draft && <span className="label">Coming soon</span>}
            </div>
            <p>
              {info.summary_draft ??
                "The assist will draft the call summary and suggest a reason code here. For now, add your notes below."}
            </p>
          </div>

          <div className="wrapup-field">
            <div className="label" id="reason-label">
              Reason code
            </div>
            <div className="reason-codes" role="radiogroup" aria-labelledby="reason-label">
              {info.reason_codes.map((r) => (
                <button
                  key={r.code}
                  type="button"
                  role="radio"
                  aria-checked={code === r.code}
                  className={`reason${code === r.code ? " is-on" : ""}`}
                  disabled={saving}
                  onClick={() => setCode(r.code)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>

          <div className="wrapup-field">
            <label className="label" htmlFor="wrapup-notes">
              Notes
            </label>
            <textarea
              id="wrapup-notes"
              className="field wrapup-notes"
              rows={3}
              maxLength={2000}
              placeholder="What happened and what comes next"
              value={notes}
              disabled={saving}
              onChange={(e) => setNotes(e.target.value)}
              onKeyDown={onNotesKey}
            />
          </div>

          <div className="incoming-actions">
            <button type="button" className="btn btn-primary" disabled={!canSave} aria-busy={saving} onClick={save}>
              <Check strokeWidth={1.7} aria-hidden="true" />
              {saving ? "Saving" : "Save and go available"}
            </button>
          </div>

          {saveError && (
            <p className="error" role="alert">
              {saveError}
            </p>
          )}
        </>
      )}
    </section>
  );
}
