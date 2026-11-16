/** Client copy of the queue, rebuilt from each snapshot and kept current by events. */

import type { Agent, Call, ServerMessage } from "../api/types";

export interface QueueState {
  /** True once a snapshot arrived on the current connection. */
  synced: boolean;
  queue: Call[];
  active: Call[];
  agents: Record<string, Agent>;
  offerTimeoutSecs: number;
}

export const emptyQueueState: QueueState = {
  synced: false,
  queue: [],
  active: [],
  agents: {},
  offerTimeoutSecs: 20,
};

const without = (calls: Call[], id: string) => calls.filter((c) => c.session_id !== id);

function upsertAgent(s: QueueState, agent: Agent): QueueState {
  return { ...s, agents: { ...s.agents, [agent.agent_id]: agent } };
}

export function applyMessage(s: QueueState, msg: ServerMessage): QueueState {
  switch (msg.type) {
    case "snapshot":
      return {
        synced: true,
        queue: msg.queue,
        active: msg.active,
        agents: Object.fromEntries(msg.agents.map((a) => [a.agent_id, a])),
        offerTimeoutSecs: msg.offer_timeout_secs,
      };
    case "call_queued": {
      const queue = without(s.queue, msg.call.session_id);
      const at = Math.max(0, Math.min(queue.length, msg.position - 1));
      queue.splice(at, 0, msg.call);
      return { ...s, queue };
    }
    case "call_updated": {
      const id = msg.call.session_id;
      const replace = (calls: Call[]) => calls.map((c) => (c.session_id === id ? msg.call : c));
      return { ...s, queue: replace(s.queue), active: replace(s.active) };
    }
    case "call_accepted": {
      const id = msg.call.session_id;
      return { ...s, queue: without(s.queue, id), active: [...without(s.active, id), msg.call] };
    }
    case "call_ended": {
      const id = msg.call.session_id;
      return { ...s, queue: without(s.queue, id), active: without(s.active, id) };
    }
    case "agent_updated":
    case "signed_in":
    case "status_set":
      return upsertAgent(s, msg.agent);
    default:
      return s;
  }
}
