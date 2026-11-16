import { Headphones, LogOut, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { acceptRest, declineRest, setStatusRest, type QueueSocket } from "../api/queueClient";
import type { Agent, AgentState, ServerMessage } from "../api/types";
import type { CreateMedia } from "../call/media";
import { useCall } from "../hooks/useCall";
import { useQueueSocket } from "../hooks/useQueueSocket";
import { offerFor, type Offer } from "../state/offer";
import { InCall } from "./InCall";
import { IncomingCall } from "./IncomingCall";
import { StatusToggle } from "./StatusToggle";
import { TopBar } from "./TopBar";

interface Props {
  agent: Agent;
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
const CALLER_LEFT = "The caller hung up. You are in wrap up.";
/** Clock tick while ringing or on a call. */
const TICK_MS = 250;

const errorText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

/** Signed in: idle home with status toggle (AP-2), offer card (AP-3), in-call shell (AP-4). */
export function Home({ agent: signedIn, createSocket, createMedia, onSignOut }: Props) {
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
  const { call, start, leave, toggleMute, toggleHold } = useCall(createMedia);

  const ids = useRef({ agentId, sessionId: call?.sessionId ?? null });
  useEffect(() => {
    ids.current = { agentId, sessionId: call?.sessionId ?? null };
  }, [agentId, call?.sessionId]);

  const onMessage = useCallback(
    (msg: ServerMessage) => {
      const me = ids.current;
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
        setNotice(CALLER_LEFT);
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

  // Tick the clock for the countdown and the call timer.
  const ticking = !!offer || !!call;
  useEffect(() => {
    if (!ticking) return;
    setNowMs(Date.now());
    const t = setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [ticking]);

  const live = status === "open" && state.synced && !!agent;
  const available = agentState === "available";

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
    setInFlight(offer);
    setOfferError(null);
    try {
      const res = await acceptRest(agentId, offer.call.session_id);
      setEventOffer(null);
      setNotice(null);
      start(res);
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

  /** End: leave the room, then wrap up (docs/queue.md: on_call -> wrap_up by hand). */
  async function endCall() {
    setEnding(true);
    await leave();
    setNotice(null);
    if (!send({ type: "set_status", agent_id: agentId, state: "wrap_up" })) {
      try {
        await setStatusRest(agentId, "wrap_up");
      } catch (e) {
        setEnding(false);
        setError(errorText(e, "Could not set wrap up."));
      }
    }
  }

  if (call) {
    return (
      <InCall
        caller={call.caller}
        memberId={call.memberId}
        phase={call.phase}
        muted={call.muted}
        held={call.held}
        remoteLevel={call.remoteLevel}
        elapsedSecs={(nowMs - call.startedAtMs) / 1000}
        error={call.error}
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

  return (
    <div className="page">
      <TopBar
        center={
          <div className="island" aria-label="Queue">
            <span className={`dot${waiting > 0 ? " dot-waiting" : ""}`} aria-hidden="true" />
            <span className="label island-label">In queue</span>
            <span className="island-count mono" data-testid="queue-count">
              {state.synced ? waiting : "–"}
            </span>
          </div>
        }
        right={
          <>
            <StatusToggle
              state={agentState}
              busy={pending !== null}
              disabled={!live || pending !== null || !TOGGLEABLE.includes(agentState)}
              onToggle={toggle}
            />
            <span className="agent-name">{signedIn.name}</span>
            <button type="button" className="round-btn" aria-label="Sign out" title="Sign out" onClick={signOut}>
              <LogOut strokeWidth={1.8} aria-hidden="true" />
            </button>
          </>
        }
      />
      <main className="center-stage">
        {offer ? (
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
          <section className="glass panel empty-state" aria-live="polite">
            <div className={`orb${available ? "" : " orb-rest"}`} aria-hidden="true">
              <Headphones strokeWidth={1.8} />
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
              <p className="notice" role="status">
                {notice}
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
                <WifiOff strokeWidth={1.8} aria-hidden="true" />
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
    </div>
  );
}
