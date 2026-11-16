import { useCallback, useEffect, useState } from "react";
import { QueueSocket, signIn, wsUrlFor } from "./api/queueClient";
import type { Agent } from "./api/types";
import { createDailyMedia } from "./call/dailyMedia";
import type { CreateMedia } from "./call/media";
import { Home } from "./components/Home";
import { SignIn } from "./components/SignIn";

/** Remembered per tab so a reload signs the agent back in (same name, same agent). */
const NAME_KEY = "vitals.agentName";

const defaultSocket = () => new QueueSocket({ url: wsUrlFor(window.location) });

interface Props {
  createSocket?: () => QueueSocket;
  createMedia?: CreateMedia;
}

export default function App({ createSocket = defaultSocket, createMedia = createDailyMedia }: Props) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [busy, setBusy] = useState(() => !!sessionStorage.getItem(NAME_KEY));
  const [error, setError] = useState<string | null>(null);

  const doSignIn = useCallback(async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      const a = await signIn(name);
      sessionStorage.setItem(NAME_KEY, a.name);
      setAgent(a);
    } catch (e) {
      sessionStorage.removeItem(NAME_KEY);
      setError(e instanceof Error ? e.message : "Sign in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const saved = sessionStorage.getItem(NAME_KEY);
    if (saved) void doSignIn(saved);
  }, [doSignIn]);

  const signOut = useCallback(() => {
    sessionStorage.removeItem(NAME_KEY);
    setAgent(null);
  }, []);

  if (!agent) return <SignIn onSubmit={doSignIn} busy={busy} error={error} />;
  return <Home key={agent.agent_id} agent={agent} createSocket={createSocket} createMedia={createMedia} onSignOut={signOut} />;
}
