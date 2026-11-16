import { Check, History, List, LogOut, Moon, Network, Phone, Play, Settings, Sun } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import dTile from "../assets/d-tile.png";
import wordmark from "../assets/wordmark.png";
import type { Theme } from "../state/theme";
import { initials } from "../state/names";
import type { Page } from "../state/route";
import { PhiMaskedChip } from "./PhiMaskedChip";

export type { Page };

export interface ShellAgent {
  name: string;
  /** Online (any state but offline): the avatar shows the phosphor available dot. */
  online: boolean;
  theme: Theme;
  onTheme: (t: Theme) => void;
  onSignOut: () => void;
  /** The rail's current page (P5-5) and how to change it. No onPage: Settings is hidden. */
  page?: Page;
  onPage?: (p: Page) => void;
}

function AgentMenu({ agent }: { agent: ShellAgent }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // The menu owns Esc while open: it must not also decline a ringing call.
      e.stopImmediatePropagation();
      e.preventDefault();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const option = (t: Theme, label: string, icon: ReactNode) => (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={agent.theme === t}
      className="menu-item"
      onClick={() => agent.onTheme(t)}
    >
      {icon}
      {label}
      {agent.theme === t && <Check className="menu-check" strokeWidth={1.7} aria-hidden="true" />}
    </button>
  );

  return (
    <div className="me-box" ref={box}>
      <button
        type="button"
        className={`me${agent.online ? " is-online" : ""}`}
        aria-label="Agent menu"
        aria-haspopup="menu"
        aria-expanded={open}
        title={agent.name}
        onClick={() => setOpen((o) => !o)}
      >
        {initials(agent.name)}
      </button>
      {open && (
        <div className="panel menu" role="menu" aria-label="Agent">
          <div className="menu-head">{agent.name}</div>
          <div className="label menu-label">Theme</div>
          {option("light", "Light", <Sun strokeWidth={1.7} aria-hidden="true" />)}
          {option("dark", "Dark", <Moon strokeWidth={1.7} aria-hidden="true" />)}
          <div className="menu-sep" />
          <button type="button" role="menuitem" className="menu-item" onClick={agent.onSignOut}>
            <LogOut strokeWidth={1.7} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/** Left rail (DESIGN.md "Left rail"): D tile, Calls, Queue, History, Demos (P5-10),
 * Architecture (P5-6),
 * Settings (P5-5), agent avatar. */
function Rail({ agent }: { agent?: ShellAgent }) {
  const page = agent?.page ?? "calls";
  const item = (p: Page, label: string, icon: ReactNode) => (
    <button
      type="button"
      className={`ri${page === p ? " is-on" : ""}`}
      aria-label={label}
      aria-current={page === p ? "page" : undefined}
      title={label}
      onClick={() => agent?.onPage?.(p)}
    >
      {icon}
    </button>
  );
  return (
    <nav className="rail" aria-label="Main">
      <img className="rail-tile" src={dTile} alt="" width={36} height={36} />
      {agent && (
        <>
          {item("calls", "Calls", <Phone strokeWidth={1.7} aria-hidden="true" />)}
          <button type="button" className="ri" aria-label="Queue" title="Queue (coming later)" disabled>
            <List strokeWidth={1.7} aria-hidden="true" />
          </button>
          <button type="button" className="ri" aria-label="History" title="History (coming later)" disabled>
            <History strokeWidth={1.7} aria-hidden="true" />
          </button>
          {agent.onPage && item("demos", "Demos", <Play strokeWidth={1.7} aria-hidden="true" />)}
          {agent.onPage && item("architecture", "Architecture", <Network strokeWidth={1.7} aria-hidden="true" />)}
          {agent.onPage && item("settings", "Settings", <Settings strokeWidth={1.7} aria-hidden="true" />)}
          <AgentMenu agent={agent} />
        </>
      )}
    </nav>
  );
}

interface ShellProps {
  agent?: ShellAgent;
  /** Centered call island. */
  island?: ReactNode;
  right?: ReactNode;
  /** Where the aurora orb sits: behind the hero (in call) or the center card. */
  orb?: "hero" | "center";
  className?: string;
  /** Present mode (AP-21): no rail and no header, the stage fills the screen. */
  bare?: boolean;
  children: ReactNode;
}

/**
 * Studio frame (DESIGN.md "Layout"): material backdrop, left rail, header with the
 * wordmark, "Vitals", the PHI masked chip, the centered call island, and controls.
 */
export function Shell({ agent, island, right, orb = "center", className, bare, children }: ShellProps) {
  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <div className="backdrop-horizon" aria-hidden="true" />
      <div className={`backdrop-orb orb-${orb}`} aria-hidden="true" />
      <div className={`app${bare ? " is-bare" : ""}`}>
        {!bare && <Rail agent={agent} />}
        <div className={`stage${className ? ` ${className}` : ""}`}>
          {!bare && (
            <header className="head">
              <div className="brand">
                <img className="brand-wordmark" src={wordmark} alt="Deepgram" height={16} />
                <span className="brand-sep" aria-hidden="true" />
                <span className="brand-product">Vitals</span>
                <PhiMaskedChip />
              </div>
              <div className="head-center">{island}</div>
              <div className="head-right">{right}</div>
            </header>
          )}
          {children}
        </div>
      </div>
    </>
  );
}
