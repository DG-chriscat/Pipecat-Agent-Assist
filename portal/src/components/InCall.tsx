import { Mic, MicOff, Pause, PhoneOff, Play } from "lucide-react";
import { useMemo } from "react";
import type { CallerDetails } from "../api/types";
import { assistHighlights, emptyAssist, type AssistState, type LocalAction } from "../state/assist";
import type { CallerInfo } from "../state/offer";
import { formatClock } from "../state/offer";
import { MOOD_TOOLTIP, moodChip } from "../state/mood";
import type { HighlightRange, TranscriptLine } from "../state/transcript";
import { AssistColumn } from "./AssistColumn";
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
  /** Assist column (P4-4, docs/assist.md); empty (Listening) when absent. */
  assist?: AssistState;
  /** The agent acted on a card or the call plan. */
  onAssist?: (a: LocalAction) => void;
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
const noop = () => {};
const EMPTY = emptyAssist();

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

function CallIsland({ caller, phase, held, remoteLevel, elapsedSecs, assist }: InCallProps) {
  const status = phase === "connecting" ? "Connecting" : held ? "On hold" : null;
  const mood = moodChip(assist?.mood);
  return (
    <div className={`island call-island${held ? " is-held" : ""}`} role="group" aria-label="Call">
      <span className={`vdot vdot-${caller.verified === "verified" ? "ok" : "warn"}`} aria-hidden="true" />
      <b className="island-name">{caller.name}</b>
      <VerifiedChip result={caller.verified} />
      <Waveform level={held || phase !== "live" ? 0 : remoteLevel} />
      {status && <span className="chip chip-neutral">{status}</span>}
      <span className={`chip chip-${mood.tone}`} data-testid="mood-chip" title={MOOD_TOOLTIP}>
        {mood.text}
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

/** In-call frame (DESIGN.md "Layout"): rail, header with call island and controls, three columns. */
export function InCall(props: InCallProps) {
  const firstName = props.caller.name.split(/\s+/)[0] || "the caller";
  const assist = props.assist ?? EMPTY;
  const onAssist = props.onAssist ?? noop;
  const highlights = useMemo(
    () => [...props.highlights, ...assistHighlights(assist)],
    [props.highlights, assist],
  );
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
          mood={assist.mood}
          onRevealPhi={props.onRevealPhi}
        />
        <AssistColumn assist={assist} onAction={onAssist} firstName={firstName} />
        <TranscriptColumn lines={props.transcript} highlights={highlights} callerName={props.caller.name} />
      </main>
    </Shell>
  );
}
