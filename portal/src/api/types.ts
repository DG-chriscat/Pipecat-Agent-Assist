/** Wire types for the queue REST + websocket protocol (docs/queue.md). */

export type AgentState = "offline" | "available" | "ringing" | "on_call" | "wrap_up";
export type CallStatus = "queued" | "with_agent" | "ended" | "abandoned";
export type IdentityResult = "verified" | "not_verified" | "not_found";
export type Outcome = "completed" | "asked_for_person" | "emergency";

export interface Agent {
  agent_id: string;
  name: string;
  state: AgentState;
  idle_since: number | null;
  offer_call_id: string | null;
  offer_expires_at: number | null;
  call_id: string | null;
}

export interface Call {
  session_id: string;
  room_url: string;
  room_name: string;
  summary: Record<string, unknown>;
  outcome: Outcome | null;
  identity_result: IdentityResult | null;
  member_id: string | null;
  flags: string[];
  status: CallStatus;
  queued_at: number;
  offered_to: string | null;
  agent_id: string | null;
  answered_at: number | null;
  ended_at: number | null;
}

export interface QueueSnapshot {
  queue: Call[];
  active: Call[];
  agents: Agent[];
  offer_timeout_secs: number;
}

export interface AcceptResult {
  session_id: string;
  room_url: string;
  token: string;
  expires_at: number;
  summary: Record<string, unknown>;
  outcome: Outcome | null;
  identity_result: IdentityResult | null;
  member_id: string | null;
  flags: string[];
}

/** Messages the server sends on /ws/queue. */
export type ServerMessage =
  | ({ type: "snapshot" } & QueueSnapshot)
  | { type: "call_queued"; call: Call; position: number }
  | { type: "call_updated"; call: Call }
  | { type: "call_accepted"; agent_id: string; call: Call }
  | { type: "call_ended"; call: Call }
  | { type: "agent_updated"; agent: Agent }
  | { type: "offer"; agent_id: string; call: Call; expires_at: number }
  | { type: "offer_missed"; agent_id: string; session_id: string | null; reason: "timeout" | "declined" }
  | { type: "signed_in"; agent: Agent }
  | { type: "status_set"; agent: Agent }
  | ({ type: "accepted" } & AcceptResult)
  | { type: "declined"; session_id: string }
  | { type: "error"; message: string };

/** Commands the client sends on /ws/queue. */
export type ClientCommand =
  | { type: "sign_in"; name: string }
  | { type: "set_status"; agent_id: string; state: AgentState }
  | { type: "accept"; agent_id: string; session_id: string }
  | { type: "decline"; agent_id: string; session_id: string };
