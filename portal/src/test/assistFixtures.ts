/** Assist contract v1 fixtures (docs/assist.md), shaped like the bot's real messages. */
import type { AssistCard, PlanStep } from "../state/assist";

export function heroCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "nbs:retro_auth_review:70412",
    kind: "next_step",
    title: "Next best step",
    pack_id: "claims_status",
    rank: 105,
    ai_suggested: true,
    locked: false,
    trigger: {
      phrase: "denied",
      text: "So why did my MRI claim get denied?",
      role: "caller",
      line_id: "caller-3",
      start: 28,
      end: 34,
      heard_at: 1794800787.43,
    },
    source: {
      label: "Claims system, KB-CL-002 and KB-CL-009",
      records: [{ connector: "claims", id: "70412" }],
    },
    rule_id: "retro_auth_review",
    template_id: null,
    body: {
      headline: "Start a retro authorization review for 70412",
      why: "70412 (MRI lumbar spine) was denied CO-197 because no prior authorization was on file.",
      script: "I see why this happened. I can open a retro authorization review today.",
      action: { id: "request_retro_auth_review", label: "Request retro auth review" },
    },
    ...over,
  };
}

export function recordCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "card:denied_claim",
    kind: "record",
    title: "Denied claim",
    pack_id: "claims_status",
    rank: 0,
    ai_suggested: true,
    locked: false,
    trigger: heroCard().trigger,
    source: { label: "Claims system", records: [{ connector: "claims", id: "70412" }] },
    rule_id: null,
    template_id: "denied_claim",
    body: {
      fields: ["claim_id", "service_date", "provider", "service", "billed_amount", "denial_code"],
      rows: [
        {
          claim_id: "70412",
          service_date: "2026-10-28",
          provider: "Riverside Imaging Center",
          service: "MRI lumbar spine",
          billed_amount: 1240,
          denial_code: "CO-197",
        },
      ],
      summary: "70412 MRI lumbar spine denied CO-197: no prior authorization",
    },
    ...over,
  };
}

/** A tone tip card as the bot sends it (P4-5): mood drop, not a record. */
export function toneTipCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "tone:caller-4",
    kind: "tone_tip",
    title: "Tone tip",
    pack_id: "claims_status",
    rank: 4,
    ai_suggested: true,
    locked: false,
    trigger: {
      phrase: null,
      text: "I've called three times and nobody fixes this.",
      role: "caller",
      line_id: "caller-4",
      start: null,
      end: null,
      heard_at: 1794800801.2,
    },
    source: { label: "Pipecat Tone reader, words only", records: [] },
    rule_id: null,
    template_id: null,
    body: {
      headline: "Acknowledge, then act",
      why: "The caller sounds frustrated. Name the problem in their words, then give one concrete next step.",
      mood: "Frustrated",
      emotion: "angry",
      confidence: 0.9,
    },
    ...over,
  };
}

/** An Ask bar answer as the bot sends it (P4-6): the agent's question is the trigger. */
export function askCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "ask:ask-1",
    kind: "record",
    title: "Claims",
    pack_id: "claims_status",
    rank: 0,
    ai_suggested: true,
    locked: false,
    trigger: {
      phrase: null,
      text: "Show the last 3 claims",
      role: "ask",
      line_id: "ask-ask-1",
      start: null,
      end: null,
      heard_at: 1794800801.2,
    },
    source: {
      label: "Claims system",
      records: [
        { connector: "claims", id: "70412" },
        { connector: "claims", id: "70358" },
      ],
    },
    rule_id: null,
    template_id: "claims_table",
    body: {
      fields: ["claim_id", "service_date", "provider", "service", "billed_amount", "status", "denial_code"],
      rows: [
        {
          claim_id: "70412",
          service_date: "2026-10-13",
          provider: "Riverside Imaging Center",
          service: "MRI, knee, left",
          billed_amount: 1850,
          status: "denied",
          denial_code: "CO-197",
        },
        {
          claim_id: "70358",
          service_date: "2026-09-07",
          provider: "Lakeside Family Medicine",
          service: "Office visit, established patient",
          billed_amount: 180,
          status: "paid",
          denial_code: null,
        },
      ],
      summary: null,
    },
    ...over,
  };
}

/** A locked tile as the bot sends it before the agent verifies the caller (IN-13). */
export function lockedCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "locked:3f9a1c2b7d4e",
    kind: "record",
    title: "Locked record",
    pack_id: "claims_status",
    rank: 0,
    ai_suggested: true,
    locked: true,
    trigger: heroCard().trigger,
    source: { label: "Records", records: [] },
    rule_id: null,
    template_id: null,
    body: { locked: true },
    ...over,
  };
}

/** The verify hero (IN-13): Linda Nguyen heard as "Linda Yuen", ID and DOB exact. */
export function verifyCard(over: Partial<AssistCard> = {}, result = "close_match"): AssistCard {
  return {
    id: "verify:identity",
    kind: "verify",
    title: "Verify caller",
    pack_id: "claims_status",
    rank: 1000,
    ai_suggested: false,
    locked: false,
    trigger: {
      phrase: null,
      text: "Identity check at intake",
      role: "intake",
      line_id: "intake",
      start: null,
      end: null,
      heard_at: 1794800780,
    },
    source: { label: "Intake identity check", records: [] },
    rule_id: null,
    template_id: null,
    body: {
      headline: "Confirm who you're speaking with.",
      why: "Member ID and date of birth match; the name was heard differently.",
      result,
      items: [
        { field: "member_id", label: "Member ID", ok: true, heard: null, on_file: null },
        { field: "dob", label: "Date of birth", ok: true, heard: null, on_file: null },
        { field: "full_name", label: "Name", ok: false, heard: "Linda Yuen", on_file: "Linda Nguyen" },
      ],
      script: "Thanks. So I have it right, could you spell your last name for me, please?",
      actions: [
        { id: "mark_verified", label: "Mark verified" },
        { id: "could_not_verify", label: "Could not verify" },
      ],
    },
    ...over,
  };
}

export function identityMsg(state: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return msg("identity", { state, result: "close_match", checks: ["dob", "member_id"], by: null, at: null, ...over });
}

/** One tone reading as it appears in a mood message. */
export function reading(step: number, lineId: string, emotion = "neutral") {
  const moods = ["Pleased", "Calm", "Uneasy", "Worried", "Frustrated"];
  return {
    line_id: lineId,
    ts: 1794800790,
    emotion,
    confidence: 0.9,
    sarcasm: 0.01,
    step,
    mood: moods[step],
    score: step / 4,
  };
}

export function moodMsg(steps: number[], label: string, tone = "warn"): Record<string, unknown> {
  return msg("mood", {
    label,
    tone,
    step: steps.at(-1) ?? null,
    readings: steps.map((s, i) => reading(s, `caller-${i + 1}`)),
  });
}

export const PLAN_STEPS: PlanStep[] = [
  { id: "confirm_claim", label: "Confirm which claim the caller means", done: false, by: null },
  { id: "explain_status", label: "Explain the status or the denial reason in plain words", done: false, by: null },
  { id: "take_next_step", label: "Take the next step", done: false, by: null },
  { id: "set_expectations", label: "Tell the caller what happens next and when", done: false, by: null },
];

let seq = 0;
/** One assist server message (the RTVI `data`), with an increasing seq. */
export function msg(event: string, fields: Record<string, unknown>): Record<string, unknown> {
  seq += 1;
  return { type: "assist", v: 1, event, seq, ts: 1794800790 + seq, ...fields };
}

export function planMsg(steps: PlanStep[] = PLAN_STEPS): Record<string, unknown> {
  return msg("plan_update", {
    pack_id: "claims_status",
    pack_name: "Claim status",
    steps,
    done: steps.filter((s) => s.done).length,
    total: steps.length,
  });
}
