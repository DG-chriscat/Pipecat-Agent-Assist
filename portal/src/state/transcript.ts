/**
 * Live transcript (P3-4). The bot sends one RTVI server message per transcript update,
 * to the agent only:
 *   {type: "transcript", role: "caller" | "agent", text, is_final, ts, line_id}
 * Interim updates reuse the line_id of the line in progress and replace its text;
 * the final with that id commits it.
 */

export type SpeakerRole = "caller" | "agent";

export interface TranscriptMessage {
  type: "transcript";
  role: SpeakerRole;
  text: string;
  is_final: boolean;
  /** Bot clock, epoch seconds. */
  ts: number;
  line_id: string;
}

export interface TranscriptLine {
  id: string;
  role: SpeakerRole;
  text: string;
  final: boolean;
  ts: number;
}

/** Character range [start, end) inside one line's text, for trigger words. */
export interface TextRange {
  start: number;
  end: number;
}

/** A highlight for `highlight(ranges)`: which line, which characters. */
export interface HighlightRange extends TextRange {
  lineId: string;
}

export function parseTranscriptMessage(data: unknown): TranscriptMessage | null {
  if (typeof data !== "object" || data === null) return null;
  const m = data as Record<string, unknown>;
  if (m.type !== "transcript") return null;
  if (m.role !== "caller" && m.role !== "agent") return null;
  if (typeof m.text !== "string" || typeof m.is_final !== "boolean") return null;
  if (typeof m.ts !== "number" || typeof m.line_id !== "string" || !m.line_id) return null;
  return {
    type: "transcript",
    role: m.role,
    text: m.text,
    is_final: m.is_final,
    ts: m.ts,
    line_id: m.line_id,
  };
}

export function applyTranscript(lines: TranscriptLine[], m: TranscriptMessage): TranscriptLine[] {
  const i = lines.findIndex((l) => l.id === m.line_id);
  const next: TranscriptLine = { id: m.line_id, role: m.role, text: m.text, final: m.is_final, ts: m.ts };
  if (i === -1) return [...lines, next];
  const cur = lines[i];
  if (cur.final) return lines; // committed; late interims and repeats change nothing
  if (cur.text === next.text && cur.final === next.final) return lines;
  const out = lines.slice();
  out[i] = next;
  return out;
}

export interface Segment {
  text: string;
  mark: boolean;
}

/** Split text into plain and highlighted parts. Ranges may overlap or run past the end. */
export function segmentText(text: string, ranges: TextRange[]): Segment[] {
  const clamped = ranges
    .map((r) => ({ start: Math.max(0, r.start), end: Math.min(text.length, r.end) }))
    .filter((r) => r.end > r.start)
    .sort((a, b) => a.start - b.start);
  const merged: TextRange[] = [];
  for (const r of clamped) {
    const last = merged.at(-1);
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ ...r });
  }
  const out: Segment[] = [];
  let pos = 0;
  for (const r of merged) {
    if (r.start > pos) out.push({ text: text.slice(pos, r.start), mark: false });
    out.push({ text: text.slice(r.start, r.end), mark: true });
    pos = r.end;
  }
  if (pos < text.length || out.length === 0) out.push({ text: text.slice(pos), mark: false });
  return out;
}
