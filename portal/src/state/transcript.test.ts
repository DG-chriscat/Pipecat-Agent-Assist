import { describe, expect, it } from "vitest";
import {
  applyTranscript,
  parseTranscriptMessage,
  segmentText,
  type TranscriptLine,
  type TranscriptMessage,
} from "./transcript";

const msg = (over: Partial<TranscriptMessage> = {}): TranscriptMessage => ({
  type: "transcript",
  role: "caller",
  text: "I already",
  is_final: false,
  ts: 100,
  line_id: "caller-1",
  ...over,
});

describe("parseTranscriptMessage", () => {
  it("accepts the bot's transcript server message", () => {
    expect(parseTranscriptMessage(msg())).toEqual(msg());
  });

  it("rejects anything else", () => {
    expect(parseTranscriptMessage(null)).toBeNull();
    expect(parseTranscriptMessage({ type: "card" })).toBeNull();
    expect(parseTranscriptMessage({ ...msg(), role: "bot" })).toBeNull();
    expect(parseTranscriptMessage({ ...msg(), text: 3 })).toBeNull();
    expect(parseTranscriptMessage({ ...msg(), line_id: "" })).toBeNull();
    expect(parseTranscriptMessage({ ...msg(), is_final: "yes" })).toBeNull();
  });
});

describe("applyTranscript", () => {
  it("an interim line updates in place, then the final commits it", () => {
    let lines: TranscriptLine[] = [];
    lines = applyTranscript(lines, msg({ text: "I already" }));
    lines = applyTranscript(lines, msg({ text: "I already called" }));
    expect(lines).toEqual([{ id: "caller-1", role: "caller", text: "I already called", final: false, ts: 100 }]);
    lines = applyTranscript(lines, msg({ text: "I already called last week.", is_final: true, ts: 101 }));
    expect(lines).toEqual([
      { id: "caller-1", role: "caller", text: "I already called last week.", final: true, ts: 101 },
    ]);
  });

  it("a late interim never reopens a committed line", () => {
    let lines = applyTranscript([], msg({ text: "Done.", is_final: true }));
    lines = applyTranscript(lines, msg({ text: "Do" }));
    expect(lines).toEqual([{ id: "caller-1", role: "caller", text: "Done.", final: true, ts: 100 }]);
  });

  it("new lines append in arrival order across roles", () => {
    let lines = applyTranscript([], msg({ is_final: true, text: "Hi." }));
    lines = applyTranscript(lines, msg({ role: "agent", line_id: "agent-1", text: "Hello" }));
    lines = applyTranscript(lines, msg({ line_id: "caller-2", text: "So" }));
    expect(lines.map((l) => [l.id, l.role, l.final])).toEqual([
      ["caller-1", "caller", true],
      ["agent-1", "agent", false],
      ["caller-2", "caller", false],
    ]);
  });

  it("returns the same array when nothing changed", () => {
    const lines = applyTranscript([], msg({ is_final: true }));
    expect(applyTranscript(lines, msg({ is_final: true }))).toBe(lines);
  });
});

describe("segmentText (highlight ranges)", () => {
  it("splits text into plain and marked parts", () => {
    expect(segmentText("I have the referral today", [{ start: 11, end: 19 }])).toEqual([
      { text: "I have the ", mark: false },
      { text: "referral", mark: true },
      { text: " today", mark: false },
    ]);
  });

  it("sorts, merges overlaps, and clamps out of range", () => {
    expect(
      segmentText("prior authorization", [
        { start: 6, end: 40 },
        { start: 0, end: 5 },
        { start: 3, end: 8 },
      ]),
    ).toEqual([{ text: "prior authorization", mark: true }]);
    expect(segmentText("abc", [{ start: 5, end: 9 }])).toEqual([{ text: "abc", mark: false }]);
    expect(segmentText("abc", [])).toEqual([{ text: "abc", mark: false }]);
  });
});
