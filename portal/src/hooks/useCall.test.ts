import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { AcceptResult } from "../api/types";
import { heroCard, msg, planMsg, recordCard } from "../test/assistFixtures";
import { createFakeMedia, FakeMedia } from "../test/fakeMedia";
import { useCall } from "./useCall";

const res: AcceptResult = {
  session_id: "s1",
  room_url: "https://x.daily.co/r1",
  token: "t",
  expires_at: 0,
  summary: { full_name: "Maria Alvarez", reason: "MRI claim" },
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
};

const tmsg = (line_id: string, text: string, is_final = true) => ({
  type: "transcript",
  role: line_id.split("-")[0],
  text,
  is_final,
  ts: 1,
  line_id,
});

describe("useCall transcript (P3-4)", () => {
  beforeEach(() => FakeMedia.reset());

  it("collects transcript lines from server messages and starts empty on each call", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    expect(result.current.call?.transcript).toEqual([]);
    act(() => FakeMedia.last().events.onServerMessage(tmsg("caller-1", "Hi.")));
    expect(result.current.call?.transcript.map((l) => l.text)).toEqual(["Hi."]);
    act(() => result.current.start({ ...res, session_id: "s2" }));
    expect(result.current.call?.transcript).toEqual([]);
  });

  it("ignores messages from a media session that is no longer current", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    const old = FakeMedia.last();
    act(() => result.current.start({ ...res, session_id: "s2" }));
    act(() => old.events.onServerMessage(tmsg("caller-1", "stale")));
    expect(result.current.call?.transcript).toEqual([]);
  });

  it("highlight(ranges) sets the trigger word ranges; a new call clears them", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    act(() => result.current.highlight([{ lineId: "caller-1", start: 0, end: 3 }]));
    expect(result.current.call?.highlights).toEqual([{ lineId: "caller-1", start: 0, end: 3 }]);
    act(() => result.current.start({ ...res, session_id: "s2" }));
    expect(result.current.call?.highlights).toEqual([]);
  });
});

describe("useCall warm up while ringing (P3-5: accept to audio under 2 s)", () => {
  beforeEach(() => FakeMedia.reset());

  it("prepare() warms one media session; start() joins with that same session", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.prepare());
    expect(FakeMedia.instances).toHaveLength(1);
    expect(FakeMedia.last().prepared).toBe(1);
    act(() => result.current.prepare()); // ringing re-renders do not warm twice
    expect(FakeMedia.instances).toHaveLength(1);
    act(() => result.current.start(res));
    expect(FakeMedia.instances).toHaveLength(1);
    expect(FakeMedia.last().joined).toEqual({ roomUrl: res.room_url, token: "t" });
    act(() => FakeMedia.last().events.onServerMessage(tmsg("caller-1", "Hi.")));
    expect(result.current.call?.transcript.map((l) => l.text)).toEqual(["Hi."]);
  });

  it("discard() releases a warmed session that was never used", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.prepare());
    const warm = FakeMedia.last();
    act(() => result.current.discard());
    expect(warm.left).toBe(1);
    act(() => result.current.start(res));
    expect(FakeMedia.instances).toHaveLength(2); // a fresh one
  });

  it("start() without prepare() still works", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    expect(FakeMedia.last().prepared).toBe(0);
    expect(FakeMedia.last().joined).not.toBeNull();
  });
});

describe("useCall assist (P4-4, docs/assist.md)", () => {
  beforeEach(() => FakeMedia.reset());

  it("applies assist messages, ignores other versions, and starts empty on each call", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    expect(result.current.call?.assist.hero).toBeNull();
    act(() => FakeMedia.last().events.onServerMessage({ ...msg("hero", { card: heroCard(), reason: "new" }), v: 2 }));
    expect(result.current.call?.assist.hero).toBeNull();
    act(() => FakeMedia.last().events.onServerMessage(msg("hero", { card: heroCard(), reason: "new", replaces: null })));
    expect(result.current.call?.assist.hero?.id).toBe(heroCard().id);
    act(() => FakeMedia.last().events.onServerMessage(planMsg()));
    expect(result.current.call?.assist.plan?.steps).toHaveLength(4);
    act(() => result.current.start({ ...res, session_id: "s2" }));
    expect(result.current.call?.assist.hero).toBeNull();
    expect(result.current.call?.assist.plan).toBeNull();
  });

  it("sends each feedback action to the bot as an assist_feedback client message", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    const m = FakeMedia.last();
    act(() => m.events.onServerMessage(planMsg()));
    act(() => m.events.onServerMessage(msg("card_added", { card: recordCard() })));
    act(() => m.events.onServerMessage(msg("hero", { card: heroCard(), reason: "new", replaces: null })));
    act(() => result.current.assistAction({ type: "pin", cardId: "card:denied_claim" }));
    act(() => result.current.assistAction({ type: "unpin", cardId: "card:denied_claim" }));
    act(() => result.current.assistAction({ type: "rate", cardId: "card:denied_claim", rating: "down" }));
    act(() => result.current.assistAction({ type: "dismiss", cardId: "card:denied_claim" }));
    act(() => result.current.assistAction({ type: "accept", cardId: heroCard().id }));
    act(() => result.current.assistAction({ type: "tick", stepId: "confirm_claim", done: true }));
    act(() => result.current.assistAction({ type: "settle" }));
    expect(m.sent).toEqual([
      ["assist_feedback", { v: 1, card_id: "card:denied_claim", action: "pin" }],
      ["assist_feedback", { v: 1, card_id: "card:denied_claim", action: "unpin" }],
      ["assist_feedback", { v: 1, card_id: "card:denied_claim", action: "rate", rating: "down" }],
      ["assist_feedback", { v: 1, card_id: "card:denied_claim", action: "dismiss" }],
      ["assist_feedback", { v: 1, card_id: heroCard().id, action: "accept" }],
    ]);
    expect(result.current.call?.assist.history.map((h) => h.label)).toEqual([
      "Request retro auth review",
      "Confirm which claim the caller means",
    ]);
  });

  it("does not send feedback for a hero that is not on screen", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    act(() => result.current.assistAction({ type: "accept", cardId: heroCard().id }));
    expect(FakeMedia.last().sent).toEqual([]);
  });

  it("sends an Ask as an assist_ask client message and tracks it (P4-6)", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    act(() => result.current.assistAction({ type: "ask", askId: "q1", text: " Show the last 3 claims " }));
    expect(FakeMedia.last().sent).toEqual([["assist_ask", { v: 1, ask_id: "q1", text: "Show the last 3 claims" }]]);
    expect(result.current.call?.assist.asks.map((a) => a.status)).toEqual(["pending"]);
    act(() =>
      FakeMedia.last().events.onServerMessage(
        msg("ask_result", { ask_id: "q1", status: "no_answer", card_id: null, message: "No." }),
      ),
    );
    expect(result.current.call?.assist.asks[0].status).toBe("no_answer");
  });
});
