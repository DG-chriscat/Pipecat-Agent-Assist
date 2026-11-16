import { FileText, HeartPulse, ListChecks, Mic, MicOff, Pause, PhoneOff, Play, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import tileAluminum from "../assets/tex/aluminum.jpg";
import tileBakelite from "../assets/tex/bakelite.jpg";
import tileStone from "../assets/tex/stone.jpg";
import type { CallerDetails } from "../api/types";
import type { CallerInfo } from "../state/offer";
import { formatClock } from "../state/offer";
import type { HighlightRange, TranscriptLine } from "../state/transcript";
import { CallerPanel, type DetailsState } from "./CallerPanel";
import type { PhiFieldName } from "./PhiField";
import { Shell, type ShellAgent } from "./Shell";
import { TranscriptColumn } from "./TranscriptColumn";
import { VerifiedChip } from "./VerifiedChip";

export type CallPhase = "connecting" | "live";

export interface InCallProps {
  /** Rail avatar menu: agent name, theme, sign out. */
  shell: ShellAgent;
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
  /** Live transcript (P3-4). */
  transcript: TranscriptLine[];
  highlights: HighlightRange[];
  /** AP-17: log a reveal of a masked field on the call record. */
  onRevealPhi: (field: PhiFieldName) => void;
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
        <i key={i} style={{ height: `${Math.round(4 + 14 * b * gain)}px`, animationDelay: `${i * 0.08}s` }} />
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
      <span className="chip chip-neutral" data-testid="mood-chip" title="Tone reader, words only. Arrives with P4-5.">
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
        {muted ? <MicOff strokeWidth={1.7} aria-hidden="true" /> : <Mic strokeWidth={1.7} aria-hidden="true" />}
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
        {held ? <Play strokeWidth={1.7} aria-hidden="true" /> : <Pause strokeWidth={1.7} aria-hidden="true" />}
      </button>
      <button type="button" className="end-btn" aria-label="End call" onClick={onEnd}>
        <PhoneOff strokeWidth={1.7} aria-hidden="true" />
        End
      </button>
    </div>
  );
}

function ShelfTile({ swatch, icon, title, empty }: { swatch: string; icon: ReactNode; title: string; empty: string }) {
  return (
    <div className="panel tile is-empty" data-testid="shelf-tile">
      <div className="tile-top">
        <span className="sw" style={{ backgroundImage: `url(${swatch})` }} aria-hidden="true">
          {icon}
        </span>
        <b>{title}</b>
      </div>
      <p>{empty}</p>
    </div>
  );
}

/**
 * Assist column (DESIGN.md "Assist column"), laid out as Studio with empty states:
 * hero in its listening state, three shelf tiles, Earlier this call, and the Ask bar.
 * P4-4 fills them from the assist contract (docs/assist.md).
 */
function AssistColumn({ firstName }: { firstName: string }) {
  return (
    <section className="assist" aria-label="Assist">
      <div className="hero is-listening">
        <div className="eyebrow">
          <span className="chip chip-ai">
            <Sparkles strokeWidth={1.7} aria-hidden="true" />
            Next best step
          </span>
        </div>
        <div className="hero-listening">
          <div className="orb orb-sm" aria-hidden="true" />
          <div>
            <h2 className="hero-title">Listening with you</h2>
            <p className="hero-why">The next best step shows up here as the call goes, with the words to say.</p>
          </div>
        </div>
      </div>
      <div className="shelf">
        <ShelfTile
          swatch={tileAluminum}
          icon={<FileText strokeWidth={1.7} />}
          title="Records"
          empty="Claims and payments appear when they come up."
        />
        <ShelfTile
          swatch={tileStone}
          icon={<ListChecks strokeWidth={1.7} />}
          title="Call plan"
          empty="Steps for this call appear here."
        />
        <ShelfTile
          swatch={tileBakelite}
          icon={<HeartPulse strokeWidth={1.7} />}
          title="Tone tip"
          empty="A tip appears if the caller's mood drops."
        />
      </div>
      <div className="hist">
        <div className="label">Earlier this call</div>
        <p className="muted-note">Steps you finish show up here.</p>
      </div>
      <label className="panel ask">
        <Sparkles strokeWidth={1.7} aria-hidden="true" />
        <input type="text" aria-label="Ask" placeholder={`Ask about ${firstName}`} disabled />
        <span className="kbd" aria-hidden="true">
          ⌘ K
        </span>
      </label>
    </section>
  );
}

/** In-call frame (DESIGN.md "Layout"): rail, header with call island and controls, three columns. */
export function InCall(props: InCallProps) {
  const firstName = props.caller.name.split(/\s+/)[0] || "the caller";
  return (
    <Shell
      agent={props.shell}
      island={<CallIsland {...props} />}
      right={<Controls {...props} />}
      orb="hero"
      className="in-call"
    >
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
          onRevealPhi={props.onRevealPhi}
        />
        <AssistColumn firstName={firstName} />
        <TranscriptColumn lines={props.transcript} highlights={props.highlights} callerName={props.caller.name} />
      </main>
    </Shell>
  );
}
