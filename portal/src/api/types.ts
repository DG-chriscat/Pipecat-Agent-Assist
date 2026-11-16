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
  /** Who ended a call the agent was on (P3-5). */
  ended_by?: "agent" | "caller" | null;
  wrap_up?: SavedWrapUp | null;
}

export interface SavedWrapUp {
  reason_code: string;
  notes: string;
  /** P5-1: the summary as the agent saved it. */
  summary?: string;
  saved_at: number;
}

export interface ReasonCode {
  code: string;
  label: string;
}

/** Wrap up view data: GET /calls/{session_id}/wrap-up (P3-5). */
export interface WrapUpInfo {
  session_id: string;
  status: CallStatus;
  ended_by: "agent" | "caller" | null;
  caller_name: string;
  reason: string;
  identity_result: IdentityResult | null;
  duration_secs: number;
  /** AP-9 (P5-1): the assist's draft summary; null until it lands (or if none). */
  summary_draft: string | null;
  /** AP-9 (P5-1): the reason code the assist suggests; preselected in the view. */
  suggested_reason_code: string | null;
  /** The bot is still writing the draft (poll the view again). */
  draft_pending: boolean;
  /** The pack the reason codes come from. */
  pack_id: string;
  reason_codes: ReasonCode[];
  wrap_up: SavedWrapUp | null;
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

/** Caller panel data: GET /calls/{session_id}/caller (docs/queue.md, P3-3). */
export interface CallerMember {
  member_id: string;
  name: string;
  first_name: string;
  last_name: string;
  /** ISO date, YYYY-MM-DD. */
  dob: string;
  plan_id: string;
  plan_name: string;
  plan_type: string;
}

export type CallerEventKind = "call" | "claim" | "referral";

export interface CallerEvent {
  kind: CallerEventKind;
  ref: string;
  /** ISO date, YYYY-MM-DD. */
  occurred_on: string;
  status: string;
  summary: string;
}

export interface CallerDetails {
  session_id: string;
  identity_result: IdentityResult | null;
  not_verified: boolean;
  outcome: Outcome | null;
  flags: string[];
  intake: Record<string, unknown>;
  wait_secs: number;
  member: CallerMember | null;
  calls_30d: number | null;
  repeat_caller: boolean | null;
  recent_events: CallerEvent[];
}

/** Messages the server sends on /ws/queue. */
export type ServerMessage =
  | ({ type: "snapshot" } & QueueSnapshot)
  | { type: "call_queued"; call: Call; position: number; requeued?: boolean }
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
