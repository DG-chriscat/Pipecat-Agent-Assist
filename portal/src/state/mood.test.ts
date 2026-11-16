import { describe, expect, it } from "vitest";
import { moodMsg, msg, reading, toneTipCard } from "../test/assistFixtures";
import { applyAssist, emptyAssist, parseAssistMessage, type AssistState } from "./assist";
import { moodBars, moodChip } from "./mood";

function apply(s: AssistState, raw: Record<string, unknown>): AssistState {
  const m = parseAssistMessage(raw);
  if (!m) throw new Error(`not parsed: ${JSON.stringify(raw)}`);
  return applyAssist(s, m, 10);
}

describe("mood message (P4-5, AP-14)", () => {
  it("parses the mood trend with every reading", () => {
    const m = parseAssistMessage(moodMsg([1, 3], "Worried, worsening"));
    expect(m).toMatchObject({
      event: "mood",
      label: "Worried, worsening",
      tone: "warn",
      step: 3,
      readings: [
        { line_id: "caller-1", step: 1, mood: "Calm" },
        { line_id: "caller-2", step: 3, mood: "Worried" },
      ],
    });
  });

  it("drops malformed readings and rejects a mood without a readings list", () => {
    const m = parseAssistMessage(
      msg("mood", { label: "Calm", tone: "ok", step: 1, readings: [reading(1, "caller-1"), { step: "x" }] }),
    );
    expect(m && m.event === "mood" && m.readings).toHaveLength(1);
    expect(parseAssistMessage(msg("mood", { label: "Calm" }))).toBeNull();
  });

  it("keeps the latest mood in the assist state", () => {
    let s = apply(emptyAssist(), moodMsg([1], "Calm", "ok"));
    s = apply(s, moodMsg([1, 4], "Frustrated, worsening", "bad"));
    expect(s.mood?.label).toBe("Frustrated, worsening");
    expect(s.mood?.readings.map((r) => r.step)).toEqual([1, 4]);
  });

  it("accepts tone tip cards and replaced removals", () => {
    let s = apply(emptyAssist(), msg("card_added", { card: toneTipCard() }));
    expect(s.cards.map((c) => c.kind)).toEqual(["tone_tip"]);
    s = apply(s, msg("card_removed", { card_id: "tone:caller-4", reason: "replaced" }));
    expect(s.cards).toEqual([]);
  });

  it("rejects a tone tip without a headline", () => {
    expect(parseAssistMessage(msg("card_added", { card: toneTipCard({ body: {} }) }))).toBeNull();
  });
});

describe("moodBars and moodChip", () => {
  it("one bar per reading, 0 calm to 1 upset", () => {
    expect(moodBars(null)).toEqual([]);
    const s = apply(emptyAssist(), moodMsg([0, 1, 3, 4], "Frustrated, worsening", "bad"));
    expect(moodBars(s.mood)).toEqual([0, 0.25, 0.75, 1]);
  });

  it("chip shows the label with its tone, a dash before any reading", () => {
    expect(moodChip(null)).toEqual({ text: "Mood –", tone: "neutral" });
    const s = apply(emptyAssist(), moodMsg([1, 3], "Worried, worsening"));
    expect(moodChip(s.mood)).toEqual({ text: "Worried, worsening", tone: "warn" });
  });
});
