/**
 * Assist (P4-4): the portal side of the bot's assist contract v1 (docs/assist.md).
 *
 * The bot sends one RTVI server message per update, to the agent only:
 *   {type: "assist", v: 1, event, seq, ts, ...}
 * Events: assist_status, plan_update (whole plan), hero (card or null), card_added
 * (same id replaces in place), card_removed. Anything else, or any other `v`, is ignored.
 *
 * The agent's own actions (accept, dismiss, pin, rate, manual plan ticks) change local
 * state at once (localAssist) and go to the bot as assist_feedback client messages.
 */
import type { HighlightRange } from "./transcript";

export const ASSIST_VERSION = 1;

export type CardKind = "next_step" | "record" | "article" | "alert" | "tone_tip";
const KINDS: readonly CardKind[] = ["next_step", "record", "article", "alert", "tone_tip"];
const STATUS_STATES = ["listening", "rate_limited", "error", "stopped"] as const;
export type AssistStatusState = (typeof STATUS_STATES)[number];

export interface Trigger {
  phrase: string | null;
  text: string;
  role: string;
  line_id: string;
  start: number | null;
  end: number | null;
  heard_at: number;
}

export interface CardSource {
  label: string;
  records: { connector: string; id: string }[];
}

export interface NextStepBody {
  headline: string;
  why: string;
  script: string;
  action: { id: string; label: string };
}

export type Cell = string | number | boolean | null;

export interface TableBody {
  fields: string[];
  rows: Record<string, Cell>[];
  summary: string | null;
}

/** Tone tip (P4-5, AP-14): sent when the caller's mood drops two steps. */
export interface ToneTipBody {
  headline: string;
  why: string;
  mood: string;
  emotion: string;
  confidence: number;
}

/** One Pipecat Tone reading of a caller line (words only, not audio). */
export interface ToneReading {
  line_id: string;
  ts: number;
  emotion: string;
  confidence: number;
  sarcasm: number;
  /** 0 Pleased, 1 Calm, 2 Uneasy, 3 Worried, 4 Frustrated. */
  step: number;
  mood: string;
  /** step / 4: 0 calm to 1 upset. */
  score: number;
}

export type MoodTone = "ok" | "warn" | "bad";

/** The caller's mood trend for the whole call (AP-14). */
export interface MoodState {
  label: string | null;
  tone: MoodTone | null;
  step: number | null;
  readings: ToneReading[];
}

export interface AssistCard {
  id: string;
  kind: CardKind;
  title: string;
  pack_id: string;
  rank: number;
  ai_suggested: boolean;
  trigger: Trigger;
  source: CardSource;
  rule_id: string | null;
  template_id: string | null;
  body: NextStepBody | TableBody | ToneTipBody | Record<string, unknown>;
}

export type StepBy = "transcript" | "action" | "manual" | null;

export interface PlanStep {
  id: string;
  label: string;
  done: boolean;
  by: StepBy;
}

interface Base {
  v: 1;
  seq: number;
  ts: number;
}

export type AssistMessage =
  | (Base & { event: "assist_status"; state: AssistStatusState; pending?: number; message?: string })
  | (Base & { event: "plan_update"; pack_id: string; pack_name: string; steps: PlanStep[] })
  | (Base & { event: "hero"; card: AssistCard | null; reason: string; replaces: string | null })
  | (Base & { event: "card_added"; card: AssistCard })
  | (Base & { event: "card_removed"; card_id: string; reason: string })
  | (Base & { event: "mood" } & MoodState);

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
const isStr = (x: unknown): x is string => typeof x === "string";
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const strOrNull = (x: unknown) => x === null || x === undefined || isStr(x);
const numOrNull = (x: unknown) => x === null || x === undefined || isNum(x);

export function isNextStepBody(b: unknown): b is NextStepBody {
  return (
    isObj(b) &&
    isStr(b.headline) &&
    isStr(b.why) &&
    isStr(b.script) &&
    isObj(b.action) &&
    isStr(b.action.id) &&
    isStr(b.action.label)
  );
}

export function isTableBody(b: unknown): b is TableBody {
  return (
    isObj(b) &&
    Array.isArray(b.fields) &&
    b.fields.every(isStr) &&
    Array.isArray(b.rows) &&
    b.rows.every(isObj) &&
    strOrNull(b.summary)
  );
}

export function isToneTipBody(b: unknown): b is ToneTipBody {
  return isObj(b) && isStr(b.headline) && isStr(b.why) && isStr(b.mood);
}

function bodyOk(kind: CardKind, body: unknown): boolean {
  if (kind === "next_step") return isNextStepBody(body);
  if (kind === "tone_tip") return isToneTipBody(body);
  return isTableBody(body);
}

function parseReading(x: unknown): ToneReading | null {
  if (!isObj(x) || !isStr(x.line_id) || !isNum(x.step) || !isStr(x.mood) || !isNum(x.score)) return null;
  return {
    line_id: x.line_id,
    ts: isNum(x.ts) ? x.ts : 0,
    emotion: isStr(x.emotion) ? x.emotion : "",
    confidence: isNum(x.confidence) ? x.confidence : 0,
    sarcasm: isNum(x.sarcasm) ? x.sarcasm : 0,
    step: x.step,
    mood: x.mood,
    score: Math.max(0, Math.min(1, x.score)),
  };
}

const MOOD_TONES: readonly MoodTone[] = ["ok", "warn", "bad"];

function parseCard(x: unknown): AssistCard | null {
  if (!isObj(x) || !isStr(x.id) || !x.id || !isStr(x.title)) return null;
  if (!KINDS.includes(x.kind as CardKind)) return null;
  const t = x.trigger;
  if (!isObj(t) || !isStr(t.text) || !isStr(t.line_id) || !strOrNull(t.phrase)) return null;
  if (!numOrNull(t.start) || !numOrNull(t.end)) return null;
  const src = x.source;
  if (!isObj(src) || !isStr(src.label) || !Array.isArray(src.records)) return null;
  const records = src.records.filter(
    (r): r is { connector: string; id: string } => isObj(r) && isStr(r.connector) && isStr(r.id),
  );
  const kind = x.kind as CardKind;
  if (!bodyOk(kind, x.body)) return null;
  return {
    id: x.id,
    kind,
    title: x.title,
    pack_id: isStr(x.pack_id) ? x.pack_id : "",
    rank: isNum(x.rank) ? x.rank : 0,
    ai_suggested: x.ai_suggested !== false,
    trigger: {
      phrase: (t.phrase as string | null | undefined) ?? null,
      text: t.text,
      role: isStr(t.role) ? t.role : "caller",
      line_id: t.line_id,
      start: (t.start as number | null | undefined) ?? null,
      end: (t.end as number | null | undefined) ?? null,
      heard_at: isNum(t.heard_at) ? t.heard_at : 0,
    },
    source: { label: src.label, records },
    rule_id: isStr(x.rule_id) ? x.rule_id : null,
    template_id: isStr(x.template_id) ? x.template_id : null,
    body: x.body as AssistCard["body"],
  };
}

function parseStep(x: unknown): PlanStep | null {
  if (!isObj(x) || !isStr(x.id) || !isStr(x.label) || typeof x.done !== "boolean") return null;
  const by = x.by === "transcript" || x.by === "action" ? x.by : null;
  return { id: x.id, label: x.label, done: x.done, by };
}

/** One assist message from an RTVI server message's data, or null (wrong type, version, or shape). */
export function parseAssistMessage(data: unknown): AssistMessage | null {
  if (!isObj(data) || data.type !== "assist" || data.v !== ASSIST_VERSION) return null;
  if (!isNum(data.seq)) return null;
  const base: Base = { v: 1, seq: data.seq, ts: isNum(data.ts) ? data.ts : 0 };
  switch (data.event) {
    case "assist_status": {
      if (!STATUS_STATES.includes(data.state as AssistStatusState)) return null;
      return {
        ...base,
        event: "assist_status",
        state: data.state as AssistStatusState,
        ...(isNum(data.pending) ? { pending: data.pending } : {}),
        ...(isStr(data.message) ? { message: data.message } : {}),
      };
    }
    case "plan_update": {
      if (!Array.isArray(data.steps)) return null;
      const steps = data.steps.map(parseStep);
      if (steps.some((s) => s === null)) return null;
      return {
        ...base,
        event: "plan_update",
        pack_id: isStr(data.pack_id) ? data.pack_id : "",
        pack_name: isStr(data.pack_name) ? data.pack_name : "",
        steps: steps as PlanStep[],
      };
    }
    case "hero": {
      const card = data.card === null ? null : parseCard(data.card);
      if (data.card !== null && !card) return null;
      return {
        ...base,
        event: "hero",
        card,
        reason: isStr(data.reason) ? data.reason : "new",
        replaces: isStr(data.replaces) ? data.replaces : null,
      };
    }
    case "card_added": {
      const card = parseCard(data.card);
      return card ? { ...base, event: "card_added", card } : null;
    }
    case "card_removed":
      if (!isStr(data.card_id)) return null;
      return { ...base, event: "card_removed", card_id: data.card_id, reason: isStr(data.reason) ? data.reason : "" };
    case "mood": {
      if (!Array.isArray(data.readings)) return null;
      const readings = data.readings.map(parseReading).filter((r): r is ToneReading => r !== null);
      return {
        ...base,
        event: "mood",
        label: isStr(data.label) ? data.label : null,
        tone: MOOD_TONES.includes(data.tone as MoodTone) ? (data.tone as MoodTone) : null,
        step: isNum(data.step) ? data.step : null,
        readings,
      };
    }
    default:
      return null;
  }
}

/** One row of "Earlier this call": an accepted action or a plan step done. */
export interface HistoryItem {
  key: string;
  label: string;
  detail: string | null;
  /** Call time (seconds since accept) when it happened. */
  atSecs: number;
}

export interface HeroAck {
  card: AssistCard;
  outcome: "accepted" | "dismissed";
}

export interface AssistState {
  /** Highest seq applied; older or repeated messages are dropped. */
  seq: number;
  status: { state: AssistStatusState; pending?: number } | null;
  plan: { packId: string; packName: string; steps: PlanStep[] } | null;
  hero: AssistCard | null;
  /** Supporting cards in arrival order. */
  cards: AssistCard[];
  /** The hero the agent just accepted or dismissed, shown briefly in that state. */
  ack: HeroAck | null;
  history: HistoryItem[];
  pinned: string[];
  ratings: Record<string, "up" | "down">;
  /** The agent's own plan ticks (AP-12); they win over the bot's. */
  manual: Record<string, boolean>;
  /** Cards the agent accepted or dismissed: never shown again in this call. */
  gone: string[];
  /** The caller's mood trend (P4-5), null until the first tone reading. */
  mood: MoodState | null;
}

export function emptyAssist(): AssistState {
  return {
    seq: 0,
    status: null,
    plan: null,
    hero: null,
    cards: [],
    ack: null,
    history: [],
    pinned: [],
    ratings: {},
    manual: {},
    gone: [],
    mood: null,
  };
}

function effectiveSteps(steps: PlanStep[], manual: Record<string, boolean>): PlanStep[] {
  return steps.map((s) =>
    s.id in manual ? { ...s, done: manual[s.id], by: manual[s.id] ? (s.done ? s.by : "manual") : null } : s,
  );
}

/** Add "step done" rows for steps newly done; drop rows for steps no longer done. */
function syncStepHistory(history: HistoryItem[], steps: PlanStep[], atSecs: number): HistoryItem[] {
  const done = new Set(steps.filter((s) => s.done).map((s) => `step:${s.id}`));
  let out = history.filter((h) => !h.key.startsWith("step:") || done.has(h.key));
  for (const s of steps) {
    const key = `step:${s.id}`;
    if (s.done && !out.some((h) => h.key === key)) {
      out = [...out, { key, label: s.label, detail: null, atSecs }];
    }
  }
  return out.length === history.length && out.every((h, i) => h === history[i]) ? history : out;
}

/** Apply one bot message. `atSecs` is the call time now, for history rows. */
export function applyAssist(s: AssistState, m: AssistMessage, atSecs: number): AssistState {
  if (m.seq <= s.seq) return s;
  const next: AssistState = { ...s, seq: m.seq };
  switch (m.event) {
    case "assist_status":
      next.status = m.pending === undefined ? { state: m.state } : { state: m.state, pending: m.pending };
      return next;
    case "plan_update": {
      next.plan = { packId: m.pack_id, packName: m.pack_name, steps: m.steps };
      next.history = syncStepHistory(s.history, effectiveSteps(m.steps, s.manual), atSecs);
      return next;
    }
    case "hero":
      next.hero = m.card && !s.gone.includes(m.card.id) ? m.card : null;
      next.cards = m.card ? s.cards.filter((c) => c.id !== m.card?.id) : s.cards;
      return next;
    case "card_added": {
      if (s.gone.includes(m.card.id)) return next;
      const i = s.cards.findIndex((c) => c.id === m.card.id);
      next.cards = i === -1 ? [...s.cards, m.card] : s.cards.map((c, j) => (j === i ? m.card : c));
      return next;
    }
    case "card_removed":
      next.cards = s.cards.filter((c) => c.id !== m.card_id);
      return next;
    case "mood":
      next.mood = { label: m.label, tone: m.tone, step: m.step, readings: m.readings };
      return next;
  }
}

export type LocalAction =
  | { type: "accept"; cardId: string }
  | { type: "dismiss"; cardId: string }
  | { type: "settle" }
  | { type: "pin"; cardId: string }
  | { type: "unpin"; cardId: string }
  | { type: "rate"; cardId: string; rating: "up" | "down" }
  | { type: "tick"; stepId: string; done: boolean };

/** Apply one of the agent's own actions. `atSecs` is the call time now. */
export function localAssist(s: AssistState, a: LocalAction, atSecs: number): AssistState {
  switch (a.type) {
    case "accept": {
      const card = s.hero;
      if (!card || card.id !== a.cardId || !isNextStepBody(card.body)) return s;
      const ids = card.source.records.map((r) => r.id).join(", ");
      const row: HistoryItem = {
        key: `action:${card.id}`,
        label: card.body.action.label,
        detail: ids ? `Simulated · ${ids}` : "Simulated",
        atSecs,
      };
      return {
        ...s,
        hero: null,
        ack: { card, outcome: "accepted" },
        history: [...s.history, row],
        gone: [...s.gone, card.id],
      };
    }
    case "dismiss":
      if (s.hero?.id === a.cardId) {
        return { ...s, hero: null, ack: { card: s.hero, outcome: "dismissed" }, gone: [...s.gone, a.cardId] };
      }
      return {
        ...s,
        cards: s.cards.filter((c) => c.id !== a.cardId),
        pinned: s.pinned.filter((id) => id !== a.cardId),
        gone: [...s.gone, a.cardId],
      };
    case "settle":
      return s.ack ? { ...s, ack: null } : s;
    case "pin":
      return s.pinned.includes(a.cardId) ? s : { ...s, pinned: [...s.pinned, a.cardId] };
    case "unpin":
      return { ...s, pinned: s.pinned.filter((id) => id !== a.cardId) };
    case "rate":
      return { ...s, ratings: { ...s.ratings, [a.cardId]: a.rating } };
    case "tick": {
      const manual = { ...s.manual, [a.stepId]: a.done };
      const history = s.plan ? syncStepHistory(s.history, effectiveSteps(s.plan.steps, manual), atSecs) : s.history;
      return { ...s, manual, history };
    }
  }
}

export interface PlanView {
  packName: string;
  steps: PlanStep[];
  done: number;
  total: number;
  /** The first step not done yet (the "next" ring), or null when all are done. */
  next: string | null;
}

export function planView(s: AssistState): PlanView | null {
  if (!s.plan) return null;
  const steps = effectiveSteps(s.plan.steps, s.manual);
  return {
    packName: s.plan.packName,
    steps,
    done: steps.filter((x) => x.done).length,
    total: steps.length,
    next: steps.find((x) => !x.done)?.id ?? null,
  };
}

/** Trigger words of the cards on screen, for the transcript highlighter. */
export function assistHighlights(s: AssistState): HighlightRange[] {
  const out: HighlightRange[] = [];
  for (const c of [s.hero, ...s.cards]) {
    const t = c?.trigger;
    if (!t || t.phrase === null || t.start === null || t.end === null) continue;
    if (out.some((h) => h.lineId === t.line_id && h.start === t.start && h.end === t.end)) continue;
    out.push({ lineId: t.line_id, start: t.start, end: t.end });
  }
  return out;
}
