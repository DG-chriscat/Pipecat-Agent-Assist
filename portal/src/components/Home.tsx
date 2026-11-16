import { AlertTriangle, CheckCircle2, ClipboardCheck, Headphones, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  acceptRest,
  declineRest,
  getCaller,
  getWrapUp,
  logPhiReveal,
  postIdentityDecision,
  saveWrapUp,
  setStatusRest,
  type QueueSocket,
} from "../api/queueClient";
import { settingsApi } from "../api/settingsClient";
import type { Agent, AgentState, ServerMessage, WrapUpInfo } from "../api/types";
import type { CreateMedia } from "../call/media";
import { useCall } from "../hooks/useCall";
import type { LocalAction } from "../state/assist";
import { useQueueSocket } from "../hooks/useQueueSocket";
import { offerFor, type Offer } from "../state/offer";
import { pageFromPath, pathForPage } from "../state/route";
import type { Theme } from "../state/theme";
import { Architecture } from "./Architecture";
import { Demos } from "./Demos";
import { InCall } from "./InCall";
import { IncomingCall } from "./IncomingCall";
import type { PhiFieldName } from "./PhiField";
import { Settings } from "./Settings";
import { Shell, type Page, type ShellAgent } from "./Shell";
import { StatusToggle } from "./StatusToggle";
import { WrapUp } from "./WrapUp";

interface Props {
  agent: Agent;
  theme: Theme;
  onTheme: (t: Theme) => void;
  createSocket: () => QueueSocket;
  createMedia: CreateMedia;
  onSignOut: () => void;
}

const SESSION_EXPIRED = "Your session expired. Reload the page to sign in again.";
/** States the agent can toggle from by hand (docs/queue.md, QH-1). */
const TOGGLEABLE: AgentState[] = ["offline", "available", "wrap_up"];
const MISSED: Record<"timeout" | "declined", string> = {
  timeout: "You missed a call. You are set to not available until you turn it back on.",
  declined: "You declined the call. You are set to not available until you turn it back on.",
};
const DROPPED =
  "We lost the connection to your call. The caller went back to the front of the queue, and you are set to not available.";
const SAVED = "Call saved. You are available.";
/** Clock tick while ringing or on a call. */
const TICK_MS = 250;
/** P5-1: how often the wrap up view is read again while the summary draft is coming. */
const WRAP_POLL_MS = 1000;

const errorText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

interface WrapState {
  sessionId: string;
  info: WrapUpInfo | null;
  error: string | null;
}

/** Signed in: idle home with status toggle (AP-2), offer card (AP-3), in-call shell (AP-4),
 * wrap up (P3-5). */
export function Home({ agent: signedIn, theme, onTheme, createSocket, createMedia, onSignOut }: Props) {
  const [agentId, setAgentId] = useState(signedIn.agent_id);
  const [pending, setPending] = useState<AgentState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [eventOffer, setEventOffer] = useState<Offer | null>(null);
  /** The offer being accepted or declined: keeps the card up until the reply. */
  const [inFlight, setInFlight] = useState<Offer | null>(null);
  const [offerError, setOfferError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  /** End pressed; waiting for the server to confirm wrap up. */
  const [ending, setEnding] = useState(false);
  const [wrap, setWrap] = useState<WrapState | null>(null);
  const [wrapAttempt, setWrapAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [page, setPageState] = useState<Page>(() => pageFromPath(window.location.pathname));
  /** The rail moves the page and the URL (/agent/architecture, AP-21). */
  const setPage = useCallback((p: Page) => {
    setPageState(p);
    const path = pathForPage(p);
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
  }, []);
  useEffect(() => {
    const onPop = () => setPageState(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const { call, start, prepare, discard, setDetails, leave, toggleMute, toggleHold, assistAction } =
    useCall(createMedia);

  const ids = useRef({ agentId, sessionId: call?.sessionId ?? null });
  useEffect(() => {
    ids.current = { agentId, sessionId: call?.sessionId ?? null };
  }, [agentId, call?.sessionId]);
  /** The last call the server put us on, so a re-queue (QH-9) is recognised after a reload. */
  const lastCallId = useRef<string | null>(null);

  const onMessage = useCallback(
    (msg: ServerMessage) => {
      const me = ids.current;
      if ((msg.type === "agent_updated" || msg.type === "status_set") && msg.agent.agent_id === me.agentId) {
        if (msg.agent.call_id) lastCallId.current = msg.agent.call_id;
      } else if (msg.type === "snapshot") {
        const mine = msg.agents.find((a) => a.agent_id === me.agentId);
        if (mine?.call_id) lastCallId.current = mine.call_id;
      }
      if (msg.type === "signed_in") setAgentId(msg.agent.agent_id);
      else if (msg.type === "status_set") setPending(null);
      else if (msg.type === "error") {
        setPending(null);
        setError(msg.message);
      } else if (msg.type === "offer" && msg.agent_id === me.agentId) {
        setEventOffer({ call: msg.call, expiresAt: msg.expires_at });
        setOfferError(null);
        setNotice(null);
      } else if (msg.type === "offer_missed" && msg.agent_id === me.agentId) {
        setEventOffer(null);
        setNotice(MISSED[msg.reason]);
      } else if (msg.type === "call_ended" && msg.call.session_id === me.sessionId) {
        // Caller hung up (or our own End landed): the wrap up view takes over.
        void leave();
      } else if (
        msg.type === "call_queued" &&
        msg.requeued &&
        (msg.call.session_id === me.sessionId || msg.call.session_id === lastCallId.current)
      ) {
        // QH-9: we dropped off and the caller went back to the queue.
        lastCallId.current = null;
        setNotice(DROPPED);
        void leave();
      }
    },
    [leave],
  );

  const { state, status, send } = useQueueSocket(createSocket, onMessage);
  const agent = state.agents[agentId] ?? (state.synced ? undefined : signedIn);
  const agentState: AgentState = agent?.state ?? "offline";
  const offer = inFlight ?? offerFor(agent, state.queue, eventOffer);

  // The server keeps agents in memory; after a restart the snapshot no longer has us.
  useEffect(() => {
    if (state.synced && !state.agents[agentId]) {
      send({ type: "sign_in", name: signedIn.name });
    }
  }, [state.synced, state.agents, agentId, signedIn.name, send]);

  // The confirmed state reached the target: no longer pending.
  useEffect(() => {
    if (pending && agentState === pending) setPending(null);
  }, [pending, agentState]);

  // A reconnect drops any in-flight command.
  useEffect(() => {
    if (status !== "open") setPending(null);
  }, [status]);

  // Wrap up confirmed (or the call ended some other way): End is done.
  useEffect(() => {
    if (ending && agentState !== "on_call") setEnding(false);
  }, [ending, agentState]);

  // QH-9: the server says we are not on the call any more (we dropped off and it put
  // the caller back in the queue), but this tab still has the room: leave it. Ringing
  // is excluded because accept can return before the on_call update arrives.
  const dropped =
    !!call && status === "open" && state.synced && (agentState === "offline" || agentState === "available");
  useEffect(() => {
    if (!dropped) return;
    setNotice(DROPPED);
    void leave();
  }, [dropped, leave]);

  // Wrap up (P3-5): the server keeps call_id on the agent until Save.
  const wrapSession = agentState === "wrap_up" ? (agent?.call_id ?? null) : null;
  useEffect(() => {
    setSaving(false);
    setSaveError(null);
    if (!wrapSession) {
      setWrap(null);
      return;
    }
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setWrap({ sessionId: wrapSession, info: null, error: null });
    // P5-1: the bot writes the summary draft when the call ends; read the view again
    // while the server says it is still coming.
    const load = () =>
      getWrapUp(wrapSession, agentId).then(
        (info) => {
          if (!current) return;
          setWrap({ sessionId: wrapSession, info, error: null });
          if (info.draft_pending) timer = setTimeout(load, WRAP_POLL_MS);
        },
        (e) =>
          current && setWrap({ sessionId: wrapSession, info: null, error: errorText(e, "Could not load the call.") }),
      );
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [wrapSession, agentId, wrapAttempt]);

  // Tick the clock for the countdown and the call timer.
  const ticking = !!offer || !!call;

  // P3-5: warm the call audio (SDK, mic) while the card rings, so accept only has
  // to connect; release it if the offer goes away without a call.
  const ringingFor = offer && !call ? offer.call.session_id : null;
  useEffect(() => {
    if (!ringingFor) return;
    prepare();
    return () => discard();
  }, [ringingFor, prepare, discard]);
  useEffect(() => {
    if (!ticking) return;
    setNowMs(Date.now());
    const t = setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [ticking]);

  const live = status === "open" && state.synced && !!agent;
  const available = agentState === "available";
  // P5-5: the Settings page (and the Architecture page, P5-6). A ringing, live, or wrap
  // up call always shows Calls, and moves the rail back to Calls so the agent lands there
  // after the call too.
  const busyWithCall = !!call || !!offer || !!wrapSession;
  useEffect(() => {
    if (busyWithCall) setPage("calls");
  }, [busyWithCall, setPage]);
  const onSettings = page === "settings" && !busyWithCall;
  const onArchitecture = page === "architecture" && !busyWithCall;
  const onDemos = page === "demos" && !busyWithCall;

  function toggle() {
    const target: AgentState = available ? "offline" : "available";
    setError(null);
    setNotice(null);
    if (send({ type: "set_status", agent_id: agentId, state: target })) setPending(target);
  }

  function signOut() {
    if (live && agentState !== "offline" && agentState !== "on_call") {
      send({ type: "set_status", agent_id: agentId, state: "offline" });
    }
    void leave();
    onSignOut();
  }

  async function accept() {
    if (!offer || inFlight) return;
    performance.mark?.("vitals:accept");
    setInFlight(offer);
    setOfferError(null);
    try {
      const res = await acceptRest(agentId, offer.call.session_id);
      performance.mark?.("vitals:accepted");
      setEventOffer(null);
      setNotice(null);
      // AP-5: the accept summary fills the panel now; the history call fills the rest.
      start(res, { waitSecs: Math.max(0, Date.now() / 1000 - offer.call.queued_at) });
      getCaller(res.session_id, agentId).then(
        (details) => setDetails(res.session_id, details),
        () => setDetails(res.session_id, null),
      );
    } catch (e) {
      setOfferError(errorText(e, "Could not accept the call. Try again."));
    } finally {
      setInFlight(null);
    }
  }

  async function decline() {
    if (!offer || inFlight) return;
    setInFlight(offer);
    setOfferError(null);
    try {
      await declineRest(agentId, offer.call.session_id);
      setEventOffer(null);
    } catch (e) {
      setOfferError(errorText(e, "Could not decline the call. Try again."));
    } finally {
      setInFlight(null);
    }
  }

  /**
   * End: wrap up first, then leave the room (docs/queue.md: on_call -> wrap_up by hand).
   * In that order the server already knows the call ended when the bot sees us leave,
   * so it is never taken for a dropped agent (QH-9).
   */
  async function endCall() {
    setEnding(true);
    setNotice(null);
    if (!send({ type: "set_status", agent_id: agentId, state: "wrap_up" })) {
      try {
        await setStatusRest(agentId, "wrap_up");
      } catch (e) {
        setEnding(false);
        setError(errorText(e, "Could not set wrap up."));
      }
    }
    await leave();
  }

  async function saveWrap(reasonCode: string, notes: string, summary: string) {
    if (!wrapSession || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveWrapUp(agentId, wrapSession, reasonCode, notes, summary);
      setNotice(SAVED);
      setError(null);
    } catch (e) {
      setSaveError(errorText(e, "Could not save. Try again."));
    } finally {
      setSaving(false);
    }
  }

  /**
   * IN-13: the bot unlocks (or keeps locked) the records; the server stores the decision
   * and, on Mark verified, the caller panel reloads with the member's history.
   */
  function onAssist(a: LocalAction) {
    assistAction(a);
    if (a.type !== "verify" || !call) return;
    const sessionId = call.sessionId;
    postIdentityDecision(sessionId, agentId, a.verified ? "verified" : "not_verified")
      .then(() => getCaller(sessionId, agentId))
      .then(
        (details) => setDetails(sessionId, details),
        () => {
          // The bot already has the decision; the panel keeps what it shows.
        },
      );
  }

  /** AP-17: each reveal is logged on the call record. A failed log never blocks the agent. */
  function revealPhi(field: PhiFieldName) {
    if (!call) return;
    logPhiReveal(call.sessionId, agentId, field).catch(() => {
      // The server logs what it receives; the portal has nothing to retry here.
    });
  }

  const shellAgent: ShellAgent = {
    name: signedIn.name,
    online: live && agentState !== "offline",
    theme,
    onTheme,
    onSignOut: signOut,
    page: onSettings ? "settings" : onArchitecture ? "architecture" : onDemos ? "demos" : "calls",
    onPage: setPage,
  };

  // AP-21: the Architecture page has its own header (view toggle, walk, export, present).
  // Home stays mounted, so the queue socket keeps running and a ring still comes through.
  if (onArchitecture) return <Architecture shell={shellAgent} />;
  // P5-10: the Demos page, same rule (the queue socket keeps running behind it).
  if (onDemos) return <Demos shell={shellAgent} />;

  if (call) {
    return (
      <InCall
        shell={shellAgent}
        onRevealPhi={revealPhi}
        caller={call.caller}
        memberId={call.memberId}
        intake={call.intake}
        waitSecs={call.waitSecs}
        details={call.details}
        detailsState={call.detailsState}
        phase={call.phase}
        muted={call.muted}
        held={call.held}
        remoteLevel={call.remoteLevel}
        elapsedSecs={(nowMs - call.startedAtMs) / 1000}
        error={call.error}
        transcript={call.transcript}
        highlights={call.highlights}
        assist={call.assist}
        onAssist={onAssist}
        onMute={toggleMute}
        onHold={toggleHold}
        onEnd={() => void endCall()}
      />
    );
  }

  const alert = status === "unauthorized" ? SESSION_EXPIRED : error;
  const waiting = state.queue.length;
  // On a call with no local session (reload mid call): the room token is gone, so
  // the only way on is to end it.
  const orphaned = live && agentState === "on_call" && !ending;
  const wrapView = wrapSession && wrap?.sessionId === wrapSession ? wrap : null;

  const queueIsland = (
    <div className="island" aria-label="Queue">
      <span className={`dot${waiting > 0 ? " dot-waiting" : ""}`} aria-hidden="true" />
      <span className="label island-label">In queue</span>
      <span className="island-count mono" data-testid="queue-count">
        {state.synced ? waiting : "–"}
      </span>
    </div>
  );
  // DESIGN.md call island, wrap-up state.
  const wrapIsland = (
    <div className="island call-island is-wrap" role="group" aria-label="Call wrap up">
      <span className="vdot vdot-info" aria-hidden="true" />
      <b className="island-name">{wrapView?.info?.caller_name ?? "Last call"}</b>
      <span className="chip chip-info">
        <ClipboardCheck strokeWidth={1.7} aria-hidden="true" />
        Wrap up
      </span>
      <span className="island-label label">In queue</span>
      <span className="island-count mono" data-testid="queue-count">
        {state.synced ? waiting : "–"}
      </span>
    </div>
  );

  return (
    <Shell
      agent={shellAgent}
      island={wrapSession ? wrapIsland : queueIsland}
      right={
        <>
          <StatusToggle
            state={agentState}
            busy={pending !== null}
            disabled={!live || pending !== null || !TOGGLEABLE.includes(agentState)}
            onToggle={toggle}
          />
          <span className="agent-name">{signedIn.name}</span>
        </>
      }
    >
      <main className={`center-stage${onSettings ? " is-settings" : ""}`}>
        {onSettings ? (
          <Settings api={settingsApi} agentName={signedIn.name} />
        ) : wrapSession ? (
          <WrapUp
            key={wrapSession}
            info={wrapView?.info ?? null}
            loadError={wrapView?.error ?? null}
            saving={saving}
            saveError={saveError}
            onSave={(code, notes, summary) => void saveWrap(code, notes, summary)}
            onRetry={() => setWrapAttempt((n) => n + 1)}
          />
        ) : offer ? (
          <IncomingCall
            offer={offer}
            windowSecs={state.offerTimeoutSecs}
            nowMs={nowMs}
            busy={inFlight !== null}
            error={offerError}
            onAccept={() => void accept()}
            onDecline={() => void decline()}
          />
        ) : (
          <section className="panel card empty-state" aria-live="polite">
            <div className={`orb${available ? "" : " orb-rest"}`} aria-hidden="true">
              <Headphones strokeWidth={1.7} />
            </div>
            <h1>Waiting for the next call</h1>
            <p className="sub">
              {available
                ? "You are available. Calls ring here, and you have 20 seconds to answer."
                : "Set yourself available when you are ready to take calls."}
            </p>
            <dl className="stats">
              <div>
                <dt className="label">Callers waiting</dt>
                <dd className="mono">{state.synced ? waiting : "–"}</dd>
              </div>
              <div>
                <dt className="label">Agents available</dt>
                <dd className="mono">
                  {state.synced ? Object.values(state.agents).filter((a) => a.state === "available").length : "–"}
                </dd>
              </div>
            </dl>
            {notice && (
              <p className={`notice ${notice === SAVED ? "is-ok" : "is-warn"}`} role="status">
                {notice === SAVED ? (
                  <CheckCircle2 strokeWidth={1.7} aria-hidden="true" />
                ) : (
                  <AlertTriangle strokeWidth={1.7} aria-hidden="true" />
                )}
                <span>{notice}</span>
              </p>
            )}
            {orphaned && (
              <div className="orphaned">
                <p className="sub">This tab lost the call audio. End the call to move to wrap up.</p>
                <button type="button" className="end-btn" onClick={() => void endCall()}>
                  End call
                </button>
              </div>
            )}
            {status === "reconnecting" && (
              <p className="conn" role="status">
                <WifiOff strokeWidth={1.7} aria-hidden="true" />
                Reconnecting to the queue
              </p>
            )}
            {alert && (
              <p className="error" role="alert">
                {alert}
              </p>
            )}
          </section>
        )}
      </main>
    </Shell>
  );
}
