import { AlertTriangle, ChevronDown, ChevronUp, Copy, ExternalLink as ExternalIcon, Phone, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  callerHref,
  copyText,
  demosApi as defaultApi,
  type DemoPersona,
  type DemosApi,
  type DemosResponse,
} from "../api/demosClient";
import { Shell, type ShellAgent } from "./Shell";

interface Props {
  shell: ShellAgent;
  api?: DemosApi;
  /** Puts text on the clipboard. Default: navigator.clipboard. */
  copy?: (text: string) => Promise<void>;
}

const ALL = "all";
const COPIED_MS = 2000;
const clipboardCopy = (text: string) => navigator.clipboard.writeText(text);

/** Feature chip colors (DESIGN.md chips): verified/ok, warnings, AI, alerts, info. */
const FEATURE_TONE: Record<string, string> = {
  identity_verified: "chip-ok",
  close_match: "chip-warn",
  not_verified: "chip-warn",
  name_collision: "chip-warn",
  not_found: "chip-bad",
  repeat_caller: "chip-warn",
  duplicate_charge: "chip-bad",
  kb_article: "chip-info",
  tone_tip: "chip-ai",
  wrap_up: "chip-ai",
  emergency: "chip-bad",
};

function DemoCard({
  p,
  labels,
  copy,
}: {
  p: DemoPersona;
  labels: Record<string, string>;
  copy: (text: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);

  async function onCopy() {
    try {
      await copy(copyText(p));
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
  }

  const scriptId = `demo-script-${p.key}`;
  return (
    <article className={`panel demo-card${p.hero ? " is-hero" : ""}`} data-testid={`demo-card-${p.key}`}>
      <header className="demo-card-head">
        <div>
          <h2>{p.name}</h2>
          <div className="demo-meta">
            <span className="label">DOB</span> <span className="mono">{p.dob}</span>
            <span className="demo-dot" aria-hidden="true" />
            <span>{p.plan}</span>
          </div>
        </div>
        {p.hero && (
          <span className="chip chip-ai">
            <Sparkles strokeWidth={1.7} aria-hidden="true" />
            Hero demo
          </span>
        )}
      </header>

      <div className="demo-id">
        <span className="label">Member ID</span>
        <span className="mono demo-id-value" data-testid="demo-member-id">
          {p.member_id_groups}
        </span>
      </div>

      <p className="demo-case">{p.case}</p>

      {p.warning && (
        <p className="demo-warning" role="note">
          <AlertTriangle strokeWidth={1.7} aria-hidden="true" />
          {p.warning}
        </p>
      )}

      <dl className="demo-facts">
        <div>
          <dt className="label">Pack</dt>
          <dd className="mono">{p.pack}</dd>
        </div>
        <div>
          <dt className="label">To start, say</dt>
          <dd>“{p.start_line}”</dd>
        </div>
        <div>
          <dt className="label">The audience sees</dt>
          <dd>
            <span className="demo-headline">{p.audience_sees.headline}</span>
            <span className="demo-cards">{p.audience_sees.cards.join(" · ")}</span>
          </dd>
        </div>
      </dl>

      <ul className="demo-features" aria-label="Features">
        {p.features.map((f) => (
          <li key={f} className={`chip ${FEATURE_TONE[f] ?? "chip-neutral"}`} data-testid="demo-feature">
            {labels[f] ?? f}
          </li>
        ))}
      </ul>

      <div className="demo-actions">
        <button type="button" className="btn" onClick={() => void onCopy()}>
          <Copy strokeWidth={1.7} aria-hidden="true" />
          Copy details
        </button>
        <a className="btn btn-primary" href={callerHref(p)} target="_blank" rel="noopener noreferrer">
          <ExternalIcon strokeWidth={1.7} aria-hidden="true" />
          Start browser call
          <span className="sr-only"> as {p.name} (opens in a new tab)</span>
        </a>
        <button
          type="button"
          className="btn btn-quiet"
          aria-expanded={open}
          aria-controls={scriptId}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <ChevronUp strokeWidth={1.7} aria-hidden="true" /> : <ChevronDown strokeWidth={1.7} aria-hidden="true" />}
          {open ? "Hide script" : "Show script"}
        </button>
        {copied && (
          <span className="demo-copied label" role="status">
            {copied === "ok" ? "Copied" : "Copy failed"}
          </span>
        )}
      </div>

      {open && (
        <ol className="demo-script" id={scriptId} aria-label="Script">
          {p.script.map((s, i) => (
            <li key={i} className={`demo-step is-${s.to}`}>
              <span className="label demo-step-to">{s.to === "bot" ? "To the bot" : "To the agent"}</span>
              <span className="demo-step-say">{s.say}</span>
              {s.note && <span className="demo-step-note">{s.note}</span>}
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

/**
 * Demos (P5-10, docs/demos.md): one card per demo caller, with the details to give the
 * bot, what to say, and what the audience should see. Filters by feature and by pack.
 * Data: GET /demo/personas (server/agent_assist_server/demo_personas.yaml, checked
 * against the seed).
 */
export function Demos({ shell, api = defaultApi, copy = clipboardCopy }: Props) {
  const [data, setData] = useState<DemosResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feature, setFeature] = useState<string | null>(null);
  const [pack, setPack] = useState<string>(ALL);

  useEffect(() => {
    let live = true;
    api
      .list()
      .then((d) => live && setData(d))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load the demos."));
    return () => {
      live = false;
    };
  }, [api]);

  const labels = useMemo(() => Object.fromEntries((data?.features ?? []).map((f) => [f.id, f.label])), [data]);
  const packs = useMemo(() => [...new Set((data?.personas ?? []).map((p) => p.pack))], [data]);
  const shown = (data?.personas ?? []).filter(
    (p) => (!feature || p.features.includes(feature)) && (pack === ALL || p.pack === pack),
  );
  const usedFeatures = (data?.features ?? []).filter((f) => data?.personas.some((p) => p.features.includes(f.id)));

  return (
    <Shell agent={{ ...shell, page: "demos" }} className="demos-stage">
      <section className="demos" aria-label="Demos">
        <div className="demos-head">
          <div>
            <h1>Demos</h1>
            <p className="sub">Pick a caller, give the bot their details, and say the lines. Mock data only.</p>
          </div>
          {data && (
            <section className="panel demos-phone" aria-label="Phone line">
              <Phone strokeWidth={1.7} aria-hidden="true" />
              <div>
                <div className="label">Phone line</div>
                <span className="mono demos-phone-number" data-testid="demos-phone-number">
                  {data.phone_line.number}
                </span>
                <div className="muted-note">App password: {data.phone_line.password_hint}</div>
              </div>
            </section>
          )}
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!data && !error && <p className="muted-note">Loading demos</p>}

        {data && (
          <>
            <div className="demos-filters">
              <div className="demos-filter-row" role="group" aria-label="Filter by feature">
                {usedFeatures.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={`segment demos-chip${feature === f.id ? " is-on" : ""}`}
                    aria-pressed={feature === f.id}
                    onClick={() => setFeature((cur) => (cur === f.id ? null : f.id))}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <div className="segmented" role="radiogroup" aria-label="Filter by pack">
                {[ALL, ...packs].map((id) => (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={pack === id}
                    className={`segment${pack === id ? " is-on" : ""}`}
                    onClick={() => setPack(id)}
                  >
                    {id === ALL ? "All packs" : id}
                  </button>
                ))}
              </div>
            </div>

            {shown.length === 0 ? (
              <p className="muted-note">No demo matches these filters.</p>
            ) : (
              <div className="demos-grid">
                {shown.map((p) => (
                  <DemoCard key={p.key} p={p} labels={labels} copy={copy} />
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </Shell>
  );
}
