/** Assist contract v1 fixtures (docs/assist.md), shaped like the bot's real messages. */
import type { AssistCard, PlanStep } from "../state/assist";

export function heroCard(over: Partial<AssistCard> = {}): AssistCard {
  return {
    id: "nbs:retro_auth_review:CLM-900001",
    kind: "next_step",
    title: "Next best step",
    pack_id: "claims_status",
    rank: 105,
    ai_suggested: true,
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
      records: [{ connector: "claims", id: "CLM-900001" }],
    },
    rule_id: "retro_auth_review",
    template_id: null,
    body: {
      headline: "Start a retro authorization review for CLM-900001",
      why: "CLM-900001 (MRI lumbar spine) was denied CO-197 because no prior authorization was on file.",
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
    trigger: heroCard().trigger,
    source: { label: "Claims system", records: [{ connector: "claims", id: "CLM-900001" }] },
    rule_id: null,
    template_id: "denied_claim",
    body: {
      fields: ["claim_id", "service_date", "provider", "service", "billed_amount", "denial_code"],
      rows: [
        {
          claim_id: "CLM-900001",
          service_date: "2026-10-28",
          provider: "Riverside Imaging Center",
          service: "MRI lumbar spine",
          billed_amount: 1240,
          denial_code: "CO-197",
        },
      ],
      summary: "CLM-900001 MRI lumbar spine denied CO-197: no prior authorization",
    },
    ...over,
  };
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
