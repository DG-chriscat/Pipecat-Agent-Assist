import { describe, expect, it } from "vitest";
import { applyMessage, emptyQueueState } from "./queueState";
import type { Agent, Call, ServerMessage } from "../api/types";

const agent = (over: Partial<Agent> = {}): Agent => ({
  agent_id: "agent-1",
  name: "Ana",
  state: "offline",
  idle_since: null,
  offer_call_id: null,
  offer_expires_at: null,
  call_id: null,
  ...over,
});

const call = (id: string, over: Partial<Call> = {}): Call => ({
  session_id: id,
  room_url: `https://x.daily.co/${id}`,
  room_name: id,
  summary: {},
  outcome: "completed",
  identity_result: "verified",
  member_id: null,
  flags: [],
  status: "queued",
  queued_at: 1,
  offered_to: null,
  agent_id: null,
  answered_at: null,
  ended_at: null,
  ...over,
});

const snap = (queue: Call[] = [], agents: Agent[] = []): ServerMessage => ({
  type: "snapshot",
  queue,
  active: [],
  agents,
  offer_timeout_secs: 20,
});

describe("applyMessage", () => {
  it("starts empty and not synced", () => {
    expect(emptyQueueState.synced).toBe(false);
    expect(emptyQueueState.queue).toEqual([]);
  });

  it("snapshot replaces all state and marks synced", () => {
    const s = applyMessage(emptyQueueState, snap([call("a")], [agent()]));
    expect(s.synced).toBe(true);
    expect(s.queue.map((c) => c.session_id)).toEqual(["a"]);
    expect(s.agents["agent-1"].name).toBe("Ana");
    const s2 = applyMessage(s, snap([], []));
    expect(s2.queue).toEqual([]);
    expect(s2.agents).toEqual({});
  });

  it("call_queued inserts at its position", () => {
    let s = applyMessage(emptyQueueState, snap([call("a"), call("b")]));
    s = applyMessage(s, { type: "call_queued", call: call("c"), position: 2 });
    expect(s.queue.map((c) => c.session_id)).toEqual(["a", "c", "b"]);
    s = applyMessage(s, { type: "call_queued", call: call("c", { flags: ["x"] }), position: 2 });
    expect(s.queue.map((c) => c.session_id)).toEqual(["a", "c", "b"]);
    expect(s.queue[1].flags).toEqual(["x"]);
  });

  it("call_accepted and call_ended take the call out of the queue", () => {
    let s = applyMessage(emptyQueueState, snap([call("a"), call("b")]));
    s = applyMessage(s, {
      type: "call_accepted",
      agent_id: "agent-1",
      call: call("a", { status: "with_agent" }),
    });
    expect(s.queue.map((c) => c.session_id)).toEqual(["b"]);
    expect(s.active.map((c) => c.session_id)).toEqual(["a"]);
    s = applyMessage(s, { type: "call_ended", call: call("a", { status: "ended" }) });
    s = applyMessage(s, { type: "call_ended", call: call("b", { status: "abandoned" }) });
    expect(s.queue).toEqual([]);
    expect(s.active).toEqual([]);
  });

  it("call_updated updates a queued call in place", () => {
    let s = applyMessage(emptyQueueState, snap([call("a"), call("b")]));
    s = applyMessage(s, { type: "call_updated", call: call("a", { offered_to: "agent-1" }) });
    expect(s.queue.map((c) => c.session_id)).toEqual(["a", "b"]);
    expect(s.queue[0].offered_to).toBe("agent-1");
  });

  it("agent_updated, signed_in, and status_set upsert the agent", () => {
    let s = applyMessage(emptyQueueState, snap());
    s = applyMessage(s, { type: "signed_in", agent: agent() });
    expect(s.agents["agent-1"].state).toBe("offline");
    s = applyMessage(s, { type: "agent_updated", agent: agent({ state: "available" }) });
    expect(s.agents["agent-1"].state).toBe("available");
    s = applyMessage(s, { type: "status_set", agent: agent({ state: "offline" }) });
    expect(s.agents["agent-1"].state).toBe("offline");
  });

  it("returns the same state for messages it does not track", () => {
    const s = applyMessage(emptyQueueState, snap());
    expect(applyMessage(s, { type: "error", message: "x" })).toBe(s);
  });
});
