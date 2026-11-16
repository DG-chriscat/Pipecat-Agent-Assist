import { Mic, MicOff, Pause, PhoneOff, Play, Sparkles } from "lucide-react";
import type { CallerDetails } from "../api/types";
import type { CallerInfo } from "../state/offer";
import { formatClock } from "../state/offer";
import { CallerPanel, type DetailsState } from "./CallerPanel";
import { TopBar } from "./TopBar";
import { VerifiedChip } from "./VerifiedChip";

export type CallPhase = "connecting" | "live";

export interface InCallProps {
  caller: CallerInfo;
  memberId: string | null;
  /** Intake answers from the accept reply. */
  intake?: Record<string, unknown>;
  waitSecs?: number | null;
  /** Caller history (GET /calls/{id}/caller), once loaded. */
  details?: CallerDetails | null;
  detailsState?: DetailsState;
  phase: CallPhase;
  muted: boolean;
  held: boolean;
  /** Loudest remote participant, 0..1, from the Daily audio level observer. */
  remoteLevel: number;
  elapsedSecs: number;
  error: string | null;
  onMute: () => void;
  onHold: () => void;
  onEnd: () => void;
}

/** Bar shape for the island waveform (relative heights, DESIGN.md mockup). */
const BARS = [0.4, 0.8, 0.55, 1, 0.45, 0.7, 0.3];
/** Below this the line is quiet and the bars rest. */
export const SPEAKING_LEVEL = 0.02;

function Waveform({ level }: { level: number }) {
  const speaking = level > SPEAKING_LEVEL;
  // Daily levels are small (speech is ~0.05..0.4); lift them so bars move visibly.
  const gain = speaking ? Math.min(1, Math.sqrt(level) * 1.6) : 0;
  return (
    <div className={`wave${speaking ? " speaking" : ""}`} data-testid="waveform" aria-hidden="true">
      {BARS.map((b, i) => (
        <i key={i} style={{ height: `${Math.round(4 + 16 * b * gain)}px`, animationDelay: `${i * 0.08}s` }} />
      ))}
    </div>
  );
}

function CallIsland({ caller, phase, held, remoteLevel, elapsedSecs }: InCallProps) {
  const status = phase === "connecting" ? "Connecting" : held ? "On hold" : null;
  return (
    <div className={`island call-island${held ? " is-held" : ""}`} role="group" aria-label="Call">
      <span className={`vdot vdot-${caller.verified === "verified" ? "ok" : "warn"}`} aria-hidden="true" />
      <b className="island-name">{caller.name}</b>
      <VerifiedChip result={caller.verified} />
      <Waveform level={held || phase !== "live" ? 0 : remoteLevel} />
      {status && <span className="chip chip-neutral">{status}</span>}
      <span className="chip chip-neutral" data-testid="mood-chip" title="Mood reading arrives in a later phase">
        Mood –
      </span>
      <span className="time" role="timer" aria-label="Call time">
        {formatClock(elapsedSecs)}
      </span>
    </div>
  );
}

function Controls({ phase, muted, held, onMute, onHold, onEnd }: InCallProps) {
  const live = phase === "live";
  return (
    <div className="ctrls">
      <button
        type="button"
        className="round-btn"
        aria-label="Mute"
        title={muted ? "Unmute" : "Mute"}
        aria-pressed={muted}
        disabled={!live || held}
        onClick={onMute}
      >
        {muted ? <MicOff strokeWidth={1.8} aria-hidden="true" /> : <Mic strokeWidth={1.8} aria-hidden="true" />}
      </button>
      <button
        type="button"
        className="round-btn"
        aria-label="Hold"
        title={held ? "Resume" : "Hold"}
        aria-pressed={held}
        disabled={!live}
        onClick={onHold}
      >
        {held ? <Play strokeWidth={1.8} aria-hidden="true" /> : <Pause strokeWidth={1.8} aria-hidden="true" />}
      </button>
      <button type="button" className="end-btn" aria-label="End call" onClick={onEnd}>
        <PhoneOff strokeWidth={1.8} aria-hidden="true" />
        End
      </button>
    </div>
  );
}

function AssistColumn() {
  return (
    <section className="assist" aria-label="Assist">
      <div className="glass hero hero-empty">
        <span className="chip chip-ai">
          <Sparkles strokeWidth={1.8} aria-hidden="true" />
          Next best step
        </span>
        <div className="hero-listening">
          <div className="orb orb-sm" aria-hidden="true" />
          <div>
            <h2 className="hero-title">Listening</h2>
            <p className="hero-why">The next best step shows up here as the call goes.</p>
          </div>
        </div>
      </div>
      <label className="glass ask">
        <Sparkles strokeWidth={1.8} aria-hidden="true" />
        <input
          type="text"
          aria-label="Ask"
          placeholder="Ask anything about this caller"
          disabled
        />
        <span className="kbd" aria-hidden="true">
          ⌘ K
        </span>
      </label>
    </section>
  );
}

function TranscriptColumn() {
  return (
    <aside className="glass transcript" aria-label="Transcript">
      <div className="transcript-head">
        <span className="label">Live transcript</span>
        <span className="chip chip-bad">
          <span className="live-dot" aria-hidden="true" />
          Live
        </span>
      </div>
      <p className="muted-note">The transcript shows up here once the caller and you talk.</p>
    </aside>
  );
}

/** In-call frame (DESIGN.md "Layout"): top bar with call island and controls, three columns. */
export function InCall(props: InCallProps) {
  return (
    <div className="page in-call">
      <TopBar center={<CallIsland {...props} />} right={<Controls {...props} />} />
      {props.error && (
        <p className="error call-error" role="alert">
          {props.error}
        </p>
      )}
      <main className="call-grid">
        <CallerPanel
          caller={props.caller}
          memberId={props.memberId}
          intake={props.intake ?? {}}
          waitSecs={props.waitSecs ?? null}
          details={props.details ?? null}
          detailsState={props.detailsState ?? "loading"}
        />
        <AssistColumn />
        <TranscriptColumn />
      </main>
    </div>
  );
}
