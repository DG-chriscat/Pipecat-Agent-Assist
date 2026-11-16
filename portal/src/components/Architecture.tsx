import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  Ear,
  HeartPulse,
  Maximize2,
  Minimize2,
  Monitor,
  Phone,
  Play,
  Server,
  Sparkles,
  Users,
  Volume2,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import aluminum from "../assets/tex/aluminum.jpg";
import bakelite from "../assets/tex/bakelite.jpg";
import copper from "../assets/tex/copper.jpg";
import stone from "../assets/tex/stone.jpg";
import {
  BOT_GROUP,
  EDGES,
  FAQ,
  MAP,
  NODES,
  POC_VS_PROD,
  PRIVACY,
  WALK,
  type ArchNode,
  type IconName,
  type Stat,
  type Swatch,
  type View,
} from "../architecture/content";
import { exportPng as defaultExportPng } from "../architecture/exportPng";
import { ExternalLink } from "./ExternalLink";
import { Shell, type ShellAgent } from "./Shell";

const ICONS: Record<IconName, LucideIcon> = {
  phone: Phone,
  users: Users,
  ear: Ear,
  audio: AudioLines,
  heart: HeartPulse,
  workflow: Workflow,
  sparkles: Sparkles,
  volume: Volume2,
  server: Server,
  monitor: Monitor,
  database: Database,
};

const TEXTURES: Partial<Record<Swatch, string>> = { copper, aluminum, bakelite, stone };

const SUBTITLE: Record<View, string> = {
  business: "How a call moves from the caller's phone, through Pipecat and Deepgram, to the agent's screen.",
  technical: "Services, protocols, and models behind each step. One Pipecat bot process per call.",
};

const SECTIONS = ["Data and privacy", "Prospect questions", "PoC vs production"] as const;
type SectionName = (typeof SECTIONS)[number];

export type ExportPng = (el: HTMLElement, filename: string) => Promise<void>;

interface Props {
  shell: ShellAgent;
  /** Injected in tests; the default renders the diagram with html-to-image. */
  exportPng?: ExportPng;
}

function SwatchIcon({ node, size = "sm" }: { node: ArchNode; size?: "sm" | "lg" }) {
  const Icon = ICONS[node.icon];
  const tex = TEXTURES[node.swatch];
  return (
    <span
      className={`sw arch-sw sw-${node.swatch}${size === "lg" ? " is-lg" : ""}`}
      style={tex ? { backgroundImage: `url(${tex})` } : undefined}
      aria-hidden="true"
    >
      <Icon strokeWidth={1.7} />
    </span>
  );
}

function Stats({ stats }: { stats: Stat[] }) {
  return (
    <div className="arch-stats">
      {stats.map((s) => (
        <div key={`${s.value}-${s.label}`} className="arch-stat" title={`Source: ${s.source}`}>
          <b>{s.value}</b>
          <span>{s.label}</span>
        </div>
      ))}
    </div>
  );
}

function NodePanel({ node, view, onClose }: { node: ArchNode; view: View; onClose: () => void }) {
  const text = node[view];
  return (
    <>
      <div className="arch-ptop">
        <SwatchIcon node={node} size="lg" />
        <div>
          <div className="label">{node.role}</div>
          <h2>{text.title}</h2>
        </div>
        <button type="button" className="arch-x" aria-label="Close details" title="Close (Esc)" onClick={onClose}>
          <X strokeWidth={1.7} aria-hidden="true" />
        </button>
      </div>
      <p className="arch-lead">{text.lead}</p>
      {view === "technical" && node.facts && (
        <dl className="arch-kv">
          {node.facts.map((f) => (
            <div key={f.k}>
              <dt className="label">{f.k}</dt>
              <dd>{f.v}</dd>
            </div>
          ))}
        </dl>
      )}
      <h3 className="label arch-h">Why we chose it</h3>
      <ul className="arch-why">
        {node.why.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      {node.stats.length > 0 && (
        <>
          <h3 className="label arch-h">Measured in this build</h3>
          <Stats stats={node.stats} />
        </>
      )}
      <h3 className="label arch-h">Docs</h3>
      <div className="arch-links">
        {node.links.map((l) => (
          <ExternalLink key={l.href} href={l.href} className="arch-link">
            {l.label}
          </ExternalLink>
        ))}
      </div>
    </>
  );
}

function WalkPanel({ step, view, onStep }: { step: number; view: View; onStep: (i: number) => void }) {
  const s = WALK[step];
  const prev = WALK[step - 1];
  const next = WALK[step + 1];
  const lit = s.nodes.map((id) => NODES.find((n) => n.id === id)![view].title);
  return (
    <>
      <div className="label">
        Step {step + 1} of {WALK.length}
      </div>
      <h2 className="arch-step-title">{s.title}</h2>
      <p className="arch-lead">{s.text}</p>
      {s.stats.length > 0 && <Stats stats={s.stats} />}
      <h3 className="label arch-h">Lit up on the map</h3>
      <p className="arch-muted">{lit.join(", ")}.</p>
      <div className="arch-stepnav">
        {prev ? (
          <button type="button" className="btn" aria-label={`Back: ${prev.title}`} onClick={() => onStep(step - 1)}>
            <ChevronLeft strokeWidth={1.7} aria-hidden="true" />
            {prev.title}
          </button>
        ) : (
          <span />
        )}
        {next && (
          <button
            type="button"
            className="btn btn-primary"
            aria-label={`Next: ${next.title}`}
            onClick={() => onStep(step + 1)}
          >
            {next.title}
            <ChevronRight strokeWidth={1.7} aria-hidden="true" />
          </button>
        )}
      </div>
    </>
  );
}

function SectionsPanel({ tab, onTab }: { tab: SectionName; onTab: (t: SectionName) => void }) {
  const id = (t: SectionName) => `arch-tab-${SECTIONS.indexOf(t)}`;
  return (
    <>
      <div className="label">About this build</div>
      <h2>Questions prospects ask</h2>
      <p className="arch-muted arch-hint">Click any box on the map for details, or walk through a call.</p>
      <div className="arch-tabs" role="tablist" aria-label="Sections">
        {SECTIONS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={id(t)}
            aria-selected={tab === t}
            aria-controls={`${id(t)}-panel`}
            className={`arch-tab${tab === t ? " is-on" : ""}`}
            onClick={() => onTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="arch-section" role="tabpanel" id={`${id(tab)}-panel`} aria-labelledby={id(tab)}>
        {tab === "Data and privacy" &&
          PRIVACY.map((p) => (
            <div key={p.title} className="arch-qa">
              <h3>{p.title}</h3>
              <p>{p.text}</p>
            </div>
          ))}
        {tab === "Prospect questions" &&
          FAQ.map((f) => (
            <div key={f.q} className="arch-qa">
              <h3>{f.q}</h3>
              <p>{f.a}</p>
              {f.links && (
                <div className="arch-inline-links">
                  {f.links.map((l) => (
                    <ExternalLink key={l.href} href={l.href} className="arch-inline-link">
                      {l.label}
                    </ExternalLink>
                  ))}
                </div>
              )}
            </div>
          ))}
        {tab === "PoC vs production" && (
          <table className="arch-table">
            <thead>
              <tr>
                <th scope="col" className="label">
                  Area
                </th>
                <th scope="col" className="label">
                  This PoC
                </th>
                <th scope="col" className="label">
                  Production
                </th>
              </tr>
            </thead>
            <tbody>
              {POC_VS_PROD.map((r) => (
                <tr key={r.area}>
                  <th scope="row">{r.area}</th>
                  <td>{r.poc}</td>
                  <td>{r.production}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

/** Fit the 1100 x 620 map into the canvas (laptop 1440 x 900 up to a 1920 x 1080 projector). */
function useFit(walking: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState({ scale: 1, left: 18, top: 30 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (!w || !h) return;
      const bottom = walking ? 74 : 52;
      const scale = Math.min((w - 36) / MAP.w, (h - 30 - bottom) / MAP.h);
      setFit({ scale, left: (w - MAP.w * scale) / 2, top: 26 + Math.max(0, (h - 26 - bottom - MAP.h * scale) / 2) });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [walking]);
  return { ref, fit };
}

/**
 * Architecture page (PRD AP-21): one diagram from caller to database for prospect
 * presentations, Business and Technical views, node details, a nine step call walk,
 * and the privacy / questions / PoC sections. Content: architecture/content.ts.
 */
export function Architecture({ shell, exportPng = defaultExportPng }: Props) {
  const [view, setView] = useState<View>("business");
  const [selected, setSelected] = useState<string | null>(null);
  const [walk, setWalk] = useState<number | null>(null);
  const [tab, setTab] = useState<SectionName>("Data and privacy");
  const [presenting, setPresenting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const { ref: canvas, fit } = useFit(walk !== null);

  const stepTo = useCallback((i: number) => setWalk(Math.max(0, Math.min(WALK.length - 1, i))), []);

  const endPresent = useCallback(() => {
    setPresenting(false);
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") {
        if (selected) setSelected(null);
        else if (walk !== null) setWalk(null);
        else if (presenting && !document.fullscreenElement) setPresenting(false);
        else return;
        e.preventDefault();
      } else if (walk !== null && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        e.preventDefault();
        setSelected(null);
        stepTo(walk + (e.key === "ArrowRight" ? 1 : -1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, walk, presenting, stepTo]);

  // The browser leaves full screen on its own Esc: present mode ends with it.
  useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) setPresenting(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function present() {
    setPresenting(true);
    const req = document.documentElement.requestFullscreen;
    if (typeof req === "function") {
      void req.call(document.documentElement).catch(() => {
        // Blocked (iframe, policy): present mode still hides the rail and header.
      });
    }
  }

  function toggleWalk() {
    setSelected(null);
    setWalk((w) => (w === null ? 0 : null));
  }

  async function doExport() {
    if (!canvas.current) return;
    setExportError(null);
    const step = walk !== null ? `-step-${walk + 1}` : "";
    try {
      await exportPng(canvas.current, `vitals-architecture-${view}${step}.png`);
    } catch {
      setExportError("Export failed. Try again.");
    }
  }

  const step = walk !== null ? WALK[walk] : null;
  const litNodes = new Set(step?.nodes ?? []);
  const litEdges = new Set(step?.edges ?? []);
  const nodeState = (id: string) => (step ? (litNodes.has(id) ? " is-lit" : " is-dim") : "");
  const edgeState = (id: string) => (step ? (litEdges.has(id) ? " is-lit" : " is-dim") : "");
  const selectedNode = selected ? NODES.find((n) => n.id === selected) : undefined;

  const tools: ReactNode = (
    <div className="arch-tools">
      <div className="segmented arch-seg" role="radiogroup" aria-label="View">
        {(["business", "technical"] as const).map((v) => (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={view === v}
            className={`segment${view === v ? " is-on" : ""}`}
            onClick={() => setView(v)}
          >
            {v === "business" ? "Business" : "Technical"}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`btn arch-btn${walk !== null ? " btn-primary" : ""}`}
        aria-pressed={walk !== null}
        onClick={toggleWalk}
      >
        <Play strokeWidth={1.7} aria-hidden="true" />
        Walk a call
      </button>
      {!presenting && (
        <button type="button" className="btn arch-btn" onClick={() => void doExport()}>
          <Download strokeWidth={1.7} aria-hidden="true" />
          Export
        </button>
      )}
      {presenting ? (
        <button type="button" className="btn arch-btn" onClick={endPresent}>
          <Minimize2 strokeWidth={1.7} aria-hidden="true" />
          Exit present
        </button>
      ) : (
        <button type="button" className="btn arch-btn" onClick={present}>
          <Maximize2 strokeWidth={1.7} aria-hidden="true" />
          Present
        </button>
      )}
    </div>
  );

  return (
    <Shell
      agent={{ ...shell, page: "architecture" }}
      right={presenting ? undefined : tools}
      bare={presenting}
      className={`arch-stage${presenting ? " is-presenting" : ""}`}
    >
      <section className={`arch is-${view}`} aria-label="Architecture">
        <div className="arch-title">
          <div>
            <h1>How Deepgram Vitals works</h1>
            <p>{SUBTITLE[view]}</p>
            {exportError && (
              <p className="error" role="alert">
                {exportError}
              </p>
            )}
          </div>
          {presenting && tools}
        </div>
        <div className="arch-main">
          <div className="arch-canvas panel" ref={canvas}>
            <div
              className="arch-map"
              style={{ transform: `translate(${fit.left}px, ${fit.top}px) scale(${fit.scale})` }}
            >
              <svg className="arch-edges" width={MAP.w} height={MAP.h} viewBox={`0 0 ${MAP.w} ${MAP.h}`} aria-hidden="true">
                {EDGES.map((e) => (
                  <path key={e.id} data-edge={e.id} className={`arch-edge${edgeState(e.id)}`} d={e.d} fill="none" />
                ))}
              </svg>
              <div
                className="arch-group"
                style={{ left: BOT_GROUP.box.x, top: BOT_GROUP.box.y, width: BOT_GROUP.box.w, height: BOT_GROUP.box.h }}
              >
                <span className="arch-group-label">{BOT_GROUP[view]}</span>
              </div>
              {NODES.map((n) => {
                const t = n[view];
                const on = selected === n.id;
                return (
                  <button
                    key={n.id}
                    type="button"
                    data-node={n.id}
                    className={`arch-node${on ? " is-selected" : ""}${nodeState(n.id)}`}
                    style={{ left: n.box.x, top: n.box.y, width: n.box.w, height: n.box.h }}
                    aria-pressed={on}
                    onClick={() => {
                      setWalk(null);
                      setSelected(on ? null : n.id);
                    }}
                  >
                    <SwatchIcon node={n} />
                    <span className="arch-node-text">
                      <b>{t.title}</b>
                      <small>{t.sub}</small>
                    </span>
                  </button>
                );
              })}
              {view === "technical" &&
                EDGES.map((e) => (
                  <span
                    key={e.id}
                    className={`arch-edge-label${edgeState(e.id)}`}
                    style={{ left: e.labelAt.x, top: e.labelAt.y }}
                  >
                    {e.label}
                  </span>
                ))}
            </div>
            {walk === null ? (
              <div className="arch-legend" aria-hidden="true">
                <span>
                  <i className="lg-ai" />
                  AI
                </span>
                <span>
                  <i className="lg-deepgram" />
                  Deepgram
                </span>
                <span>
                  <i className="lg-infra" />
                  Infrastructure
                </span>
                <span>
                  <i className="lg-data" />
                  Data
                </span>
                <span className="arch-legend-hint">Click any box for details</span>
              </div>
            ) : (
              <ol className="arch-walkbar" aria-label="Call steps">
                {WALK.map((s, i) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className={`arch-walkstep${i === walk ? " is-on" : i < walk ? " is-done" : ""}`}
                      aria-current={i === walk ? "step" : undefined}
                      onClick={() => stepTo(i)}
                    >
                      {i + 1}. {s.title}
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <aside className="arch-side panel" aria-label="Architecture details" aria-live="polite">
            {selectedNode ? (
              <NodePanel node={selectedNode} view={view} onClose={() => setSelected(null)} />
            ) : walk !== null ? (
              <WalkPanel step={walk} view={view} onStep={stepTo} />
            ) : (
              <SectionsPanel tab={tab} onTab={setTab} />
            )}
          </aside>
        </div>
      </section>
    </Shell>
  );
}
