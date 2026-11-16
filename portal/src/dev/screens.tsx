/**
 * Screen gallery (P5-3, dev only): every portal screen in a fixed, scripted state so
 * the visual polish pass can screenshot them in both themes without a live call.
 * Served by `vite` at /agent/screens.html?screen=<name>&theme=<light|dark>; it is not
 * part of the production build (vite builds index.html only). Mock data only.
 */
/* eslint-disable react-refresh/only-export-components -- an entry point, not a module */
import "@fontsource/work-sans/300.css";
import "@fontsource/work-sans/400.css";
import "@fontsource/work-sans/500.css";
import "@fontsource/work-sans/600.css";
import "@fontsource/azeret-mono/400.css";
import "@fontsource/azeret-mono/500.css";
import "../styles/tokens.css";
import "../styles/app.css";
import { StrictMode, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueueSocket } from "../api/queueClient";
import type { Agent, Call, CallerDetails, ServerMessage, WrapUpInfo } from "../api/types";
import type { CallMedia } from "../call/media";
import { Architecture } from "../components/Architecture";
import { Home } from "../components/Home";
import { InCall, type InCallProps } from "../components/InCall";
import { SignIn } from "../components/SignIn";
import { applyAssist, emptyAssist, type AssistMessage, type AssistState } from "../state/assist";
import { callerInfo } from "../state/offer";
import { applyTheme, type Theme } from "../state/theme";
import type { TranscriptLine } from "../state/transcript";
import {
  askCard,
  heroCard,
  identityMsg,
  lockedCard,
  moodMsg,
  msg,
  PLAN_STEPS,
  planMsg,
  recordCard,
  toneTipCard,
  verifyCard,
} from "../test/assistFixtures";

const params = new URLSearchParams(window.location.search);
const screen = params.get("screen") ?? "index";
const theme: Theme = params.get("theme") === "dark" ? "dark" : "light";
applyTheme(theme);

const NOW = Date.now() / 1000;

const ana: Agent = {
  agent_id: "agent-1",
  name: "Agent Ana",
  state: "available",
  idle_since: NOW - 60,
  offer_call_id: null,
  offer_expires_at: null,
  call_id: null,
};

const summary = {
  full_name: "Maria Lopez",
  dob: "1984-03-12",
  member_id: "418302",
  claim_number: "70412",
  reason: "Caller asking why her MRI claim was denied",
  summary: "MRI claim was denied. She expected it to be covered and has the referral.",
  identity_checks: ["full_name", "dob", "member_id"],
};

const call: Call = {
  session_id: "s1",
  room_url: "https://example.daily.co/r1",
  room_name: "r1",
  summary,
  outcome: "completed",
  identity_result: "verified",
  member_id: "418302",
  flags: [],
  status: "queued",
  queued_at: NOW - 7,
  offered_to: "agent-1",
  agent_id: null,
  answered_at: null,
  ended_at: null,
};

const details: CallerDetails = {
  session_id: "s1",
  identity_result: "verified",
  not_verified: false,
  outcome: "completed",
  flags: [],
  intake: summary,
  wait_secs: 102,
  member: {
    member_id: "418302",
    name: "Maria Lopez",
    first_name: "Maria",
    last_name: "Lopez",
    dob: "1984-03-12",
    plan_id: "PLN-HMO-SILVER",
    plan_name: "Evergreen HMO Silver",
    plan_type: "HMO",
  },
  calls_30d: 2,
  repeat_caller: true,
  recent_events: [
    {
      kind: "call",
      ref: "INT-000001",
      occurred_on: "2026-11-09",
      status: "escalated to claims review",
      summary: "called claims, 9 min, escalated to claims review",
    },
    {
      kind: "claim",
      ref: "70412",
      occurred_on: "2026-10-30",
      status: "denied",
      summary: "MRI, knee, left claim denied, CO-197",
    },
  ],
};

const wrapInfo: WrapUpInfo = {
  session_id: "s1",
  status: "ended",
  ended_by: "agent",
  caller_name: "Maria Lopez",
  reason: "Caller asking why her MRI claim was denied",
  identity_result: "verified",
  duration_secs: 252,
  summary_draft: null,
  suggested_reason_code: null,
  draft_pending: false,
  pack_id: "claims_status",
  reason_codes: [
    { code: "claim_status_given", label: "Claim status given" },
    { code: "denial_explained", label: "Denial explained" },
    { code: "appeal_started", label: "Appeal or retro review started" },
    { code: "callback_needed", label: "Callback needed" },
    { code: "transferred", label: "Transferred" },
    { code: "other", label: "Other" },
  ],
  wrap_up: null,
};

// ---- fakes -------------------------------------------------------------------------

/** A websocket that opens at once and replays a script of server frames. */
function scriptedSocket(frames: ServerMessage[], opts: { stayClosed?: boolean } = {}) {
  class ScriptedWS {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;
    readyState = 0;
    onopen: ((ev: Event) => void) | null = null;
    onmessage: ((ev: MessageEvent) => void) | null = null;
    onclose: ((ev: CloseEvent) => void) | null = null;
    onerror: ((ev: Event) => void) | null = null;
    constructor() {
      setTimeout(() => {
        if (opts.stayClosed) {
          this.readyState = 3;
          this.onclose?.({ code: 1006 } as CloseEvent);
          return;
        }
        this.readyState = 1;
        this.onopen?.(new Event("open"));
        for (const f of frames) this.onmessage?.({ data: JSON.stringify(f) } as MessageEvent);
      }, 0);
    }
    send(): void {}
    close(): void {
      this.readyState = 3;
    }
  }
  return () =>
    new QueueSocket({
      url: "ws://gallery/ws/queue",
      WebSocketImpl: ScriptedWS as unknown as typeof WebSocket,
      minDelayMs: 60_000,
      maxDelayMs: 60_000,
    });
}

const fakeMedia = (): CallMedia => ({
  prepare: async () => {},
  join: () => new Promise(() => {}),
  setMic: () => {},
  sendClientMessage: () => {},
  leave: async () => {},
});

/** Wrap up GET: answers with the fixture, or never (loading), or a 503 (error). */
function stubFetch(mode: "ok" | "loading" | "error") {
  window.fetch = (async () => {
    if (mode === "loading") return new Promise<Response>(() => {});
    if (mode === "error") {
      return new Response(JSON.stringify({ detail: "Could not load the call. Try again." }), { status: 503 });
    }
    return new Response(JSON.stringify(wrapInfo), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

const snapshot = (agents: Agent[], queue: Call[] = []): ServerMessage => ({
  type: "snapshot",
  queue,
  active: [],
  agents,
  offer_timeout_secs: 20,
});

function HomeWith({ frames, stayClosed }: { frames: ServerMessage[]; stayClosed?: boolean }) {
  const [t, setT] = useState<Theme>(theme);
  const [createSocket] = useState(() => scriptedSocket(frames, { stayClosed }));
  return (
    <Home
      agent={ana}
      theme={t}
      onTheme={(x) => {
        applyTheme(x);
        setT(x);
      }}
      createSocket={createSocket}
      createMedia={fakeMedia}
      onSignOut={() => {}}
    />
  );
}

// ---- in call -----------------------------------------------------------------------

const LINES: TranscriptLine[] = [
  { id: "caller-1", role: "caller", text: "Hi, I already called last week about my MRI claim.", final: true, ts: 1 },
  { id: "agent-1", role: "agent", text: "Thanks for holding, Maria. I can see that claim right here.", final: true, ts: 2 },
  { id: "caller-3", role: "caller", text: "So why did my MRI claim get denied?", final: true, ts: 3 },
  { id: "agent-2", role: "agent", text: "It looks like the prior authorization was not on file.", final: true, ts: 4 },
  { id: "caller-4", role: "caller", text: "Nobody told me that. I have the referral", final: false, ts: 5 },
];

function assistFrom(messages: Record<string, unknown>[]): AssistState {
  let s = emptyAssist();
  for (const m of messages) s = applyAssist(s, m as unknown as AssistMessage, 42);
  return s;
}

const doneSteps = PLAN_STEPS.map((s, i) => (i === 0 ? { ...s, done: true, by: "transcript" as const } : s));

function inCallProps(over: Partial<InCallProps>): InCallProps {
  const [t, setT] = [theme, (x: Theme) => applyTheme(x)];
  return {
    shell: { name: "Agent Ana", online: true, theme: t, onTheme: setT, onSignOut: () => {} },
    caller: callerInfo({ summary, identity_result: "verified" }),
    memberId: "418302",
    intake: summary,
    waitSecs: 102,
    details,
    detailsState: "ready",
    phase: "live",
    muted: false,
    held: false,
    remoteLevel: 0,
    elapsedSecs: 252,
    error: null,
    transcript: LINES,
    highlights: [],
    assist: emptyAssist(),
    onAssist: () => {},
    onRevealPhi: () => {},
    onMute: () => {},
    onHold: () => {},
    onEnd: () => {},
    ...over,
  };
}

const suggestion = () =>
  assistFrom([
    planMsg(doneSteps),
    msg("hero", { card: heroCard(), reason: "new", replaces: null }),
    msg("card_added", { card: recordCard() }),
    msg("card_added", { card: toneTipCard() }),
    moodMsg([1, 2, 3, 3, 2], "Worried, easing"),
  ]);

const SCREENS: Record<string, () => ReactNode> = {
  "sign-in": () => <SignIn onSubmit={() => {}} busy={false} error={null} />,
  "sign-in-error": () => <SignIn onSubmit={() => {}} busy={false} error="Sign in failed. Try again." />,
  home: () => <HomeWith frames={[snapshot([ana, { ...ana, agent_id: "agent-2", name: "Ben" }])]} />,
  "home-offline": () => <HomeWith frames={[snapshot([{ ...ana, state: "offline" }])]} />,
  "home-reconnecting": () => <HomeWith frames={[]} stayClosed />,
  "agent-drop": () => (
    <HomeWith
      frames={[
        snapshot([{ ...ana, state: "offline", call_id: "s1" }]),
        { type: "call_queued", call, position: 1, requeued: true },
      ]}
    />
  ),
  incoming: () => (
    <HomeWith
      frames={[
        snapshot([{ ...ana, state: "ringing", offer_call_id: "s1", offer_expires_at: NOW + 19.5 }], [call]),
      ]}
    />
  ),
  "in-call-listening": () => (
    <InCall
      {...inCallProps({
        transcript: LINES.slice(0, 2),
        assist: assistFrom([planMsg(PLAN_STEPS), moodMsg([1], "Calm", "ok")]),
      })}
    />
  ),
  "in-call-empty": () => (
    <InCall
      {...inCallProps({
        transcript: [],
        details: null,
        detailsState: "loading",
        phase: "connecting",
        elapsedSecs: 2,
      })}
    />
  ),
  "in-call-suggestion": () => <InCall {...inCallProps({ assist: suggestion(), remoteLevel: 0.2 })} />,
  "in-call-accepted": () => {
    const s = suggestion();
    const accepted: AssistState = {
      ...s,
      hero: null,
      ack: { card: heroCard(), outcome: "accepted" },
      history: [
        ...s.history,
        { key: "action:x", label: "Request retro auth review", detail: "Simulated · 70412", atSecs: 190 },
      ],
      gone: [heroCard().id],
    };
    return <InCall {...inCallProps({ assist: accepted })} />;
  },
  "in-call-llm-error": () => (
    <InCall
      {...inCallProps({
        assist: assistFrom([planMsg(doneSteps), msg("assist_status", { state: "error", message: "assist model unavailable" })]),
      })}
    />
  ),
  "in-call-slow-connector": () => (
    <InCall
      {...inCallProps({
        assist: {
          ...assistFrom([planMsg(doneSteps)]),
          asks: [{ askId: "ask-1", text: "Show the last 3 claims", status: "pending", cardId: null, message: null }],
        },
      })}
    />
  ),
  "in-call-verify": () => (
    <InCall
      {...inCallProps({
        caller: { ...callerInfo({ summary, identity_result: "close_match" }), name: "Linda Yuen" },
        memberId: "741526",
        intake: { ...summary, full_name: "Linda Yuen", identity_checks: ["dob", "member_id"] },
        details: { ...details, identity_result: "close_match", not_verified: true, member: null, calls_30d: null, repeat_caller: false, recent_events: [] },
        transcript: LINES.slice(0, 1),
        assist: assistFrom([
          planMsg(PLAN_STEPS),
          identityMsg("pending"),
          msg("hero", { card: verifyCard(), reason: "new", replaces: null }),
          msg("card_added", { card: lockedCard() }),
          moodMsg([1, 2], "Calm", "ok"),
        ]),
      })}
    />
  ),
  "in-call-verified": () => (
    <InCall
      {...inCallProps({
        caller: { ...callerInfo({ summary, identity_result: "close_match" }), name: "Linda Yuen" },
        memberId: "741526",
        intake: { ...summary, full_name: "Linda Yuen", identity_checks: ["dob", "member_id"] },
        details: {
          ...details,
          identity_result: "close_match",
          identity_decision: { decision: "verified", agent_id: "agent-1", agent_name: "Agent Ana", at: NOW },
          member: { ...details.member!, member_id: "741526", name: "Linda Nguyen", first_name: "Linda", last_name: "Nguyen", dob: "1958-04-22" },
        },
        transcript: LINES.slice(0, 1),
        assist: {
          ...assistFrom([
            planMsg(PLAN_STEPS),
            identityMsg("verified", { by: "Agent Ana", at: NOW }),
            msg("card_added", { card: recordCard() }),
          ]),
          ack: { card: verifyCard(), outcome: "verified" },
          history: [{ key: "identity", label: "Caller verified", detail: "Verified by you · close match", atSecs: 40 }],
        },
      })}
    />
  ),
  "in-call-ask": () => (
    <InCall
      {...inCallProps({
        assist: assistFrom([planMsg(doneSteps), msg("card_added", { card: askCard() })]),
      })}
    />
  ),
  "in-call-audio-error": () => (
    <InCall
      {...inCallProps({
        error: "Could not connect the call audio. Check your microphone, or end the call.",
        phase: "connecting",
        transcript: [],
        detailsState: "error",
        details: null,
      })}
    />
  ),
  "wrap-up": () => {
    stubFetch("ok");
    return <HomeWith frames={[snapshot([{ ...ana, state: "wrap_up", call_id: "s1" }])]} />;
  },
  "wrap-up-loading": () => {
    stubFetch("loading");
    return <HomeWith frames={[snapshot([{ ...ana, state: "wrap_up", call_id: "s1" }])]} />;
  },
  "wrap-up-error": () => {
    stubFetch("error");
    return <HomeWith frames={[snapshot([{ ...ana, state: "wrap_up", call_id: "s1" }])]} />;
  },
  // P5-6 (AP-21): the shooter clicks through the views, walk, sections, and present.
  architecture: () => (
    <Architecture
      shell={{ name: ana.name, online: true, theme, onTheme: () => {}, onSignOut: () => {}, page: "architecture", onPage: () => {} }}
    />
  ),
};

function Index() {
  return (
    <ul style={{ padding: 40, position: "relative", zIndex: 1 }}>
      {Object.keys(SCREENS).map((k) => (
        <li key={k}>
          <a href={`?screen=${k}&theme=light`}>{k}</a> · <a href={`?screen=${k}&theme=dark`}>dark</a>
        </li>
      ))}
    </ul>
  );
}

const render = SCREENS[screen] ?? Index;
createRoot(document.getElementById("root")!).render(<StrictMode>{render()}</StrictMode>);
