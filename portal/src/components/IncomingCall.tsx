import { Check, PhoneIncoming, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { callerInfo, formatWait, secondsLeft, type Offer } from "../state/offer";
import { VerifiedChip } from "./VerifiedChip";

interface Props {
  offer: Offer;
  windowSecs: number;
  /** Client clock in ms; the parent ticks it while ringing. */
  nowMs: number;
  busy: boolean;
  error: string | null;
  onAccept: () => void;
  onDecline: () => void;
}

const RING_R = 34;
const RING_C = 2 * Math.PI * RING_R;

/** AP-3: the offer card. 20 s countdown ring; Enter accepts, Esc declines. */
export function IncomingCall({ offer, windowSecs, nowMs, busy, error, onAccept, onDecline }: Props) {
  const info = callerInfo(offer.call);
  const left = secondsLeft(offer.expiresAt, nowMs, windowSecs);
  const fraction = windowSecs > 0 ? left / windowSecs : 0;
  const canAccept = !busy && left > 0;
  const canDecline = !busy;

  // Latest handlers for the window key listener, without re-binding every tick.
  const keys = useRef({ canAccept, canDecline, onAccept, onDecline });
  useEffect(() => {
    keys.current = { canAccept, canDecline, onAccept, onDecline };
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      // Keys pressed inside the rail menu belong to the menu, not the call card.
      if (e.target instanceof Element && e.target.closest("[role=menu]")) return;
      const k = keys.current;
      if (e.key === "Enter" && k.canAccept) {
        e.preventDefault();
        k.onAccept();
      } else if (e.key === "Escape" && k.canDecline) {
        e.preventDefault();
        k.onDecline();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <section className="panel card incoming" role="dialog" aria-modal="false" aria-label="Incoming call">
      <div className="incoming-head">
        <span className="chip chip-ai">
          <PhoneIncoming strokeWidth={1.7} aria-hidden="true" />
          Incoming call
        </span>
      </div>

      <div className="incoming-body">
        <div
          className={`countdown${left <= 5 ? " countdown-low" : ""}`}
          data-testid="countdown-ring"
          data-fraction={String(Math.round(fraction * 100) / 100)}
        >
          <svg viewBox="0 0 80 80" aria-hidden="true">
            <defs>
              <linearGradient id="ring-grad" x1="0" x2="1" y1="0" y2="1">
                <stop offset="0" stopColor="var(--fuchsia)" />
                <stop offset="1" stopColor="var(--led)" />
              </linearGradient>
            </defs>
            <circle className="countdown-track" cx="40" cy="40" r={RING_R} />
            <circle
              className="countdown-arc"
              cx="40"
              cy="40"
              r={RING_R}
              strokeDasharray={RING_C}
              strokeDashoffset={RING_C * (1 - fraction)}
            />
          </svg>
          <span className="countdown-num mono" role="timer" aria-label={`${left} seconds to answer`}>
            {left}
          </span>
        </div>

        <div className="incoming-who">
          <h1 className="incoming-name">{info.name}</h1>
          <div className="incoming-chips">
            <VerifiedChip result={info.verified} />
            <span className="chip chip-neutral">Waited {formatWait(nowMs / 1000 - offer.call.queued_at)}</span>
          </div>
        </div>
      </div>

      <div className="why">
        <div className="label">Why they called</div>
        <p>{info.reason}</p>
      </div>

      <div className="incoming-actions">
        <button type="button" className="btn" disabled={!canDecline} onClick={onDecline}>
          <X strokeWidth={1.7} aria-hidden="true" />
          Decline
          <span className="kbd" aria-hidden="true">
            Esc
          </span>
        </button>
        <button
          type="button"
          className="btn btn-primary"
          aria-label="Accept"
          aria-busy={busy}
          disabled={!canAccept}
          onClick={onAccept}
        >
          <Check strokeWidth={1.7} aria-hidden="true" />
          {busy ? "Connecting" : "Accept"}
          <span className="kbd kbd-on-primary" aria-hidden="true">
            Enter
          </span>
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
