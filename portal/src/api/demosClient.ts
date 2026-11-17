/** Demos page API (P5-10, docs/demos.md): GET /demo/personas behind the login. */

export interface ScriptStep {
  /** Who the presenter says the line to: the intake bot, or the agent after handoff. */
  to: "bot" | "agent";
  say: string;
  note?: string;
}

/** What should show on the agent's screen after a beat (P5-11). */
export interface ExpectItem {
  kind: string;
  text: string;
  /** The card, rule, plan step, or article it points at. */
  id?: string;
  /** The Ask bar question to type. */
  ask?: string;
  reason_code?: string;
  refs?: string[];
}

/** One beat of the caller talk track (P5-11, docs/demos.md). */
export interface TalkBeat {
  phase: "intake" | "agent" | "closing";
  to: "bot" | "agent";
  /** Intake only: which bot question it answers. */
  step?: string;
  say: string;
  if_asked: string[];
  /** For the caller (also on the caller page). */
  note?: string;
  /** For the tester: what the agent should do. Never on the caller page. */
  agent_note?: string;
  optional: boolean;
  /** Optional feature beats: tone_tip, ask_bar, verify. */
  feature?: string;
  expect: ExpectItem[];
}

export interface DemoPersona {
  key: string;
  name: string;
  dob: string;
  dob_said: string;
  member_id: string;
  /** "418 302": how the ID is read aloud. */
  member_id_groups: string;
  plan_id: string;
  plan: string;
  claim_id?: string;
  /** False for the caller who is deliberately not a member (not found). */
  in_seed: boolean;
  hero: boolean;
  pack: string;
  case: string;
  start_line: string;
  audience_sees: { headline: string; cards: string[] };
  features: string[];
  kb_articles: string[];
  script: ScriptStep[];
  talk_track: TalkBeat[];
  warning?: string;
}

export interface DemosResponse {
  phone_line: { number: string; password_hint: string };
  features: { id: string; label: string }[];
  /** Labels for expect item kinds (hero, card, plan, ...). */
  expect_kinds?: { id: string; label: string }[];
  personas: DemoPersona[];
}

export interface DemosApi {
  list(): Promise<DemosResponse>;
}

export const demosApi: DemosApi = {
  list: async () => {
    const res = await fetch("/demo/personas", { credentials: "same-origin" });
    if (res.status === 401) throw new Error("Your session expired. Reload the page to sign in again.");
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return (await res.json()) as DemosResponse;
  },
};

/** What Copy details puts on the clipboard. */
export const copyText = (p: DemoPersona) => `${p.name}\nDOB ${p.dob}\nMember ID ${p.member_id_groups}`;

/** The browser caller with this persona's script (opens in a new tab). */
export const callerHref = (p: DemoPersona) => `/caller?persona=${encodeURIComponent(p.key)}`;
