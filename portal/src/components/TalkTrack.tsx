import { Check, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import type { DemoPersona, ExpectItem, TalkBeat } from "../api/demosClient";

/** Phase headings, in call order. */
const PHASES: { id: TalkBeat["phase"]; label: string }[] = [
  { id: "intake", label: "Intake, to the bot" },
  { id: "agent", label: "With the agent" },
  { id: "closing", label: "Closing" },
];

/** Optional feature beats (P5-11). Same ids as demo_personas.OPTIONAL_FEATURES. */
const OPTIONAL_LABEL: Record<string, string> = {
  tone_tip: "Sound frustrated",
  ask_bar: "Ask bar follow up",
  verify: "Verification moment",
};

/** Expect kind chip tones (DESIGN.md chips). */
const KIND_TONE: Record<string, string> = {
  emergency: "chip-bad",
  queue: "chip-neutral",
  identity: "chip-ok",
  verify: "chip-warn",
  locked: "chip-warn",
  repeat_caller: "chip-warn",
  hero: "chip-ai",
  card: "chip-info",
  kb: "chip-info",
  plan: "chip-neutral",
  tone_tip: "chip-warn",
  ask: "chip-ai",
  wrap_up: "chip-ai",
};

function ExpectRow({
  item,
  label,
  checked,
  onToggle,
}: {
  item: ExpectItem;
  label: string;
  checked: boolean;
  onToggle: () => void;
}) {
  const extra = [
    item.ask && `Type: ${item.ask}`,
    item.reason_code && `Reason code ${item.reason_code}`,
    item.refs?.length && `Refs ${item.refs.join(", ")}`,
  ].filter(Boolean);
  return (
    <li className={`talk-expect${checked ? " is-done" : ""}`}>
      <label>
        <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`${label}: ${item.text}`} />
        <span className="plan-box" aria-hidden="true">
          {checked && <Check strokeWidth={2.4} />}
        </span>
        <span className="talk-expect-body">
          <span className={`chip ${KIND_TONE[item.kind] ?? "chip-neutral"}`}>{label}</span>
          <span className="talk-expect-text">{item.text}</span>
          {extra.length > 0 && <span className="talk-expect-extra mono">{extra.join(" · ")}</span>}
        </span>
      </label>
    </li>
  );
}

/**
 * Caller talk track (P5-11, docs/demos.md): the caller's beats in call order with
 * backup lines, the tester's notes for the agent, and what should show on the agent's
 * screen as a checklist the tester ticks. Ticks live in the page only.
 */
export function TalkTrack({ p, kinds, id }: { p: DemoPersona; kinds: Record<string, string>; id: string }) {
  const [done, setDone] = useState<Set<string>>(new Set());
  const total = useMemo(() => p.talk_track.reduce((n, b) => n + b.expect.length, 0), [p.talk_track]);
  const toggle = (key: string) =>
    setDone((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  let n = 0;
  return (
    <section className="talk-track" id={id} aria-label="Caller talk track">
      <div className="talk-rules" role="note" aria-label="Before you start">
        <div className="label">Before you start</div>
        <ul>
          <li>Say names plainly. Never spell them to the bot.</li>
          <li>Press Accept on the hero before End, or the wrap up has no logged action and the reason code falls back.</li>
          <li>The agent is not scripted: the assist is the agent's script.</li>
        </ul>
      </div>
      <div className="talk-head">
        <span className="tile-count mono" data-testid="talk-progress">
          {done.size} of {total} seen
        </span>
        <button type="button" className="btn btn-quiet" onClick={() => setDone(new Set())} disabled={done.size === 0}>
          <RotateCcw strokeWidth={1.7} aria-hidden="true" />
          Reset ticks
        </button>
      </div>
      {PHASES.map((ph) => {
        const beats = p.talk_track.map((b, i) => [b, i] as const).filter(([b]) => b.phase === ph.id);
        if (beats.length === 0) return null;
        return (
          <div className="talk-phase" key={ph.id}>
            <h3 className="label talk-phase-title">{ph.label}</h3>
            <ol className="talk-beats">
              {beats.map(([b, i]) => {
                n += 1;
                return (
                  <li key={i} className={`talk-beat is-${b.to}${b.optional ? " is-optional" : ""}`} data-testid="talk-beat">
                    <span className="talk-num mono" aria-hidden="true">
                      {n}
                    </span>
                    <div className="talk-body">
                      <div className="talk-caller">
                        {b.optional && (
                          <span className="chip chip-ai talk-optional">Optional: {OPTIONAL_LABEL[b.feature ?? ""] ?? "extra"}</span>
                        )}
                        <p className="talk-say">“{b.say}”</p>
                        {b.note && <p className="talk-note">{b.note}</p>}
                        {b.if_asked.length > 0 && (
                          <div className="talk-if">
                            <div className="label">If asked</div>
                            <ul>
                              {b.if_asked.map((x, k) => (
                                <li key={k}>{x}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {b.agent_note && (
                          <p className="talk-agent-note">
                            <span className="label">Agent</span> {b.agent_note}
                          </p>
                        )}
                      </div>
                      {b.expect.length > 0 && (
                        <div className="talk-expects">
                          <div className="label">On the agent's screen</div>
                          <ul>
                            {b.expect.map((e, k) => {
                              const key = `${i}:${k}`;
                              return (
                                <ExpectRow
                                  key={key}
                                  item={e}
                                  label={kinds[e.kind] ?? e.kind}
                                  checked={done.has(key)}
                                  onToggle={() => toggle(key)}
                                />
                              );
                            })}
                          </ul>
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        );
      })}
    </section>
  );
}
