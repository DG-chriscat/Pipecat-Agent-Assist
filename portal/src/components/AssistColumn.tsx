import {
  AlertTriangle,
  BookOpen,
  Check,
  ChevronDown,
  CornerDownLeft,
  FileText,
  HeartPulse,
  Link2,
  ListChecks,
  Pin,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from "react";
import tileAluminum from "../assets/tex/aluminum.jpg";
import tileBakelite from "../assets/tex/bakelite.jpg";
import tileClay from "../assets/tex/clay.jpg";
import tileStone from "../assets/tex/stone.jpg";
import {
  isNextStepBody,
  isTableBody,
  planView,
  type AssistCard,
  type AssistState,
  type Cell,
  type LocalAction,
  type PlanView,
  type TableBody,
} from "../state/assist";
import { fieldLabel, formatCell, isMoneyField } from "../state/format";
import { formatClock } from "../state/offer";
import { segmentText } from "../state/transcript";

/** How long the accepted or dismissed hero shows before the next state (ms). */
export const ACK_MS = 1600;

export interface AssistColumnProps {
  assist: AssistState;
  onAction: (a: LocalAction) => void;
  firstName: string;
}

const AiLabel = () => (
  <span className="chip chip-ai ai-label">
    <Sparkles strokeWidth={1.7} aria-hidden="true" />
    AI suggested
  </span>
);

function heardLine(card: AssistCard): string {
  return card.trigger.phrase ? `Heard "${card.trigger.phrase}"` : "From the conversation";
}

/** Ids like KB-CL-009 wrap at spaces, never inside the id (U+2011 non-breaking hyphen). */
const noBreakHyphens = (s: string) => s.replace(/-/g, "\u2011");

/** The trigger line with its phrase marked (Show me why). */
function TriggerQuote({ card }: { card: AssistCard }) {
  const { text, start, end } = card.trigger;
  const ranges = start !== null && end !== null ? [{ start, end }] : [];
  return (
    <p className="why-quote">
      {segmentText(text, ranges).map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
    </p>
  );
}

/** Record facts behind the hero: rows of supporting cards about the same records. */
function heroFacts(hero: AssistCard, cards: AssistCard[]): { field: string; value: Cell }[] {
  const ids = new Set(hero.source.records.map((r) => r.id));
  for (const c of cards) {
    if (c.kind !== "record" || !isTableBody(c.body)) continue;
    const body: TableBody = c.body;
    const row = body.rows.find((r) => Object.values(r).some((v) => typeof v === "string" && ids.has(v)));
    if (row) return body.fields.map((f) => ({ field: f, value: row[f] ?? null }));
  }
  return [];
}

function WhyPanel({ card, cards, panelRef }: { card: AssistCard; cards: AssistCard[]; panelRef: Ref<HTMLDivElement> }) {
  const facts = heroFacts(card, cards);
  return (
    <div className="why-more" data-testid="hero-why-more" ref={panelRef}>
      <div>
        <div className="label">Trigger</div>
        <TriggerQuote card={card} />
      </div>
      {card.source.records.length > 0 && (
        <div>
          <div className="label">Records</div>
          <ul className="why-records">
            {card.source.records.map((r) => (
              <li key={`${r.connector}:${r.id}`} className="mono">
                {r.connector} · {r.id}
              </li>
            ))}
          </ul>
        </div>
      )}
      {facts.length > 0 && (
        <dl className="why-facts">
          {facts.map((f) => (
            <div key={f.field}>
              <dt>{fieldLabel(f.field)}</dt>
              <dd>{formatCell(f.field, f.value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function HeroSuggestion({ card, cards, onAction }: { card: AssistCard; cards: AssistCard[]; onAction: (a: LocalAction) => void }) {
  const [open, setOpen] = useState(false);
  const more = useRef<HTMLDivElement>(null);
  // The panel opens above the action row, which stays pinned at the hero's bottom;
  // only the hero box scrolls, just enough to show the panel.
  useLayoutEffect(() => {
    const el = more.current;
    const box = el?.closest<HTMLElement>(".hero");
    const acts = box?.querySelector<HTMLElement>(".acts");
    if (!open || !el || !box || !acts) return;
    const hidden = el.offsetTop + el.offsetHeight - (box.scrollTop + box.clientHeight - acts.offsetHeight);
    if (hidden > 0) box.scrollTop += hidden + 8;
  }, [open]);
  if (!isNextStepBody(card.body)) return null;
  const body = card.body;
  return (
    <>
      <div className="eyebrow">
        <span className="chip chip-ai">
          <Sparkles strokeWidth={1.7} aria-hidden="true" />
          Next best step
        </span>
        <span className="label">{heardLine(card)}</span>
        <span className="eyebrow-end label">AI suggested</span>
      </div>
      <h2 className="hero-headline">{body.headline}</h2>
      <p className="hero-why">{body.why}</p>
      <div className="script">
        <div className="label">Say this</div>
        <p>{body.script}</p>
      </div>
      {open && <WhyPanel card={card} cards={cards} panelRef={more} />}
      <div className="acts">
        <button type="button" className="btn btn-primary" onClick={() => onAction({ type: "accept", cardId: card.id })}>
          <Check strokeWidth={1.7} aria-hidden="true" />
          {body.action.label}
          <span className="kbd kbd-on-primary" aria-hidden="true">
            <CornerDownLeft strokeWidth={1.7} />
          </span>
        </button>
        <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide why" : "Show me why"}
          <ChevronDown className={open ? "flip" : undefined} strokeWidth={1.7} aria-hidden="true" />
        </button>
        <button type="button" className="btn" onClick={() => onAction({ type: "dismiss", cardId: card.id })}>
          Not now
          <span className="kbd" aria-hidden="true">
            Esc
          </span>
        </button>
        <span className="src" data-testid="hero-source" title={card.source.label}>
          <Link2 strokeWidth={1.7} aria-hidden="true" />
          {noBreakHyphens(card.source.label)}
        </span>
      </div>
    </>
  );
}

function Hero({ assist, onAction }: { assist: AssistState; onAction: (a: LocalAction) => void }) {
  const { ack, hero, cards, status } = assist;
  const state = ack ? ack.outcome : hero && isNextStepBody(hero.body) ? "suggestion" : "listening";
  const key = ack ? `${ack.outcome}:${ack.card.id}` : hero ? `hero:${hero.id}` : "listening";
  const box = useRef<HTMLDivElement>(null);
  const lastHeight = useRef(0);

  // The accepted or dismissed state shows briefly, then the next hero (or Listening).
  const settle = useRef(onAction);
  useEffect(() => {
    settle.current = onAction;
  });
  useEffect(() => {
    if (!ack) return;
    const t = setTimeout(() => settle.current({ type: "settle" }), ACK_MS);
    return () => clearTimeout(t);
  }, [ack]);

  // Hold the suggestion's height through the ack so nothing below jumps.
  useLayoutEffect(() => {
    if (state === "suggestion" && box.current) lastHeight.current = box.current.offsetHeight;
  });
  // A new hero starts at its top.
  useLayoutEffect(() => {
    if (box.current) box.current.scrollTop = 0;
  }, [key]);
  const hold = ack && lastHeight.current ? { minHeight: lastHeight.current } : undefined;

  let content: ReactNode;
  if (ack && isNextStepBody(ack.card.body)) {
    const accepted = ack.outcome === "accepted";
    content = (
      <div className="hero-ack">
        {accepted ? (
          <span className="ack-check" data-testid="ack-check" aria-hidden="true">
            <Check strokeWidth={2.2} />
          </span>
        ) : (
          <span className="ack-check is-dismissed" aria-hidden="true">
            <X strokeWidth={2} />
          </span>
        )}
        <div>
          <div className="label">{accepted ? "Done" : "Dismissed"}</div>
          <h2 className="hero-headline">{accepted ? ack.card.body.action.label : ack.card.body.headline}</h2>
          <p className="hero-why">
            {accepted
              ? "Logged as a simulated action on this call. Nothing was sent to the source system."
              : "Listening for the next step."}
          </p>
        </div>
      </div>
    );
  } else if (state === "suggestion" && hero) {
    content = <HeroSuggestion card={hero} cards={cards} onAction={onAction} />;
  } else {
    const pending = status?.state === "rate_limited" ? (status.pending ?? 0) : 0;
    content = (
      <>
        <div className="eyebrow">
          <span className="chip chip-ai">
            <Sparkles strokeWidth={1.7} aria-hidden="true" />
            Next best step
          </span>
        </div>
        <div className="hero-listening">
          <div className="orb orb-sm" aria-hidden="true" />
          <div>
            <h2 className="hero-title">Listening with you</h2>
            <p className="hero-why">
              {pending > 0
                ? `${pending} more suggestion${pending === 1 ? " is" : "s are"} queued and show up shortly.`
                : "The next best step shows up here as the call goes, with the words to say."}
            </p>
          </div>
        </div>
      </>
    );
  }

  return (
    <div
      ref={box}
      className={`hero is-${state}`}
      data-testid="hero"
      data-state={state}
      style={hold}
      aria-live="polite"
    >
      <div className="hero-body" key={key}>
        {content}
      </div>
    </div>
  );
}

// ---- shelf -------------------------------------------------------------------------

const SWATCH: Record<AssistCard["kind"], { tex: string; icon: ReactNode }> = {
  record: { tex: tileAluminum, icon: <FileText strokeWidth={1.7} /> },
  article: { tex: tileStone, icon: <BookOpen strokeWidth={1.7} /> },
  alert: { tex: tileClay, icon: <AlertTriangle strokeWidth={1.7} /> },
  next_step: { tex: tileStone, icon: <Sparkles strokeWidth={1.7} /> },
};

function Swatch({ tex, icon }: { tex: string; icon: ReactNode }) {
  return (
    <span className="sw" style={{ backgroundImage: `url(${tex})` }} aria-hidden="true">
      {icon}
    </span>
  );
}

const idField = (fields: string[]) => fields.find((f) => /(^|_)id$/.test(f)) ?? fields[0];
const statusField = (fields: string[]) => fields.find((f) => f === "status");

/** One big answer and one line of context for a record, article, or alert. */
function answer(card: AssistCard): { big: string; context: string | null } {
  if (isNextStepBody(card.body)) return { big: card.body.action.label, context: card.body.headline };
  if (!isTableBody(card.body) || card.body.rows.length === 0) return { big: card.title, context: null };
  const { fields, rows, summary } = card.body;
  const row = rows[0];
  if (card.kind === "article") {
    const title = fields.includes("title") ? formatCell("title", row.title) : formatCell(fields[0], row[fields[0]]);
    const s = fields.includes("summary") ? row.summary : null;
    return { big: title, context: typeof s === "string" ? s : summary };
  }
  if (rows.length > 1) {
    return { big: `${rows.length} ${card.title.toLowerCase()}`, context: summary };
  }
  const money = fields.find(isMoneyField);
  const big = money ? formatCell(money, row[money]) : formatCell(idField(fields), row[idField(fields)]);
  const context =
    summary ??
    fields
      .filter((f) => f !== money && f !== idField(fields))
      .slice(0, 3)
      .map((f) => formatCell(f, row[f]))
      .join(" · ");
  return { big, context };
}

/** Up to three columns that fit a tile: id, amount (or date), status. */
function compactColumns(fields: string[]): string[] {
  const id = idField(fields);
  const money = fields.find(isMoneyField) ?? fields.find((f) => /(date|_at|_on)$/.test(f));
  const status = statusField(fields) ?? fields.at(-1);
  return [...new Set([id, money, status].filter((f): f is string => !!f))];
}

function RowsTable({ body }: { body: TableBody }) {
  const cols = compactColumns(body.fields);
  return (
    <table className="tile-table">
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c} scope="col">
              {fieldLabel(c)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.rows.map((r, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c} className={isMoneyField(c) || /(^|_)id$/.test(c) ? "mono" : undefined}>
                {formatCell(c, r[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CardTile({
  card,
  pinned,
  rating,
  onAction,
}: {
  card: AssistCard;
  pinned: boolean;
  rating: "up" | "down" | undefined;
  onAction: (a: LocalAction) => void;
}) {
  const { big, context } = answer(card);
  const sw = SWATCH[card.kind];
  const multi = isTableBody(card.body) && card.body.rows.length > 1 && card.kind !== "article";
  return (
    <article
      className={`panel tile is-${card.kind}${card.kind === "alert" ? " is-alert" : ""}${pinned ? " is-pinned" : ""}`}
      data-testid={`tile-${card.id}`}
      data-kind={card.kind}
      aria-label={card.title}
    >
      <div className="tile-top">
        <Swatch tex={sw.tex} icon={sw.icon} />
        <b>{card.title}</b>
        <span className="tile-tools">
          <button
            type="button"
            className={`tool${pinned ? " is-on" : ""}`}
            aria-label={pinned ? "Unpin" : "Pin"}
            aria-pressed={pinned}
            title={pinned ? "Unpin" : "Pin"}
            onClick={() => onAction({ type: pinned ? "unpin" : "pin", cardId: card.id })}
          >
            <Pin strokeWidth={1.7} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="tool"
            aria-label="Dismiss"
            title="Dismiss"
            onClick={() => onAction({ type: "dismiss", cardId: card.id })}
          >
            <X strokeWidth={1.7} aria-hidden="true" />
          </button>
        </span>
      </div>
      <div className="big" data-testid="tile-big">
        {big}
      </div>
      {context && (
        <p className="tile-context" title={context}>
          {context}
        </p>
      )}
      <div className="tile-foot">
        <AiLabel />
        <span className="tile-rate">
          <button
            type="button"
            className={`tool${rating === "up" ? " is-on" : ""}`}
            aria-label="Helpful"
            aria-pressed={rating === "up"}
            title="Helpful"
            onClick={() => onAction({ type: "rate", cardId: card.id, rating: "up" })}
          >
            <ThumbsUp strokeWidth={1.7} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`tool${rating === "down" ? " is-on" : ""}`}
            aria-label="Not helpful"
            aria-pressed={rating === "down"}
            title="Not helpful"
            onClick={() => onAction({ type: "rate", cardId: card.id, rating: "down" })}
          >
            <ThumbsDown strokeWidth={1.7} aria-hidden="true" />
          </button>
        </span>
      </div>
      {multi && isTableBody(card.body) && <RowsTable body={card.body} />}
      <div className="tile-src" data-testid="tile-source" title={card.source.records.map((r) => r.id).join(", ")}>
        <Link2 strokeWidth={1.7} aria-hidden="true" />
        {card.source.label}
      </div>
    </article>
  );
}

function PlanTile({ plan, onAction }: { plan: PlanView; onAction: (a: LocalAction) => void }) {
  const pct = plan.total ? Math.round((plan.done / plan.total) * 100) : 0;
  const box = useRef<HTMLUListElement>(null);
  // Keep the next step in view inside a short tile (only the list scrolls).
  useLayoutEffect(() => {
    const el = box.current;
    const next = el?.querySelector<HTMLElement>("li.is-next");
    if (!el || !next || el.matches(":hover")) return;
    el.scrollTop = Math.max(0, next.offsetTop - el.offsetTop - 4);
  }, [plan.next]);
  return (
    <section className="panel tile tile-plan" data-testid="tile-plan" aria-label="Call plan">
      <div className="tile-top">
        <Swatch tex={tileStone} icon={<ListChecks strokeWidth={1.7} />} />
        <b>Call plan</b>
        <span className="tile-count mono">
          {plan.done} of {plan.total}
        </span>
      </div>
      <div className="progress" role="progressbar" aria-valuenow={plan.done} aria-valuemin={0} aria-valuemax={plan.total} aria-label="Call plan progress">
        <i style={{ width: `${pct}%` }} />
      </div>
      <ul className="plan" ref={box}>
        {plan.steps.map((s) => (
          <li key={s.id} className={s.done ? "is-done" : s.id === plan.next ? "is-next" : undefined}>
            <label>
              <input
                type="checkbox"
                checked={s.done}
                onChange={() => onAction({ type: "tick", stepId: s.id, done: !s.done })}
              />
              <span className="plan-box" aria-hidden="true">
                {s.done && <Check strokeWidth={2.4} />}
              </span>
              <span className="plan-label">{s.label}</span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EmptyTile({ tex, icon, title, empty }: { tex: string; icon: ReactNode; title: string; empty: string }) {
  return (
    <div className="panel tile is-empty" data-testid="shelf-tile">
      <div className="tile-top">
        <Swatch tex={tex} icon={icon} />
        <b>{title}</b>
      </div>
      <p>{empty}</p>
    </div>
  );
}

/**
 * Supporting cards in a stable order: pinned first, then arrival order. While the
 * pointer is over the shelf the order is frozen (DESIGN.md Motion: tiles never reorder
 * while hovered); removed cards drop out and new ones go at the end.
 */
function useShelfOrder(cards: AssistCard[], pinned: string[]) {
  const [frozen, setFrozen] = useState<string[] | null>(null);
  const ids = cards.map((c) => c.id);
  const live = [...ids.filter((id) => pinned.includes(id)), ...ids.filter((id) => !pinned.includes(id))];
  const order = frozen ? [...frozen.filter((id) => ids.includes(id)), ...live.filter((id) => !frozen.includes(id))] : live;
  return {
    ordered: order.map((id) => cards.find((c) => c.id === id)!),
    freeze: () => setFrozen(live),
    thaw: () => setFrozen(null),
  };
}

function Shelf({ assist, onAction }: { assist: AssistState; onAction: (a: LocalAction) => void }) {
  const plan = planView(assist);
  const { ordered, freeze, thaw } = useShelfOrder(assist.cards, assist.pinned);
  const tile = (c: AssistCard) => (
    <CardTile key={c.id} card={c} pinned={assist.pinned.includes(c.id)} rating={assist.ratings[c.id]} onAction={onAction} />
  );
  // Fixed slots (Studio: record, call plan, tone tip): the first card takes the record
  // slot, the plan keeps the second, more cards follow to the right. Nothing shifts.
  const [first, ...rest] = ordered;
  return (
    <div className="shelf" onMouseEnter={freeze} onMouseLeave={thaw}>
      {first ? (
        tile(first)
      ) : (
        <EmptyTile
          tex={tileAluminum}
          icon={<FileText strokeWidth={1.7} />}
          title="Records"
          empty="Claims and payments appear when they come up."
        />
      )}
      {plan ? (
        <PlanTile plan={plan} onAction={onAction} />
      ) : (
        <EmptyTile
          tex={tileStone}
          icon={<ListChecks strokeWidth={1.7} />}
          title="Call plan"
          empty="Steps for this call appear here."
        />
      )}
      {rest.map(tile)}
      {rest.length === 0 && (
        <EmptyTile
          tex={tileBakelite}
          icon={<HeartPulse strokeWidth={1.7} />}
          title="Tone tip"
          empty="A tip appears if the caller's mood drops."
        />
      )}
    </div>
  );
}

function Earlier({ assist }: { assist: AssistState }) {
  return (
    <div className="hist">
      <div className="label" id="hist-label">
        Earlier this call
      </div>
      {assist.history.length === 0 ? (
        <p className="muted-note">Steps you finish show up here.</p>
      ) : (
        <ul className="hist-list" aria-labelledby="hist-label">
          {assist.history.map((h) => (
            <li key={h.key} className="hrow">
              <span className="tick" aria-hidden="true">
                <Check strokeWidth={2} />
              </span>
              <b>{h.label}</b>
              {h.detail && <span className="t2">{h.detail}</span>}
              <span className="tm">{formatClock(h.atSecs)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function isTyping(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
}

/**
 * Enter accepts the hero action, Esc dismisses it (DESIGN.md principle 5). Bound only
 * while the column is mounted (on a call) and a suggestion is showing. Keys inside the
 * rail menu or a text field are theirs; Enter on a focused button presses that button.
 */
function useHeroKeys(assist: AssistState, onAction: (a: LocalAction) => void) {
  const latest = useRef({ assist, onAction });
  useEffect(() => {
    latest.current = { assist, onAction };
  });
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      if (e.key !== "Enter" && e.key !== "Escape") return;
      const t = e.target;
      if (t instanceof Element && t.closest("[role=menu]")) return;
      if (isTyping(t)) return;
      if (e.key === "Enter" && t instanceof Element && t.closest("button, a, [role=button]")) return;
      const { assist: a, onAction: act } = latest.current;
      const hero = a.ack ? null : a.hero;
      if (!hero || !isNextStepBody(hero.body)) return;
      e.preventDefault();
      act({ type: e.key === "Enter" ? "accept" : "dismiss", cardId: hero.id });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/**
 * Assist column (DESIGN.md "Assist column", docs/assist.md): the next best step hero,
 * the shelf of supporting cards with the call plan, Earlier this call, and the Ask bar.
 */
export function AssistColumn({ assist, onAction, firstName }: AssistColumnProps) {
  useHeroKeys(assist, onAction);
  return (
    <section className="assist" aria-label="Assist">
      <Hero assist={assist} onAction={onAction} />
      <Shelf assist={assist} onAction={onAction} />
      <Earlier assist={assist} />
      <label className="panel ask">
        <Sparkles strokeWidth={1.7} aria-hidden="true" />
        <input type="text" aria-label="Ask" placeholder={`Ask about ${firstName}`} disabled />
        <span className="kbd" aria-hidden="true">
          ⌘ K
        </span>
      </label>
    </section>
  );
}
