/** Settings page API (P5-5, docs/settings.md): GET/PUT /settings, reset, voices, preview. */

export type TtsModel = "aura-2" | "flux";

/** Deepgram STT models (P5-7, IN-14). The server lists which ones each side offers. */
export type SttModel = "flux-general-en" | "nova-3-general" | "nova-3-medical";

export interface SettingsValues {
  tts_model: TtsModel;
  tts_voice: string;
  greeting_text: string;
  intake_pack: string;
  identity_read_back: boolean;
  hold_update_text: string;
  hold_update_secs: number;
  stt_intake_model: SttModel;
  stt_assist_model: SttModel;
}

export interface PackSummary {
  id: string;
  name: string;
  questions: { key: string; ask: string }[];
}

export interface SettingsResponse {
  values: SettingsValues;
  defaults: SettingsValues;
  is_default: boolean;
  updated_at: string | null;
  packs: PackSummary[];
  limits: { greeting_text: number; hold_update_text: number; hold_update_secs: [number, number] };
  stt_models: { intake: SttModel[]; assist: SttModel[] };
}

export interface Voice {
  id: string;
  name: string;
  accent: string;
  gender: string;
}

export type VoiceCatalog = Record<TtsModel, Voice[]>;

/** The server refused the values: one message per field. */
export class SettingsInvalid extends Error {
  readonly errors: Record<string, string>;

  constructor(errors: Record<string, string>) {
    super("Some settings need a fix.");
    this.name = "SettingsInvalid";
    this.errors = errors;
  }
}

export interface SettingsApi {
  get(): Promise<SettingsResponse>;
  save(values: SettingsValues, agent: string): Promise<SettingsResponse>;
  reset(agent: string): Promise<SettingsResponse>;
  voices(): Promise<VoiceCatalog>;
  /** A short sample line (or `text`) in this voice, as audio. */
  preview(model: TtsModel, voice: string, text?: string): Promise<Blob>;
}

const SESSION_EXPIRED = "Your session expired. Reload the page to sign in again.";

async function call(method: string, path: string, body?: unknown): Promise<Response> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.ok) return res;
  let detail: unknown = undefined;
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail;
  } catch {
    // not JSON
  }
  if (res.status === 422 && detail && typeof detail === "object" && "errors" in detail) {
    throw new SettingsInvalid((detail as { errors: Record<string, string> }).errors);
  }
  if (res.status === 401) throw new Error(SESSION_EXPIRED);
  throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
}

const json = async <T>(r: Promise<Response>) => (await (await r).json()) as T;

export const settingsApi: SettingsApi = {
  get: () => json<SettingsResponse>(call("GET", "/settings")),
  save: (values, agent) => json<SettingsResponse>(call("PUT", "/settings", { values, agent })),
  reset: (agent) => json<SettingsResponse>(call("POST", "/settings/reset", { agent })),
  voices: () => json<VoiceCatalog>(call("GET", "/settings/voices")),
  preview: async (model, voice, text) =>
    (await call("POST", "/settings/preview", { tts_model: model, tts_voice: voice, text })).blob(),
};

/** Same rule as the server (agent_assist_server.bot_settings.disclosure_problems, IN-2). */
const AI = /\b(ai|a\.i\.|artificial intelligence|virtual assistant)\b/i;
const PERSON =
  /\b(person|human|agent|representative|someone|team member)\b[^.?!]*?\b(will|'ll|is going to|can)\b[^.?!]*?\b(join|be with you|help|connect|take)/i;

export interface Disclosure {
  ai: boolean;
  person: boolean;
}

export function disclosure(greeting: string): Disclosure {
  return { ai: AI.test(greeting), person: PERSON.test(greeting) };
}
