import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueueSocket, signIn, setStatusRest, getQueue, ApiError, wsUrlFor } from "./queueClient";
import { MockWebSocket } from "../test/mockSocket";
import type { ServerMessage } from "./types";

const snapshot: ServerMessage = {
  type: "snapshot",
  queue: [],
  active: [],
  agents: [],
  offer_timeout_secs: 20,
};

describe("wsUrlFor", () => {
  it("maps http to ws and https to wss", () => {
    expect(wsUrlFor({ protocol: "http:", host: "localhost:8000" })).toBe("ws://localhost:8000/ws/queue");
    expect(wsUrlFor({ protocol: "https:", host: "x.fly.dev" })).toBe("wss://x.fly.dev/ws/queue");
  });
});

describe("REST", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("signIn posts the name and returns the agent", async () => {
    const agent = { agent_id: "agent-1", name: "Ana", state: "offline" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(agent), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(signIn("Ana")).resolves.toMatchObject(agent);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/agents/sign-in");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ name: "Ana" });
    expect(init.credentials).toBe("same-origin");
  });

  it("raises ApiError with the server detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "cannot go from on_call to offline" }), { status: 409 }),
      ),
    );
    const err = await setStatusRest("agent-1", "offline").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("cannot go from on_call to offline");
  });

  it("getQueue reads the snapshot", async () => {
    const body = { queue: [], active: [], agents: [], offer_timeout_secs: 20 };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
    await expect(getQueue()).resolves.toEqual(body);
  });
});

describe("QueueSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.reset();
  });
  afterEach(() => vi.useRealTimers());

  function make(opts: Partial<ConstructorParameters<typeof QueueSocket>[0]> = {}) {
    return new QueueSocket({
      url: "ws://test/ws/queue",
      WebSocketImpl: MockWebSocket as unknown as typeof WebSocket,
      minDelayMs: 500,
      maxDelayMs: 4000,
      random: () => 0.5,
      ...opts,
    });
  }

  it("connects, reports open, and delivers parsed messages", () => {
    const qs = make();
    const statuses: string[] = [];
    const messages: ServerMessage[] = [];
    qs.onStatus((s) => statuses.push(s));
    qs.onMessage((m) => messages.push(m));
    qs.connect();
    const sock = MockWebSocket.last();
    expect(sock.url).toBe("ws://test/ws/queue");
    sock.serverOpen();
    sock.serverSend(snapshot);
    expect(statuses).toEqual(["connecting", "open"]);
    expect(messages).toEqual([snapshot]);
  });

  it("sends JSON commands when open and refuses when not", () => {
    const qs = make();
    qs.connect();
    expect(qs.send({ type: "sign_in", name: "Ana" })).toBe(false);
    MockWebSocket.last().serverOpen();
    expect(qs.send({ type: "set_status", agent_id: "a1", state: "available" })).toBe(true);
    expect(MockWebSocket.last().sent.map((s) => JSON.parse(s))).toEqual([
      { type: "set_status", agent_id: "a1", state: "available" },
    ]);
  });

  it("reconnects with exponential backoff after the socket drops", () => {
    const qs = make();
    const statuses: string[] = [];
    qs.onStatus((s) => statuses.push(s));
    qs.connect();
    MockWebSocket.last().serverClose(1006);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("reconnecting");

    vi.advanceTimersByTime(499);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    MockWebSocket.last().serverClose(1006);
    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);

    // Caps at maxDelayMs.
    for (let i = 0; i < 5; i++) {
      MockWebSocket.last().serverClose(1006);
      vi.advanceTimersByTime(4000);
    }
    expect(MockWebSocket.instances).toHaveLength(8);
  });

  it("resets the backoff once a connection opens", () => {
    const qs = make();
    qs.connect();
    MockWebSocket.last().serverClose(1006);
    vi.advanceTimersByTime(500);
    MockWebSocket.last().serverClose(1006);
    vi.advanceTimersByTime(1000);
    MockWebSocket.last().serverOpen();
    MockWebSocket.last().serverClose(1006);
    vi.advanceTimersByTime(500);
    expect(MockWebSocket.instances).toHaveLength(4);
  });

  it("delivers the fresh snapshot after a reconnect", () => {
    const qs = make();
    const messages: ServerMessage[] = [];
    qs.onMessage((m) => messages.push(m));
    qs.connect();
    MockWebSocket.last().serverOpen();
    MockWebSocket.last().serverClose(1006);
    vi.advanceTimersByTime(500);
    MockWebSocket.last().serverOpen();
    MockWebSocket.last().serverSend(snapshot);
    expect(messages).toEqual([snapshot]);
  });

  it("stops on 4401 and reports unauthorized", () => {
    const qs = make();
    const statuses: string[] = [];
    qs.onStatus((s) => statuses.push(s));
    qs.connect();
    MockWebSocket.last().serverOpen();
    MockWebSocket.last().serverClose(4401);
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("unauthorized");
  });

  it("close() stops reconnecting", () => {
    const qs = make();
    const statuses: string[] = [];
    qs.onStatus((s) => statuses.push(s));
    qs.connect();
    MockWebSocket.last().serverOpen();
    qs.close();
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("closed");
  });

  it("ignores frames that are not JSON objects", () => {
    const qs = make();
    const messages: ServerMessage[] = [];
    qs.onMessage((m) => messages.push(m));
    qs.connect();
    MockWebSocket.last().serverOpen();
    MockWebSocket.last().serverSendRaw("not json");
    MockWebSocket.last().serverSendRaw("[1,2]");
    expect(messages).toEqual([]);
  });

  it("unsubscribes listeners", () => {
    const qs = make();
    const messages: ServerMessage[] = [];
    const off = qs.onMessage((m) => messages.push(m));
    qs.connect();
    MockWebSocket.last().serverOpen();
    off();
    MockWebSocket.last().serverSend(snapshot);
    expect(messages).toEqual([]);
  });
});
