import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";
import { TopBar } from "./TopBar";

interface Props {
  onSubmit: (name: string) => void;
  busy: boolean;
  error: string | null;
}

/** AP-1 demo sign in: a display name is the identity. Same name, same agent. */
export function SignIn({ onSubmit, busy, error }: Props) {
  const [name, setName] = useState("");
  const trimmed = name.trim();

  function submit(e: FormEvent) {
    e.preventDefault();
    if (trimmed && !busy) onSubmit(trimmed);
  }

  return (
    <div className="page">
      <TopBar />
      <main className="center-stage">
        <form className="glass panel signin" onSubmit={submit} noValidate>
          <h1>Sign in</h1>
          <p className="sub">Enter your name to start taking calls.</p>
          <label className="label" htmlFor="agent-name">
            Your name
          </label>
          <input
            id="agent-name"
            className="field"
            name="name"
            autoComplete="name"
            maxLength={100}
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="btn btn-primary btn-block" type="submit" disabled={!trimmed || busy}>
            {busy ? "Signing in" : "Sign in"}
            <ArrowRight strokeWidth={1.8} aria-hidden="true" />
          </button>
          <p className="foot">Demo sign in. Use the same name to pick up where you left off.</p>
        </form>
      </main>
    </div>
  );
}
