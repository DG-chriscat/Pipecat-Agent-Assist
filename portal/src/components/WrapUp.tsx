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
  onSave: (reasonCode: string, notes: string, summary: string) => void;
  onRetry: () => void;
}

const ENDED: Record<"agent" | "caller", string> = {
  agent: "You ended the call",
  caller: "The caller hung up",
};
/** Longest summary the server keeps (queue_service.MAX_SUMMARY). */
export const MAX_SUMMARY = 3000;

/**
 * Wrap up (P3-5, P5-1): after End or a caller hang up. Call facts, the assist's
 * summary draft in an editor (AP-9), a reason code from the pack with the assist's
 * suggestion preselected, notes, then Save, which writes the calls record and sets
 * the agent available.
 *
 * The draft can land after the view opens (the bot writes it when the call ends):
 * until the agent types or picks, the editor and the pick follow the latest draft;
 * after that, the agent's own text and pick win.
 */
export function WrapUp({ info, loadError, saving, saveError, onSave, onRetry }: Props) {
  /** The agent's own text and pick; null until they edit or click. */
  const [summaryEdit, setSummaryEdit] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  const codes = info?.reason_codes ?? [];
  const suggested =
    info?.suggested_reason_code && codes.some((r) => r.code === info.suggested_reason_code)
      ? info.suggested_reason_code
      : null;
  const code = picked ?? suggested;
  const summary = summaryEdit ?? info?.summary_draft ?? "";
  const canSave = !!info && !!code && !saving;
  const pending = !!info?.draft_pending && !info.summary_draft;

  function save() {
    if (canSave && code) onSave(code, notes, summary);
  }

  function onSaveKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      save();
    }
  }

  const draftState = info?.summary_draft ? "" : pending ? " is-pending" : " is-empty";

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

          <div className={`wrapup-draft${draftState}`} data-testid="summary-draft">
            <div className="wrapup-draft-head">
              <span className="chip chip-ai">
                <Sparkles strokeWidth={1.7} aria-hidden="true" />
                Summary draft
              </span>
              <span className="label" aria-live="polite">
                {pending ? "Drafting from the call" : info.summary_draft ? "Edit before you save" : ""}
              </span>
            </div>
            <textarea
              aria-label="Summary"
              className="wrapup-summary"
              rows={5}
              maxLength={MAX_SUMMARY}
              placeholder={
                pending ? "The assist is writing the summary from the call." : "Write a short summary of the call."
              }
              value={summary}
              disabled={saving}
              onChange={(e) => setSummaryEdit(e.target.value)}
              onKeyDown={onSaveKey}
            />
          </div>

          <div className="wrapup-field">
            <div className="label" id="reason-label">
              Reason code
            </div>
            <div className="reason-codes" role="radiogroup" aria-labelledby="reason-label">
              {codes.map((r) => (
                <button
                  key={r.code}
                  type="button"
                  role="radio"
                  aria-checked={code === r.code}
                  className={`reason${code === r.code ? " is-on" : ""}${suggested === r.code ? " is-suggested" : ""}`}
                  disabled={saving}
                  onClick={() => setPicked(r.code)}
                >
                  {r.label}
                  {suggested === r.code && (
                    <span className="reason-tag">
                      <Sparkles strokeWidth={1.7} aria-hidden="true" />
                      Suggested
                    </span>
                  )}
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
              rows={2}
              maxLength={2000}
              placeholder="Anything else for the record"
              value={notes}
              disabled={saving}
              onChange={(e) => setNotes(e.target.value)}
              onKeyDown={onSaveKey}
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
