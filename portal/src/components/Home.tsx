import { Headphones, LogOut, WifiOff } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { QueueSocket } from "../api/queueClient";
import type { Agent, AgentState, ServerMessage } from "../api/types";
import { useQueueSocket } from "../hooks/useQueueSocket";
import { StatusToggle } from "./StatusToggle";
import { TopBar } from "./TopBar";

interface Props {
  agent: Agent;
  createSocket: () => QueueSocket;
  onSignOut: () => void;
}

const SESSION_EXPIRED = "Your session expired. Reload the page to sign in again.";
/** States the agent can toggle from by hand (docs/queue.md, QH-1). */
const TOGGLEABLE: AgentState[] = ["offline", "available", "wrap_up"];

/** Idle home: top bar with status toggle, live queue count, calm empty state. */
export function Home({ agent: signedIn, createSocket, onSignOut }: Props) {
  const [agentId, setAgentId] = useState(signedIn.agent_id);
  const [pending, setPending] = useState<AgentState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onMessage = useCallback((msg: ServerMessage) => {
    if (msg.type === "signed_in") setAgentId(msg.agent.agent_id);
    else if (msg.type === "status_set") setPending(null);
    else if (msg.type === "error") {
      setPending(null);
      setError(msg.message);
    }
  }, []);

  const { state, status, send } = useQueueSocket(createSocket, onMessage);
  const agent = state.agents[agentId] ?? (state.synced ? undefined : signedIn);
  const agentState: AgentState = agent?.state ?? "offline";

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

  const live = status === "open" && state.synced && !!agent;
  const available = agentState === "available";

  function toggle() {
    const target: AgentState = available ? "offline" : "available";
    setError(null);
    if (send({ type: "set_status", agent_id: agentId, state: target })) setPending(target);
  }

  function signOut() {
    if (live && agentState !== "offline" && agentState !== "on_call") {
      send({ type: "set_status", agent_id: agentId, state: "offline" });
    }
    onSignOut();
  }

  const alert = status === "unauthorized" ? SESSION_EXPIRED : error;
  const waiting = state.queue.length;

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
      </main>
    </div>
  );
}
