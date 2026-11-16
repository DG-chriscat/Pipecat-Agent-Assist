import { Check, Play, RotateCcw, Save, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  SettingsInvalid,
  disclosure,
  type SettingsApi,
  type SettingsResponse,
  type SettingsValues,
  type SttModel,
  type TtsModel,
  type VoiceCatalog,
} from "../api/settingsClient";

interface Props {
  api: SettingsApi;
  agentName: string;
  /** Plays preview audio. Default: an <audio> element. */
  play?: (audio: Blob) => Promise<void>;
}

const MODELS: { id: TtsModel; label: string }[] = [
  { id: "aura-2", label: "Aura-2" },
  { id: "flux", label: "Flux" },
];

/** STT model labels (P5-7, IN-14); the order and the choices per side come from the server. */
const STT_LABELS: Record<SttModel, string> = {
  "flux-general-en": "Flux",
  "nova-3-general": "Nova-3",
  "nova-3-medical": "Nova-3 Medical",
};

const STT_SIDES: {
  key: "stt_intake_model" | "stt_assist_model";
  side: "intake" | "assist";
  label: string;
  help: string;
}[] = [
  {
    key: "stt_intake_model",
    side: "intake",
    label: "Intake bot",
    help: "Nova-3 ends the turn on a pause. Flux decides when the caller is done, but missed some names.",
  },
  {
    key: "stt_assist_model",
    side: "assist",
    label: "Live assist",
    help: "Transcribes the caller and the agent after a person joins. Nova-3 Medical knows more clinical terms.",
  },
];

/** LLM per job (P5-9, DC-11): the choices, labels, and notes come from the server. */
const LLM_JOBS: { key: "llm_intake" | "llm_assist" | "llm_wrapup"; label: string; help: string }[] = [
  { key: "llm_intake", label: "Intake", help: "Asks the intake questions and calls the intake functions." },
  { key: "llm_assist", label: "Assist and Ask", help: "Picks cards during the call and answers the Ask bar." },
  { key: "llm_wrapup", label: "Wrap up", help: "Drafts the summary and reason code when the call ends." },
];
const DEEPGRAM_LLM_DOCS = "https://developers.deepgram.com/docs/llm";
const llmLabel = (name: string) => name.replace(/^Deepgram /, "");

/** Flux expressivity steps (P5-8, AP-22): -2 calm to 2 animated, 0 the default. */
const EXPRESSIVITY: { value: number; label: string }[] = [
  { value: -2, label: "Calm" },
  { value: -1, label: "Steady" },
  { value: 0, label: "Natural" },
  { value: 1, label: "Warm" },
  { value: 2, label: "Animated" },
];
/** Speed slider range when the server sends none; the server's comes from the read back
 * test (docs/spikes/tts-flux-controls.md). */
const SPEED_RANGE: [number, number] = [0.8, 1.2];
const SPEED_STEP = 0.05;
const speedLabel = (s: number) => `${s.toFixed(s * 10 === Math.round(s * 10) ? 1 : 2)}x`;
/** Set from the read back test (docs/spikes/tts-flux-controls.md). */
const EXPRESSIVITY_HELP = "How much the voice rises and falls. Natural is Flux's default.";
const FLUX_DEFAULTS = { tts_speed: 1.0, tts_expressivity: 0, slow_readback: true, readback_speed: 0.9 };

/** A response from a server without the Flux controls gets their defaults. */
function withFluxDefaults(s: SettingsResponse): SettingsResponse {
  return { ...s, values: { ...FLUX_DEFAULTS, ...s.values } };
}

async function playBlob(audio: Blob): Promise<void> {
  const url = URL.createObjectURL(audio);
  const el = new Audio(url);
  el.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
  await el.play();
}

const same = (a: SettingsValues, b: SettingsValues) => JSON.stringify(a) === JSON.stringify(b);
const errorText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

/**
 * Settings (P5-5, PRD AP-20, DESIGN.md "Settings"): what the bot says and how it sounds.
 * Voice (model, voice, preview, and Listening: the STT model for intake and for live
 * assist, P5-7, IN-14), greeting (with the IN-2 disclosure check), intake (pack,
 * its questions read only, identity read back), and hold (message, interval). Saved
 * settings apply to the next call; a call in progress keeps what it started with.
 */
export function Settings({ api, agentName, play = playBlob }: Props) {
  const [loaded, setLoaded] = useState<SettingsResponse | null>(null);
  const [voices, setVoices] = useState<VoiceCatalog | null>(null);
  const [draft, setDraft] = useState<SettingsValues | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [previewing, setPreviewing] = useState<"voice" | "greeting" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let live = true;
    Promise.all([api.get(), api.voices()])
      .then(([raw, v]) => {
        if (!live) return;
        const s = withFluxDefaults(raw);
        setLoaded(s);
        setDraft(s.values);
        setVoices(v);
      })
      .catch((e) => live && setLoadError(errorText(e, "Settings could not load.")));
    return () => {
      live = false;
    };
  }, [api]);

  const update = useCallback((patch: Partial<SettingsValues>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setNotice(null);
    setError(null);
    setFieldErrors((errs) => {
      const next = { ...errs };
      for (const k of Object.keys(patch)) delete next[k];
      return next;
    });
  }, []);

  const applied = useCallback((raw: SettingsResponse, message: string) => {
    const s = withFluxDefaults(raw);
    setLoaded(s);
    setDraft(s.values);
    setFieldErrors({});
    setError(null);
    setNotice(message);
  }, []);

  const checks = useMemo(() => disclosure(draft?.greeting_text ?? ""), [draft?.greeting_text]);
  const limits = loaded?.limits;
  const [minSecs, maxSecs] = limits?.hold_update_secs ?? [5, 600];
  const secsOk =
    !!draft && Number.isInteger(draft.hold_update_secs) && draft.hold_update_secs >= minSecs && draft.hold_update_secs <= maxSecs;
  const textOk = (t: string | undefined, max: number | undefined) => !!t?.trim() && (max === undefined || t.length <= max);
  const valid =
    !!draft &&
    checks.ai &&
    checks.person &&
    secsOk &&
    textOk(draft.greeting_text, limits?.greeting_text) &&
    textOk(draft.hold_update_text, limits?.hold_update_text);
  const dirty = !!draft && !!loaded && !same(draft, loaded.values);

  async function save() {
    if (!draft || !valid || busy) return;
    setBusy("save");
    try {
      applied(await api.save(draft, agentName), "Saved. Applies to the next call.");
    } catch (e) {
      if (e instanceof SettingsInvalid) {
        setFieldErrors(e.errors);
        setError(e.message);
      } else setError(errorText(e, "Settings could not be saved."));
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    if (busy) return;
    setBusy("reset");
    try {
      applied(await api.reset(agentName), "Back to the defaults. Applies to the next call.");
    } catch (e) {
      setError(errorText(e, "Settings could not be reset."));
    } finally {
      setBusy(null);
    }
  }

  async function preview(which: "voice" | "greeting") {
    if (!draft || previewing) return;
    setPreviewing(which);
    setError(null);
    try {
      const text = which === "greeting" ? draft.greeting_text : undefined;
      const controls = {
        tts_speed: draft.tts_speed,
        tts_expressivity: draft.tts_expressivity,
        slow_readback: draft.slow_readback,
        readback_speed: draft.readback_speed,
      };
      await play(await api.preview(draft.tts_model, draft.tts_voice, text, controls));
    } catch (e) {
      setError(errorText(e, "The preview could not be made."));
    } finally {
      setPreviewing(null);
    }
  }

  if (!draft || !loaded || !voices) {
    return (
      <section className="panel card settings" role="region" aria-label="Settings">
        <h1>Settings</h1>
        {loadError ? (
          <p className="error" role="alert">
            {loadError}
          </p>
        ) : (
          <p className="muted-note">Loading settings</p>
        )}
      </section>
    );
  }

  const modelVoices = voices[draft.tts_model] ?? [];
  const pack = loaded.packs.find((p) => p.id === draft.intake_pack);
  const isFlux = draft.tts_model === "flux";
  const [minSpeed, maxSpeed] = limits?.tts_speed ?? SPEED_RANGE;
  const speedStep = limits?.speed_step ?? SPEED_STEP;
  const fieldError = (k: keyof SettingsValues) =>
    fieldErrors[k] ? (
      <p className="field-error" id={`err-${k}`}>
        {fieldErrors[k]}
      </p>
    ) : null;

  return (
    <section className="panel card settings" role="region" aria-label="Settings">
      <div className="settings-head">
        <div>
          <h1>Settings</h1>
          <p className="sub">What the bot says and how it sounds. Changes apply to the next call; a call in progress keeps its settings.</p>
        </div>
        <span className="chip chip-neutral">{loaded.is_default ? "Defaults" : "Saved"}</span>
      </div>

      <div className="settings-group">
        <div className="settings-group-head">
          <h2>Voice</h2>
          <span className="label">Deepgram text to speech</span>
        </div>
        <div className="settings-row">
          <div className="label" id="model-label">
            Model
          </div>
          <div className="segmented" role="radiogroup" aria-labelledby="model-label">
            {MODELS.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={draft.tts_model === m.id}
                className={`segment${draft.tts_model === m.id ? " is-on" : ""}`}
                onClick={() =>
                  draft.tts_model !== m.id && update({ tts_model: m.id, tts_voice: voices[m.id]?.[0]?.id ?? "" })
                }
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
        <div className="settings-row">
          <label className="label" htmlFor="settings-voice">
            Voice
          </label>
          <div className="settings-inline">
            <select
              id="settings-voice"
              className="field select"
              value={draft.tts_voice}
              aria-describedby={fieldErrors.tts_voice ? "err-tts_voice" : undefined}
              onChange={(e) => update({ tts_voice: e.target.value })}
            >
              {modelVoices.map((v) => (
                <option key={v.id} value={v.id}>
                  {[v.name, v.accent, v.gender].filter(Boolean).join(" · ")}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn"
              aria-busy={previewing === "voice"}
              disabled={previewing !== null}
              onClick={() => void preview("voice")}
            >
              <Play strokeWidth={1.7} aria-hidden="true" />
              {previewing === "voice" ? "Playing" : "Preview"}
            </button>
          </div>
          {fieldError("tts_voice")}
        </div>
        <div className="settings-subgroup flux-controls" role="group" aria-labelledby="delivery-label">
          <div className="settings-subgroup-head">
            <h3 id="delivery-label">Delivery</h3>
            <span className="label">Flux voice controls</span>
          </div>
          {!isFlux && <p className="muted-note flux-note">Speed and expressiveness are only for Flux voices.</p>}
          <div className="settings-row">
            <div className="settings-inline flux-speed-head">
              <label className="label" htmlFor="settings-speed">
                Speed
              </label>
              <span className="mono flux-speed-value">{speedLabel(draft.tts_speed)}</span>
            </div>
            <div className="flux-speed">
              <input
                id="settings-speed"
                type="range"
                className="range"
                min={minSpeed}
                max={maxSpeed}
                step={speedStep}
                value={draft.tts_speed}
                disabled={!isFlux}
                list="settings-speed-ticks"
                onChange={(e) => update({ tts_speed: Math.round(e.target.valueAsNumber * 100) / 100 })}
              />
              <datalist id="settings-speed-ticks">
                <option value={1} />
              </datalist>
              <div className="flux-speed-scale label" aria-hidden="true">
                <span>{speedLabel(minSpeed)}</span>
                <span className="flux-speed-normal" style={{ left: `${((1 - minSpeed) / (maxSpeed - minSpeed)) * 100}%` }}>
                  1.0 Normal
                </span>
                <span>{speedLabel(maxSpeed)}</span>
              </div>
            </div>
            {fieldError("tts_speed")}
          </div>
          <div className="settings-row">
            <div className="settings-inline">
              <div className="label" id="expressivity-label">
                Expressiveness
              </div>
              <span className="chip chip-info">Beta</span>
            </div>
            <div className="segmented" role="radiogroup" aria-labelledby="expressivity-label">
              {EXPRESSIVITY.map((x) => (
                <button
                  key={x.value}
                  type="button"
                  role="radio"
                  aria-checked={draft.tts_expressivity === x.value}
                  disabled={!isFlux}
                  className={`segment${draft.tts_expressivity === x.value ? " is-on" : ""}`}
                  onClick={() => update({ tts_expressivity: x.value })}
                >
                  {x.label}
                </button>
              ))}
            </div>
            <p className="muted-note">{EXPRESSIVITY_HELP}</p>
            {fieldError("tts_expressivity")}
          </div>
          <div className="settings-row settings-switch-row">
            <div>
              <div className="settings-switch-title" id="slow-readback-label">
                Slow down for read backs
              </div>
              <p className="muted-note">
                Reads member IDs, dates of birth, and claim numbers back at {speedLabel(draft.readback_speed)}, then returns to
                your speed.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-labelledby="slow-readback-label"
              aria-checked={draft.slow_readback}
              disabled={!isFlux}
              className="switch"
              onClick={() => update({ slow_readback: !draft.slow_readback })}
            >
              <span className="switch-knob" />
            </button>
          </div>
        </div>
        <div className="settings-subgroup" role="group" aria-labelledby="listening-label">
          <div className="settings-subgroup-head">
            <h3 id="listening-label">Listening</h3>
            <span className="label">Deepgram speech to text</span>
          </div>
          {STT_SIDES.map(({ key, side, label, help }) => (
            <div className="settings-row" key={key}>
              <div className="label" id={`${key}-label`}>
                {label}
              </div>
              <div className="segmented" role="radiogroup" aria-labelledby={`${key}-label`}>
                {(loaded.stt_models?.[side] ?? []).map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={draft[key] === m}
                    className={`segment${draft[key] === m ? " is-on" : ""}`}
                    onClick={() => update({ [key]: m })}
                  >
                    {STT_LABELS[m] ?? m}
                  </button>
                ))}
              </div>
              <p className="muted-note">{help}</p>
              {fieldError(key)}
            </div>
          ))}
        </div>
        {loaded.llm_models && loaded.llm_models.length > 0 && (
          <div className="settings-subgroup" role="group" aria-labelledby="thinking-label">
            <div className="settings-subgroup-head">
              <h3 id="thinking-label">Thinking</h3>
              <span className="label">The LLM for each job</span>
            </div>
            {LLM_JOBS.map(({ key, label, help }) => {
              const notes = loaded.llm_models!.filter((m) => m.note);
              return (
                <div className="settings-row" key={key}>
                  <div className="label" id={`${key}-label`}>
                    {label}
                  </div>
                  <div className="segmented settings-llm" role="radiogroup" aria-label={`${label} LLM`}>
                    {loaded.llm_models!.map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        role="radio"
                        data-model={m.id}
                        aria-checked={draft[key] === m.id}
                        className={`segment${draft[key] === m.id ? " is-on" : ""}`}
                        onClick={() => update({ [key]: m.id })}
                      >
                        {llmLabel(m.name)}
                        {m.early_access && <span className="chip chip-info">Early Access</span>}
                      </button>
                    ))}
                  </div>
                  <p className="muted-note">{help}</p>
                  {notes.map((m) => (
                    <p key={m.id} className={`muted-note settings-llm-note${draft[key] === m.id ? " is-warn" : ""}`}>
                      {llmLabel(m.name)}: {m.note}
                    </p>
                  ))}
                  {fieldError(key)}
                </div>
              );
            })}
            <p className="muted-note">
              Deepgram LLMs are Early Access: not for production traffic, and model ids may change. phoneLLM mini runs
              with model training opt out.{" "}
              <a href={DEEPGRAM_LLM_DOCS} target="_blank" rel="noopener noreferrer">
                Deepgram LLM docs
              </a>
            </p>
          </div>
        )}
      </div>

      <div className="settings-group">
        <div className="settings-group-head">
          <h2>Greeting</h2>
          <span className="label">The first thing the caller hears</span>
        </div>
        <div className="settings-row">
          <label className="label" htmlFor="settings-greeting">
            Greeting
          </label>
          <textarea
            id="settings-greeting"
            className="field settings-text"
            rows={3}
            maxLength={limits?.greeting_text}
            value={draft.greeting_text}
            onChange={(e) => update({ greeting_text: e.target.value })}
          />
          <div className="settings-inline settings-checks-row">
            <ul className="settings-checks" aria-label="Required disclosure">
              <li className={checks.ai ? "is-ok" : "is-missing"}>
                {checks.ai ? <Check strokeWidth={1.7} aria-hidden="true" /> : <X strokeWidth={1.7} aria-hidden="true" />}
                Says the caller is talking to an AI assistant
              </li>
              <li className={checks.person ? "is-ok" : "is-missing"}>
                {checks.person ? <Check strokeWidth={1.7} aria-hidden="true" /> : <X strokeWidth={1.7} aria-hidden="true" />}
                Says a person will join
              </li>
            </ul>
            <button
              type="button"
              className="btn btn-quiet"
              aria-busy={previewing === "greeting"}
              disabled={previewing !== null}
              onClick={() => void preview("greeting")}
            >
              <Volume2 strokeWidth={1.7} aria-hidden="true" />
              {previewing === "greeting" ? "Playing" : "Hear the greeting"}
            </button>
          </div>
          {fieldError("greeting_text")}
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-head">
          <h2>Intake</h2>
          <span className="label">Use case pack</span>
        </div>
        <div className="settings-row">
          <div className="label" id="pack-label">
            Pack
          </div>
          <div className="reason-codes" role="radiogroup" aria-labelledby="pack-label">
            {loaded.packs.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={draft.intake_pack === p.id}
                className={`reason${draft.intake_pack === p.id ? " is-on" : ""}`}
                onClick={() => update({ intake_pack: p.id })}
              >
                {p.name}
              </button>
            ))}
          </div>
          {fieldError("intake_pack")}
        </div>
        {pack && (
          <div className="settings-row">
            <div className="label">After identity and the reason for the call</div>
            <ol className="settings-questions" aria-label="Intake questions">
              {pack.questions.map((q) => (
                <li key={q.key}>{q.ask}</li>
              ))}
            </ol>
          </div>
        )}
        <div className="settings-row settings-switch-row">
          <div>
            <div className="settings-switch-title" id="readback-label">
              Identity read back
            </div>
            <p className="muted-note">Read the name and member ID back before checking them.</p>
          </div>
          <button
            type="button"
            role="switch"
            aria-labelledby="readback-label"
            aria-checked={draft.identity_read_back}
            className="switch"
            onClick={() => update({ identity_read_back: !draft.identity_read_back })}
          >
            <span className="switch-knob" />
          </button>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-head">
          <h2>Hold</h2>
          <span className="label">While the caller waits for a person</span>
        </div>
        <div className="settings-row">
          <label className="label" htmlFor="settings-hold">
            Hold message
          </label>
          <textarea
            id="settings-hold"
            className="field settings-text"
            rows={2}
            maxLength={limits?.hold_update_text}
            value={draft.hold_update_text}
            onChange={(e) => update({ hold_update_text: e.target.value })}
          />
          <p className="muted-note">
            <span className="mono">{"{place}"}</span> is replaced with the caller's place in line, like "You're next in line."
          </p>
          {fieldError("hold_update_text")}
        </div>
        <div className="settings-row settings-interval">
          <label className="label" htmlFor="settings-hold-secs">
            Every
          </label>
          <div className="settings-inline">
            <input
              id="settings-hold-secs"
              className="field settings-secs mono"
              type="number"
              min={minSecs}
              max={maxSecs}
              step={1}
              value={Number.isNaN(draft.hold_update_secs) ? "" : draft.hold_update_secs}
              onChange={(e) => update({ hold_update_secs: e.target.valueAsNumber })}
            />
            <span className="muted-note">seconds</span>
          </div>
          {!secsOk && (
            <p className="field-error">
              {minSecs} to {maxSecs} seconds, whole numbers.
            </p>
          )}
          {fieldError("hold_update_secs")}
        </div>
      </div>

      <div className="settings-foot">
        <p className="label">Applies to the next call</p>
        <div className="incoming-actions">
          <button type="button" className="btn" disabled={busy !== null || loaded.is_default} onClick={() => void reset()}>
            <RotateCcw strokeWidth={1.7} aria-hidden="true" />
            {busy === "reset" ? "Resetting" : "Reset to defaults"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!dirty || !valid || busy !== null}
            aria-busy={busy === "save"}
            onClick={() => void save()}
          >
            <Save strokeWidth={1.7} aria-hidden="true" />
            {busy === "save" ? "Saving" : "Save"}
          </button>
        </div>
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
