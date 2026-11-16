/** Pure helpers for the incoming offer and the call island (AP-3, AP-4). */

import type { Agent, Call, IdentityResult } from "../api/types";

export interface Offer {
  call: Call;
  /** Server clock, seconds since the epoch. */
  expiresAt: number;
}

export interface CallerInfo {
  name: string;
  verified: IdentityResult;
  reason: string;
  summary: string;
}

const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** What the agent needs to decide on an offer, from the intake summary. */
export function callerInfo(call: Pick<Call, "summary" | "identity_result">): CallerInfo {
  const s = call.summary ?? {};
  const summary = text(s.summary);
  return {
    name: text(s.full_name) || "Unknown caller",
    verified: call.identity_result ?? "not_verified",
    reason: text(s.reason) || summary || "No reason captured",
    summary,
  };
}

/**
 * The open offer for this agent. The `offer` event carries the call; after a reload
 * the snapshot only has the agent's offer_call_id and offer_expires_at, so the call
 * comes from the queue instead.
 */
export function offerFor(agent: Agent | undefined, queue: Call[], fromEvent: Offer | null): Offer | null {
  if (!agent || agent.state !== "ringing" || !agent.offer_call_id) return null;
  if (fromEvent && fromEvent.call.session_id === agent.offer_call_id) return fromEvent;
  const call = queue.find((c) => c.session_id === agent.offer_call_id);
  if (!call || agent.offer_expires_at === null) return null;
  return { call, expiresAt: agent.offer_expires_at };
}

/** Whole seconds left on an offer, 0..windowSecs. */
export function secondsLeft(expiresAt: number, nowMs: number, windowSecs: number): number {
  const left = Math.ceil(expiresAt - nowMs / 1000);
  return Math.max(0, Math.min(windowSecs, left));
}

export function formatWait(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r} s`;
  return r === 0 ? `${m} min` : `${m} min ${r} s`;
}

export function formatClock(secs: number): string {
  const s = Math.max(0, Math.floor(secs));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
}
