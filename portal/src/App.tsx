import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { QueueSocket, signIn, wsUrlFor } from "./api/queueClient";
import type { Agent } from "./api/types";
import { createDailyMedia } from "./call/dailyMedia";
import type { CreateMedia } from "./call/media";
import { Home } from "./components/Home";
import { SignIn } from "./components/SignIn";
import { applyTheme, initialTheme, saveTheme, type Theme } from "./state/theme";

/** Remembered per tab so a reload signs the agent back in (same name, same agent). */
const NAME_KEY = "vitals.agentName";

const defaultSocket = () => new QueueSocket({ url: wsUrlFor(window.location) });

const storage = () => (typeof localStorage === "undefined" ? undefined : localStorage);
const media = () => (typeof window.matchMedia === "function" ? window.matchMedia.bind(window) : undefined);
const themeFor = (name: string | null) => initialTheme(name, storage(), media());

interface Props {
  createSocket?: () => QueueSocket;
  createMedia?: CreateMedia;
}

export default function App({ createSocket = defaultSocket, createMedia = createDailyMedia }: Props) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [busy, setBusy] = useState(() => !!sessionStorage.getItem(NAME_KEY));
  const [error, setError] = useState<string | null>(null);
  // AP-16: per agent, saved; before sign in the last agent of this tab, else the OS.
  const [theme, setTheme] = useState<Theme>(() => themeFor(sessionStorage.getItem(NAME_KEY)));

  useLayoutEffect(() => applyTheme(theme), [theme]);

  const doSignIn = useCallback(async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      const a = await signIn(name);
      sessionStorage.setItem(NAME_KEY, a.name);
      setTheme(themeFor(a.name));
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
    setTheme(themeFor(null));
  }, []);

  const changeTheme = useCallback(
    (t: Theme) => {
      setTheme(t);
      if (agent) saveTheme(agent.name, t, storage());
    },
    [agent],
  );

  if (!agent) return <SignIn onSubmit={doSignIn} busy={busy} error={error} />;
  return (
    <Home
      key={agent.agent_id}
      agent={agent}
      theme={theme}
      onTheme={changeTheme}
      createSocket={createSocket}
      createMedia={createMedia}
      onSignOut={signOut}
    />
  );
}
