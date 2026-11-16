import { useLayoutEffect, useRef, useState } from "react";
import { segmentText, type HighlightRange, type TranscriptLine } from "../state/transcript";

/** Within this many px of the bottom counts as "at the bottom" (autoscroll on). */
export const BOTTOM_SLACK_PX = 24;

const LABEL = { caller: "Caller", agent: "You" } as const;

export interface TranscriptColumnProps {
  lines: TranscriptLine[];
  /** Trigger word ranges to underline (Phase 4 sets these through highlight()). */
  highlights: HighlightRange[];
}

function LineText({ line, highlights }: { line: TranscriptLine; highlights: HighlightRange[] }) {
  const ranges = highlights.filter((h) => h.lineId === line.id);
  return (
    <p className={line.final ? undefined : "typing"}>
      {segmentText(line.text, ranges).map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : s.text))}
    </p>
  );
}

/**
 * Transcript column (DESIGN.md "Transcript (right)"): live chip, CALLER in fuchsia, YOU
 * muted, a fuchsia caret on the line still being spoken, trigger words underlined.
 * Follows new lines while the agent is at the bottom; scrolling up pauses that
 * ("Nothing moves under the cursor") until they scroll back down or jump to latest.
 */
export function TranscriptColumn({ lines, highlights }: TranscriptColumnProps) {
  const box = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [lines, follow]);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    setFollow(atBottom);
  };

  const jump = () => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
    setFollow(true);
  };

  return (
    <aside className="glass transcript" aria-label="Transcript">
      <div className="transcript-head">
        <span className="label">Live transcript</span>
        <span className="chip chip-bad">
          <span className="live-dot" aria-hidden="true" />
          Live
        </span>
      </div>
      <div className="transcript-scroll" data-testid="transcript-scroll" ref={box} onScroll={onScroll}>
        {lines.length === 0 ? (
          <p className="muted-note">The transcript shows up here once the caller and you talk.</p>
        ) : (
          <ol className="tr-list" aria-live="polite">
            {lines.map((l) => (
              <li key={l.id} className={`tr tr-${l.role}`} aria-busy={!l.final} data-line-id={l.id}>
                <span className="who">{LABEL[l.role]}</span>
                <LineText line={l} highlights={highlights} />
              </li>
            ))}
          </ol>
        )}
      </div>
      {!follow && (
        <button type="button" className="chip chip-neutral jump-latest" onClick={jump}>
          Jump to latest
        </button>
      )}
    </aside>
  );
}
