import { useId } from "react";
import type { CallerDetails, CallerEvent } from "../api/types";
import { eventTone, formatIsoDate, SPARK_H, SPARK_W, sparkPath } from "../state/caller";
import type { CallerInfo } from "../state/offer";
import { formatWait } from "../state/offer";

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
  /** Caller tone readings over the call, 0 calm to 1 upset. Fed by Phase 4 (AP-14). */
  mood?: number[];
}

const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

function History({ state, notVerified, events }: { state: DetailsState; notVerified: boolean; events: CallerEvent[] }) {
  let note: string | null = null;
  if (state === "loading") note = "Loading history.";
  else if (state === "error") note = "Caller history is unavailable right now.";
  else if (notVerified) note = "History shows once identity is verified.";
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

function Mood({ values }: { values?: number[] }) {
  const gradId = useId();
  const has = !!values && values.length >= 2;
  return (
    <div className="mood-area" data-testid="mood">
      <div className="label section-label">Mood over call</div>
      <svg className="spark" viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id={gradId} x1="0" x2="1">
            <stop offset="0" stopColor="var(--phosphor)" />
            <stop offset=".55" stopColor="var(--bakelite)" />
            <stop offset="1" stopColor="var(--clay)" />
          </linearGradient>
        </defs>
        {has ? (
          <path className="spark-line" d={sparkPath(values)} stroke={`url(#${gradId})`} />
        ) : (
          <line className="spark-empty" x1="0" x2={SPARK_W} y1={SPARK_H - 6} y2={SPARK_H - 6} />
        )}
      </svg>
      <div className="label spark-axis">
        <span>Calm</span>
        <span>Upset</span>
      </div>
      {!has && <p className="muted-note spark-note">Mood shows up once the caller talks.</p>}
    </div>
  );
}

/**
 * Caller panel (DESIGN.md "Caller panel"): name, member ID and plan, why they called,
 * key facts, Before this call timeline, Mood over call. Fills from the accept summary
 * first (AP-5) and from GET /calls/{id}/caller second (AP-13). Unverified callers get
 * intake data only, labeled as what the caller said.
 */
export function CallerPanel({ caller, memberId, intake, waitSecs, details, detailsState, mood }: CallerPanelProps) {
  const member = details && !details.not_verified ? details.member : null;
  const notVerified = details ? details.not_verified : caller.verified !== "verified";
  const name = member?.name ?? caller.name;
  const id = member?.member_id ?? memberId;
  const idLine = id
    ? [id, member ? member.plan_name : notVerified ? "Not verified" : null].filter(Boolean).join(" · ")
    : "No member ID";
  const claim = text(intake.claim_number);
  const saidDob = text(intake.dob);
  const wait = details ? details.wait_secs : waitSecs;
  const showCalls = !!member && details!.calls_30d !== null;

  return (
    <aside className="glass caller-panel" aria-label="Caller">
      <div>
        <div className="label">Caller</div>
        <h1 className="caller-name">{name}</h1>
        <div className="mono caller-id" data-testid="caller-id">
          {idLine}
        </div>
      </div>
      {notVerified && (
        <p className="caller-warn" role="note">
          Identity is not verified. Details are what the caller said; no member record is shown.
        </p>
      )}
      <div className="why" data-testid="why">
        <div className="label">Why they called</div>
        <p>{caller.reason}</p>
        {caller.summary && caller.summary !== caller.reason && <p className="why-summary">{caller.summary}</p>}
      </div>
      <dl className="kv">
        {member ? (
          <>
            <dt>Born</dt>
            <dd>{formatIsoDate(member.dob)}</dd>
          </>
        ) : (
          saidDob && (
            <>
              <dt>{notVerified ? "Born, said" : "Born"}</dt>
              <dd>{formatIsoDate(saidDob)}</dd>
            </>
          )
        )}
        {claim && (
          <>
            <dt>Claim</dt>
            <dd className="mono">{claim}</dd>
          </>
        )}
        {wait !== null && (
          <>
            <dt>Waited</dt>
            <dd>{formatWait(wait)}</dd>
          </>
        )}
        {showCalls && (
          <>
            <dt>Calls, 30 d</dt>
            <dd>
              {details!.calls_30d}
              {details!.repeat_caller && <span className="chip chip-warn kv-chip">Repeat</span>}
            </dd>
          </>
        )}
      </dl>
      <History state={detailsState} notVerified={notVerified} events={member ? details!.recent_events : []} />
      <Mood values={mood} />
    </aside>
  );
}
