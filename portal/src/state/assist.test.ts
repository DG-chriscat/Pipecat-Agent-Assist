import { describe, expect, it } from "vitest";
import { heroCard, msg, PLAN_STEPS, planMsg, recordCard } from "../test/assistFixtures";
import {
  applyAssist,
  assistHighlights,
  emptyAssist,
  localAssist,
  parseAssistMessage,
  planView,
  type AssistState,
} from "./assist";

function apply(s: AssistState, raw: Record<string, unknown>, at = 10): AssistState {
  const m = parseAssistMessage(raw);
  if (!m) throw new Error(`not parsed: ${JSON.stringify(raw)}`);
  return applyAssist(s, m, at);
}

const tickFirst = () => PLAN_STEPS.map((p, i) => (i === 0 ? { ...p, done: true, by: "transcript" as const } : p));

describe("parseAssistMessage (contract v1, docs/assist.md)", () => {
  it("parses each v1 event", () => {
    expect(parseAssistMessage(msg("assist_status", { state: "listening", pack_id: "claims_status" }))).toMatchObject({
      event: "assist_status",
      state: "listening",
    });
    expect(parseAssistMessage(msg("hero", { card: heroCard(), reason: "new", replaces: null }))).toMatchObject({
      event: "hero",
      card: { id: "nbs:retro_auth_review:CLM-900001" },
    });
    expect(parseAssistMessage(msg("hero", { card: null, reason: "accepted", replaces: "x" }))).toMatchObject({
      event: "hero",
      card: null,
    });
    expect(parseAssistMessage(msg("card_added", { card: recordCard() }))).toMatchObject({ event: "card_added" });
    expect(parseAssistMessage(msg("card_removed", { card_id: "card:x", reason: "dismissed" }))).toMatchObject({
      event: "card_removed",
      card_id: "card:x",
    });
    expect(parseAssistMessage(planMsg())).toMatchObject({ event: "plan_update", steps: PLAN_STEPS });
  });

  it("ignores unknown versions, unknown events, other message types, and junk", () => {
    expect(parseAssistMessage({ ...msg("hero", { card: heroCard(), reason: "new" }), v: 2 })).toBeNull();
    expect(parseAssistMessage({ ...msg("hero", { card: heroCard(), reason: "new" }), v: "1" })).toBeNull();
    expect(parseAssistMessage({ ...msg("hero", { card: heroCard(), reason: "new" }), v: undefined })).toBeNull();
    expect(parseAssistMessage(msg("tone_tip", {}))).toBeNull();
    expect(parseAssistMessage({ type: "transcript", v: 1, event: "hero" })).toBeNull();
    expect(parseAssistMessage(null)).toBeNull();
    expect(parseAssistMessage("assist")).toBeNull();
  });

  it("rejects malformed cards and steps", () => {
    expect(parseAssistMessage(msg("card_added", { card: { id: "x" } }))).toBeNull();
    expect(parseAssistMessage(msg("card_added", { card: recordCard({ kind: "poem" as never }) }))).toBeNull();
    expect(parseAssistMessage(msg("hero", { card: recordCard({ body: {} }), reason: "new" }))).toBeNull();
    expect(parseAssistMessage(msg("plan_update", { steps: [{ id: 1 }] }))).toBeNull();
    expect(parseAssistMessage(msg("card_removed", {}))).toBeNull();
  });
});

describe("applyAssist", () => {
  it("hero replaces the hero; null goes back to listening", () => {
    let s = apply(emptyAssist(), msg("hero", { card: heroCard(), reason: "new", replaces: null }));
    expect(s.hero?.id).toBe("nbs:retro_auth_review:CLM-900001");
    s = apply(s, msg("hero", { card: null, reason: "dismissed", replaces: s.hero?.id }));
    expect(s.hero).toBeNull();
  });

  it("card_added appends, the same id replaces in place, card_removed removes", () => {
    let s = apply(emptyAssist(), msg("card_added", { card: recordCard() }));
    s = apply(s, msg("card_added", { card: recordCard({ id: "kb:KB-CL-002", kind: "article", title: "Knowledge base" }) }));
    s = apply(s, msg("card_added", { card: recordCard({ title: "Denied claim (updated)" }) }));
    expect(s.cards.map((c) => c.id)).toEqual(["card:denied_claim", "kb:KB-CL-002"]);
    expect(s.cards[0].title).toBe("Denied claim (updated)");
    s = apply(s, msg("card_removed", { card_id: "card:denied_claim", reason: "dismissed" }));
    expect(s.cards.map((c) => c.id)).toEqual(["kb:KB-CL-002"]);
  });

  it("ignores stale or repeated sequence numbers", () => {
    const first = msg("hero", { card: heroCard(), reason: "new", replaces: null });
    const later = msg("hero", { card: null, reason: "dismissed", replaces: null });
    let s = apply(emptyAssist(), later);
    s = apply(s, first);
    expect(s.hero).toBeNull();
  });

  it("plan_update replaces the plan and logs steps the bot ticked in Earlier this call", () => {
    let s = apply(emptyAssist(), planMsg(), 5);
    expect(s.plan?.steps).toHaveLength(4);
    expect(s.history).toEqual([]);
    s = apply(s, planMsg(tickFirst()), 42);
    expect(s.history).toEqual([
      expect.objectContaining({ key: "step:confirm_claim", label: "Confirm which claim the caller means", atSecs: 42 }),
    ]);
    // Re-sending the same plan (resync) does not log it twice.
    s = apply(s, planMsg(tickFirst()), 60);
    expect(s.history).toHaveLength(1);
  });

  it("status keeps the rate limit backlog", () => {
    const s = apply(emptyAssist(), msg("assist_status", { state: "rate_limited", pending: 2, pack_id: "claims_status" }));
    expect(s.status).toEqual({ state: "rate_limited", pending: 2 });
  });
});

describe("localAssist (agent actions)", () => {
  const withHero = () => apply(emptyAssist(), msg("hero", { card: heroCard(), reason: "new", replaces: null }));

  it("accept shows the accepted state and logs the simulated action", () => {
    const s = localAssist(withHero(), { type: "accept", cardId: heroCard().id }, 130);
    expect(s.ack).toEqual({ card: heroCard(), outcome: "accepted" });
    expect(s.history).toEqual([
      expect.objectContaining({
        key: `action:${heroCard().id}`,
        label: "Request retro auth review",
        detail: "Simulated · CLM-900001",
        atSecs: 130,
      }),
    ]);
    // The bot's next hero lands underneath while the ack shows; settle reveals it.
    const next = apply(s, msg("hero", { card: null, reason: "accepted", replaces: heroCard().id }), 131);
    expect(next.ack?.outcome).toBe("accepted");
    expect(localAssist(next, { type: "settle" }, 132).ack).toBeNull();
  });

  it("accept on a card that is not the hero does nothing", () => {
    const s0 = withHero();
    expect(localAssist(s0, { type: "accept", cardId: "nbs:other:X" }, 1)).toBe(s0);
  });

  it("dismiss shows the dismissed state, logs nothing, and drops the hero locally", () => {
    const s = localAssist(withHero(), { type: "dismiss", cardId: heroCard().id }, 50);
    expect(s.ack?.outcome).toBe("dismissed");
    expect(s.hero).toBeNull();
    expect(s.history).toEqual([]);
  });

  it("a late echo of the dismissed hero is not shown again", () => {
    let s = localAssist(withHero(), { type: "dismiss", cardId: heroCard().id }, 50);
    s = apply(s, msg("card_added", { card: heroCard() }));
    s = apply(s, msg("hero", { card: heroCard(), reason: "new", replaces: null }));
    expect(s.hero).toBeNull();
    expect(s.cards).toEqual([]);
  });

  it("dismissing a supporting card removes it at once", () => {
    const s0 = apply(emptyAssist(), msg("card_added", { card: recordCard() }));
    const s = localAssist(s0, { type: "dismiss", cardId: "card:denied_claim" }, 50);
    expect(s.cards).toEqual([]);
    expect(s.ack).toBeNull();
  });

  it("pin, unpin, and rate are portal state", () => {
    let s = apply(emptyAssist(), msg("card_added", { card: recordCard() }));
    s = localAssist(s, { type: "pin", cardId: "card:denied_claim" }, 1);
    expect(s.pinned).toEqual(["card:denied_claim"]);
    s = localAssist(s, { type: "unpin", cardId: "card:denied_claim" }, 1);
    expect(s.pinned).toEqual([]);
    s = localAssist(s, { type: "rate", cardId: "card:denied_claim", rating: "up" }, 1);
    expect(s.ratings).toEqual({ "card:denied_claim": "up" });
    s = localAssist(s, { type: "rate", cardId: "card:denied_claim", rating: "down" }, 1);
    expect(s.ratings).toEqual({ "card:denied_claim": "down" });
  });

  it("manual tick and untick override the bot's ticks (AP-12)", () => {
    let s = apply(emptyAssist(), planMsg(tickFirst()), 10);
    s = localAssist(s, { type: "tick", stepId: "explain_status", done: true }, 70);
    let v = planView(s)!;
    expect(v.done).toBe(2);
    expect(v.steps[1]).toMatchObject({ done: true, by: "manual" });
    expect(v.next).toBe("take_next_step");
    expect(s.history.map((h) => h.key)).toEqual(["step:confirm_claim", "step:explain_status"]);
    s = localAssist(s, { type: "tick", stepId: "confirm_claim", done: false }, 80);
    v = planView(s)!;
    expect(v.steps[0]).toMatchObject({ done: false });
    expect(v.done).toBe(1);
    expect(v.next).toBe("confirm_claim");
    expect(s.history.map((h) => h.key)).toEqual(["step:explain_status"]);
    // A later bot update does not undo the agent's untick.
    s = apply(s, planMsg(tickFirst()), 90);
    expect(planView(s)!.steps[0].done).toBe(false);
  });
});

describe("assistHighlights", () => {
  it("underlines each card's trigger words; cards without a phrase are skipped", () => {
    let s = apply(emptyAssist(), msg("hero", { card: heroCard(), reason: "new", replaces: null }));
    const noPhrase = recordCard({
      id: "kb:x",
      trigger: { ...heroCard().trigger, phrase: null, start: null, end: null, line_id: "caller-9" },
    });
    s = apply(s, msg("card_added", { card: noPhrase }));
    expect(assistHighlights(s)).toEqual([{ lineId: "caller-3", start: 28, end: 34 }]);
  });
});
