import { describe, expect, it } from "vitest";
import type { Agent, Call } from "../api/types";
import { callerInfo, formatClock, formatWait, offerFor, secondsLeft } from "./offer";

const call = (over: Partial<Call> = {}): Call => ({
  session_id: "s1",
  room_url: "https://x.daily.co/r1",
  room_name: "r1",
  summary: { full_name: "Maria Alvarez", reason: "MRI claim was denied", summary: "Denied MRI claim" },
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
  status: "queued",
  queued_at: 1000,
  offered_to: "a1",
  agent_id: null,
  answered_at: null,
  ended_at: null,
  ...over,
});

const agent = (over: Partial<Agent> = {}): Agent => ({
  agent_id: "a1",
  name: "Ana",
  state: "ringing",
  idle_since: null,
  offer_call_id: "s1",
  offer_expires_at: 1120,
  call_id: null,
  ...over,
});

describe("callerInfo", () => {
  it("reads name, verified status, and reason from the intake summary", () => {
    expect(callerInfo(call())).toEqual({
      name: "Maria Alvarez",
      verified: "verified",
      identity: "verified",
      reason: "MRI claim was denied",
      summary: "Denied MRI claim",
    });
  });

  it("falls back when intake did not capture a name or reason", () => {
    const info = callerInfo(call({ summary: {}, identity_result: null }));
    expect(info.name).toBe("Unknown caller");
    expect(info.reason).toBe("No reason captured");
    expect(info.verified).toBe("not_verified");
    expect(info.identity).toBeNull();
  });

  it("uses the one line summary when there is no reason", () => {
    expect(callerInfo(call({ summary: { summary: "Wants a claim status" } })).reason).toBe("Wants a claim status");
  });
});

describe("offerFor", () => {
  it("rebuilds an open offer for this agent from the snapshot (reload while ringing)", () => {
    const o = offerFor(agent(), [call()], null);
    expect(o).toEqual({ call: call(), expiresAt: 1120 });
  });

  it("prefers the offer event when it matches the agent's offer", () => {
    const fromEvent = { call: call({ queued_at: 999 }), expiresAt: 1121 };
    expect(offerFor(agent(), [call()], fromEvent)).toBe(fromEvent);
  });

  it("is null when the agent is not ringing", () => {
    expect(offerFor(agent({ state: "available", offer_call_id: null }), [call()], null)).toBeNull();
  });

  it("is null when an event offer is for another call", () => {
    const stale = { call: call({ session_id: "old" }), expiresAt: 1 };
    expect(offerFor(agent(), [], stale)).toBeNull();
  });
});

describe("secondsLeft", () => {
  it("counts down whole seconds to the deadline and stops at zero", () => {
    expect(secondsLeft(1120, 1100_000, 20)).toBe(20);
    expect(secondsLeft(1120, 1100_400, 20)).toBe(20);
    expect(secondsLeft(1120, 1101_000, 20)).toBe(19);
    expect(secondsLeft(1120, 1119_999, 20)).toBe(1);
    expect(secondsLeft(1120, 1120_000, 20)).toBe(0);
    expect(secondsLeft(1120, 1130_000, 20)).toBe(0);
  });

  it("never shows more than the offer window (clock skew)", () => {
    expect(secondsLeft(1200, 1100_000, 20)).toBe(20);
  });
});

describe("formatting", () => {
  it("formats wait time in plain words", () => {
    expect(formatWait(8)).toBe("8 s");
    expect(formatWait(102)).toBe("1 min 42 s");
    expect(formatWait(120)).toBe("2 min");
    expect(formatWait(-3)).toBe("0 s");
  });

  it("formats the call timer as mm:ss", () => {
    expect(formatClock(0)).toBe("00:00");
    expect(formatClock(252)).toBe("04:12");
    expect(formatClock(3725)).toBe("62:05");
  });
});
