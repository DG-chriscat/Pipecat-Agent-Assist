/** Typed client for the queue REST API and the /ws/queue websocket (docs/queue.md). */

import type {
  AcceptResult,
  Agent,
  AgentState,
  ClientCommand,
  QueueSnapshot,
  ServerMessage,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    // Not JSON (for example a proxy error page); fall through to the status check.
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : res.status === 401
          ? "Your session expired. Reload the page to sign in again."
          : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return data as T;
}

export const signIn = (name: string) => request<Agent>("POST", "/agents/sign-in", { name });

export const setStatusRest = (agentId: string, state: AgentState) =>
  request<Agent>("POST", `/agents/${encodeURIComponent(agentId)}/status`, { state });

export const acceptRest = (agentId: string, sessionId: string) =>
  request<AcceptResult>("POST", `/agents/${encodeURIComponent(agentId)}/accept`, {
    session_id: sessionId,
  });

export const declineRest = (agentId: string, sessionId: string) =>
  request<{ status: string }>("POST", `/agents/${encodeURIComponent(agentId)}/decline`, {
    session_id: sessionId,
  });

export const getQueue = () => request<QueueSnapshot>("GET", "/queue");

export function wsUrlFor(loc: Pick<Location, "protocol" | "host">): string {
  return `${loc.protocol === "https:" ? "wss:" : "ws:"}//${loc.host}/ws/queue`;
}

// ---- websocket ------------------------------------------------------------------

export type SocketStatus = "idle" | "connecting" | "open" | "reconnecting" | "unauthorized" | "closed";

/** Close code the server's auth middleware uses for a missing or expired login. */
export const WS_UNAUTHORIZED = 4401;

export interface QueueSocketOptions {
  url: string;
  WebSocketImpl?: typeof WebSocket;
  /** First retry delay; doubles per failed attempt up to maxDelayMs. */
  minDelayMs?: number;
  maxDelayMs?: number;
  /** 0..1, jitter source. 0.5 means no jitter. */
  random?: () => number;
}

type Listener<T> = (value: T) => void;

/**
 * One websocket to /ws/queue that reconnects with exponential backoff. Every new
 * connection starts with a fresh snapshot from the server, so consumers rebuild their
 * state from it. Stops on close() or when the server closes with 4401 (logged out).
 */
export class QueueSocket {
  private readonly url: string;
  private readonly WS: typeof WebSocket;
  private readonly minDelay: number;
  private readonly maxDelay: number;
  private readonly random: () => number;
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private _status: SocketStatus = "idle";
  private readonly messageListeners = new Set<Listener<ServerMessage>>();
  private readonly statusListeners = new Set<Listener<SocketStatus>>();

  constructor(opts: QueueSocketOptions) {
    this.url = opts.url;
    this.WS = opts.WebSocketImpl ?? WebSocket;
    this.minDelay = opts.minDelayMs ?? 500;
    this.maxDelay = opts.maxDelayMs ?? 10_000;
    this.random = opts.random ?? Math.random;
  }

  get status(): SocketStatus {
    return this._status;
  }

  onMessage(fn: Listener<ServerMessage>): () => void {
    this.messageListeners.add(fn);
    return () => this.messageListeners.delete(fn);
  }

  onStatus(fn: Listener<SocketStatus>): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  connect(): void {
    this.stopped = false;
    if (this.ws) return;
    this.open();
  }

  /** Sends a command if the socket is open. Returns false otherwise. */
  send(cmd: ClientCommand): boolean {
    if (!this.ws || this.ws.readyState !== this.WS.OPEN) return false;
    this.ws.send(JSON.stringify(cmd));
    return true;
  }

  close(): void {
    this.stopped = true;
    this.clearTimer();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      this.detach(ws);
      ws.close();
    }
    this.setStatus("closed");
  }

  private open(): void {
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");
    const ws = new this.WS(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
    };
    ws.onmessage = (ev: MessageEvent) => {
      const msg = parse(ev.data);
      if (msg) for (const fn of [...this.messageListeners]) fn(msg);
    };
    ws.onclose = (ev: CloseEvent) => {
      this.detach(ws);
      if (this.ws === ws) this.ws = null;
      if (this.stopped) return;
      if (ev.code === WS_UNAUTHORIZED) {
        this.stopped = true;
        this.setStatus("unauthorized");
        return;
      }
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      // onclose follows and handles the retry.
    };
  }

  private scheduleReconnect(): void {
    const base = Math.min(this.maxDelay, this.minDelay * 2 ** this.attempt);
    const delay = Math.min(this.maxDelay, Math.round(base * (0.75 + this.random() * 0.5)));
    this.attempt += 1;
    this.setStatus("reconnecting");
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.stopped) this.open();
    }, delay);
  }

  private detach(ws: WebSocket): void {
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private setStatus(s: SocketStatus): void {
    if (s === this._status) return;
    this._status = s;
    for (const fn of [...this.statusListeners]) fn(s);
  }
}

function parse(data: unknown): ServerMessage | null {
  if (typeof data !== "string") return null;
  try {
    const v = JSON.parse(data);
    if (v && typeof v === "object" && !Array.isArray(v) && typeof v.type === "string") {
      return v as ServerMessage;
    }
  } catch {
    // ignore malformed frames
  }
  return null;
}
