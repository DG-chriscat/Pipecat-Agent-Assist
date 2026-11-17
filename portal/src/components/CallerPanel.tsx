import { ShieldCheck } from "lucide-react";
import type { CallerDetails, CallerEvent } from "../api/types";
import type { IdentityGate, MoodState } from "../state/assist";
import { recordsOpen } from "../state/assist";
import { eventTone, formatIsoDate } from "../state/caller";
import { MOOD_TOOLTIP, moodChip, WORRIED } from "../state/mood";
import { initials } from "../state/names";
import type { CallerInfo } from "../state/offer";
import { agentIdentityBadge, identityBadge, maskDob, maskMemberId } from "../state/privacy";
import { PhiField, type PhiFieldName } from "./PhiField";

export type DetailsState = "loading" | "ready" | "error";

export interface CallerPanelProps {
  /** From the accept reply (QH-7): shown the moment the agent accepts. */
  caller: CallerInfo;
  memberId: string | null;
  intake: Record<string, unknown>;
  waitSecs: number | null;
  /** From GET /calls/{id}/caller, once it arrives. */
  details: CallerDetails | null;
  detailsState: DetailsState;
  /** The caller's mood trend from the tone reader (P4-5, AP-14); null until a reading. */
  mood?: MoodState | null;
  /** AP-17: the agent revealed a masked field; the parent logs it on the call record. */
  onRevealPhi: (field: PhiFieldName) => void;
  /**
   * IN-13: the assist's identity gate (pending until the agent decides). Reveal is off
   * and the member record waits until it is verified; null for a call without one.
   */
  identityGate?: IdentityGate | null;
}

const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** 102 -> "1:42" (DESIGN.md caller panel rows). */
function shortClock(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function History({
  state,
  notVerified,
  noMember,
  events,
}: {
  /** "member": the agent just marked verified and the member record is on its way. */
  state: DetailsState | "member";
  notVerified: boolean;
  noMember: boolean;
  events: CallerEvent[];
}) {
  let note: string | null = null;
  if (state === "loading") note = "Loading history.";
  else if (state === "member") note = "Loading the member record.";
  else if (state === "error") note = "Caller history is unavailable right now.";
  else if (notVerified) note = "History shows once identity is verified.";
  else if (noMember) note = "No member record for this caller.";
  else if (events.length === 0) note = "No past calls, claims, or referrals.";
  return (
    <div data-testid="history">
      <div className="label section-label">Before this call</div>
      {note ? (
        <p className="muted-note">{note}</p>
      ) : (
        <ul className="timeline">
          {events.map((e) => (
            <li key={`${e.kind}-${e.ref}`} className="tl-item">
              <span className={`tl-dot tl-${eventTone(e)}`} aria-hidden="true" />
              <span>
                <b className="tl-date">{formatIsoDate(e.occurred_on, false)}</b>{" "}
                <span className="tl-text">{e.summary}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Mood area (DESIGN.md caller panel 5): label chip plus a bar per caller turn (P4-5). */
function Mood({ mood }: { mood?: MoodState | null }) {
  const readings = mood?.readings ?? [];
  const has = readings.length > 0;
  const chip = moodChip(mood);
  const label = has ? `Mood: ${chip.text}. ${readings.length} caller turn${readings.length === 1 ? "" : "s"}.` : undefined;
  return (
    <div className="mood" data-testid="mood" title={MOOD_TOOLTIP} aria-label={label}>
      <div className="mood-head">
        <b>Mood</b>
        {has ? (
          <span className={`chip chip-${chip.tone}`}>{chip.text}</span>
        ) : (
          <span className="chip chip-neutral">Listening</span>
        )}
      </div>
      {has ? (
        <div className="bars" aria-hidden="true">
          {readings.map((r) => (
            <i
              key={r.line_id}
              className={r.score >= WORRIED ? "is-worried" : undefined}
              title={`${r.mood} (${r.emotion} ${r.confidence.toFixed(2)})`}
              style={{ height: `${Math.round(20 + r.score * 80)}%` }}
            />
          ))}
        </div>
      ) : (
        <p className="muted-note mood-note">Mood shows up once the caller talks.</p>
      )}
    </div>
  );
}

/**
 * Caller panel (DESIGN.md "Caller panel", Studio): portrait with the copper avatar and
 * a phosphor ring once identity is confirmed, masked member ID and DOB with Reveal
 * (AP-17), Identity n of 3 (AP-18), In her words, settings style rows, and the mood
 * area. Fills from the accept summary first (AP-5) and from GET /calls/{id}/caller
 * second (AP-13). Unverified callers get intake data only, labeled as what they said.
 */
export function CallerPanel({
  caller,
  memberId,
  intake,
  waitSecs,
  details,
  detailsState,
  mood,
  onRevealPhi,
  identityGate = null,
}: CallerPanelProps) {
  const member = details && !details.not_verified ? details.member : null;
  const decision =
    identityGate ??
    (details?.identity_decision
      ? { state: details.identity_decision.decision, by: details.identity_decision.agent_name }
      : null);
  // IN-13: the agent marked the caller verified (the gate is verified, intake was not).
  // The member record (matched by member ID and DOB) comes from GET /caller after the
  // decision; until then it is loading. With no such member (records false) there is
  // none to show.
  const agentVerified =
    identityGate?.state === "verified"
      ? identityGate.result !== "verified"
      : details?.identity_decision?.decision === "verified";
  const recordsExpected = agentVerified && (identityGate ? recordsOpen(identityGate) : true);
  const memberLoading = !member && recordsExpected && !details?.identity_decision;
  const noMember = !member && agentVerified && !memberLoading;
  const notVerified = !agentVerified && (details ? details.not_verified : caller.verified !== "verified");
  const name = member?.name ?? caller.name;
  const id = member?.member_id ?? memberId;
  const claim = text(intake.claim_number);
  const dob = member?.dob ?? text(intake.dob);
  const wait = details ? details.wait_secs : waitSecs;
  const showCalls = !!member && details!.calls_30d !== null;
  const identity = agentIdentityBadge(
    identityBadge(details ? details.identity_result : caller.identity, intake.identity_checks),
    decision,
  );
  const confirmed = identity.state === "confirmed";
  // IN-13: PHI Reveal is off until the caller is verified (at intake or by the agent).
  const revealOff = !!decision && (decision.state !== "verified" || noMember);
  const events = member ? details!.recent_events : [];
  const lastCall = events.find((e) => e.kind === "call");
  const words = text(intake.reason) || caller.reason;
  const plan = member ? member.plan_name : notVerified ? "Not verified" : null;
  const nameParts = name.split(/\s+/);

  return (
    <aside className="panel caller-panel" aria-label="Caller">
      <div className="portrait">
        <div className={`ring${confirmed ? " is-verified" : ""}`} data-testid="caller-avatar">
          <div className="ring-face">{initials(name)}</div>
        </div>
        <div className="portrait-text">
          <h1 className="caller-name">
            {nameParts.length > 1 ? (
              <>
                {nameParts.slice(0, -1).join(" ")} <br />
                {nameParts[nameParts.length - 1]}
              </>
            ) : (
              name
            )}
          </h1>
          <div className="caller-id" data-testid="caller-id">
            {plan && <span className="caller-plan">{plan}</span>}
            {id ? (
              <PhiField
                field="member_id"
                label="Member ID"
                value={id}
                masked={maskMemberId(id)}
                onReveal={onRevealPhi}
                disabled={revealOff}
              />
            ) : (
              <span>No member ID</span>
            )}
          </div>
        </div>
      </div>

      <div className="badges">
        <span
          className={`chip ${
            confirmed
              ? "chip-ok"
              : identity.state === "refused"
                ? "chip-bad"
                : identity.state === "partial"
                  ? "chip-warn"
                  : "chip-neutral"
          }`}
          data-testid="identity-badge"
          title={identity.detail}
        >
          <ShieldCheck strokeWidth={1.7} aria-hidden="true" />
          {identity.label}
        </span>
        {member && details!.repeat_caller && <span className="chip chip-warn">Repeat caller</span>}
      </div>

      {notVerified && (
        <p className="caller-warn" role="note">
          Identity is not verified. Details are what the caller said; no member record is shown.
        </p>
      )}
      {noMember && (
        <p className="caller-warn" role="note">
          No member record matched the member ID and date of birth, so none is shown.
        </p>
      )}

      <div data-testid="why">
        <div className="label section-label">In their words</div>
        <p className="quote">{words}</p>
        {caller.summary && caller.summary !== words && (
          <p className="why-summary" title={caller.summary}>
            {caller.summary}
          </p>
        )}
      </div>

      <dl className="rows">
        {dob && (
          <div className="rw">
            <dt>{member || (!notVerified && !noMember) ? "Born" : "Born, said"}</dt>
            <dd>
              <PhiField
                field="dob"
                label="Date of birth"
                value={formatIsoDate(dob)}
                masked={maskDob(dob)}
                onReveal={onRevealPhi}
                disabled={revealOff}
              />
            </dd>
          </div>
        )}
        {claim && (
          <div className="rw">
            <dt>Claim</dt>
            <dd className="mono">{claim}</dd>
          </div>
        )}
        {wait !== null && (
          <div className="rw">
            <dt>Waited</dt>
            <dd>{shortClock(wait)}</dd>
          </div>
        )}
        {showCalls && (
          <div className="rw">
            <dt>Calls, 30 d</dt>
            <dd>{details!.calls_30d}</dd>
          </div>
        )}
        {lastCall && (
          <div className="rw">
            <dt>Last call</dt>
            <dd>{formatIsoDate(lastCall.occurred_on, false)}</dd>
          </div>
        )}
      </dl>

      <History
        state={memberLoading ? "member" : detailsState}
        notVerified={notVerified}
        noMember={noMember}
        events={events}
      />
      <Mood mood={mood} />
    </aside>
  );
}
