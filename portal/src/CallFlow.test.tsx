import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { QueueSocket } from "./api/queueClient";
import type { AcceptResult, Agent, Call } from "./api/types";
import { createFakeMedia, FakeMedia } from "./test/fakeMedia";
import { MockWebSocket } from "./test/mockSocket";

const NOW = 1_800_000_000; // seconds; fake system clock base

const ana: Agent = {
  agent_id: "agent-1",
  name: "Ana",
  state: "available",
  idle_since: NOW - 60,
  offer_call_id: null,
  offer_expires_at: null,
  call_id: null,
};

const call: Call = {
  session_id: "s1",
  room_url: "https://x.daily.co/r1",
  room_name: "r1",
  summary: {
    full_name: "Maria Alvarez",
    reason: "MRI claim from Oct 28 was denied",
    summary: "Denied MRI claim, wants to know why",
  },
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
  status: "queued",
  queued_at: NOW - 100,
  offered_to: "agent-1",
  agent_id: null,
  answered_at: null,
  ended_at: null,
};

const ringing: Agent = { ...ana, state: "ringing", offer_call_id: "s1", offer_expires_at: NOW + 20 };
const onCall: Agent = { ...ana, state: "on_call", call_id: "s1", idle_since: null };

const acceptResult: AcceptResult = {
  session_id: "s1",
  room_url: call.room_url,
  token: "meeting-token",
  expires_at: NOW + 3600,
  summary: call.summary,
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const makeSocket = () =>
  new QueueSocket({
    url: "ws://test/ws/queue",
    WebSocketImpl: MockWebSocket as unknown as typeof WebSocket,
    minDelayMs: 10,
    maxDelayMs: 10,
    random: () => 0.5,
  });

type Route = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;

describe("incoming call and in-call shell (AP-3, AP-4, QH-6, QH-7)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let route: Route;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(NOW * 1000);
    MockWebSocket.reset();
    FakeMedia.reset();
    sessionStorage.clear();
    route = () => undefined;
    fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const r = await route(url, init);
      if (r) return r;
      if (url === "/agents/sign-in") return json(ana);
      return json({ detail: `unexpected ${url}` }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function signedInAvailable() {
    render(<App createSocket={makeSocket} createMedia={createFakeMedia} />);
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Ana" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("switch", { name: /available/i });
    const sock = MockWebSocket.last();
    act(() => sock.serverOpen());
    act(() => sock.serverSend({ type: "snapshot", queue: [], active: [], agents: [ana], offer_timeout_secs: 20 }));
    return sock;
  }

  function ring(sock: MockWebSocket) {
    act(() => {
      sock.serverSend({ type: "call_queued", call, position: 1 });
      sock.serverSend({ type: "agent_updated", agent: ringing });
      sock.serverSend({ type: "offer", agent_id: "agent-1", call, expires_at: NOW + 20 });
    });
    return screen.getByRole("dialog", { name: /incoming call/i });
  }

  function tick(ms: number) {
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  }

  async function acceptAndJoin(sock: MockWebSocket) {
    route = (url) => (url === "/agents/agent-1/accept" ? json(acceptResult) : undefined);
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    await screen.findByRole("group", { name: /call/i });
    act(() => sock.serverSend({ type: "agent_updated", agent: onCall }));
    const media = FakeMedia.last();
    await act(async () => media.finishJoin());
    return media;
  }

  it("rings with the offer card and counts down from 20", async () => {
    const sock = await signedInAvailable();
    const card = ring(sock);
    expect(card).toHaveTextContent("Maria Alvarez");
    expect(card).toHaveTextContent("Verified");
    expect(card).toHaveTextContent("MRI claim from Oct 28 was denied");
    expect(card).toHaveTextContent("1 min 40 s");
    expect(within(card).getByRole("timer")).toHaveTextContent("20");
    tick(1000);
    expect(within(card).getByRole("timer")).toHaveTextContent("19");
    tick(14_000);
    expect(within(card).getByRole("timer")).toHaveTextContent("5");
    tick(5_000);
    expect(within(card).getByRole("timer")).toHaveTextContent("0");
    expect(within(card).getByRole("button", { name: /accept/i })).toBeDisabled();
  });

  it("rebuilds the offer card after a reload from the snapshot", async () => {
    render(<App createSocket={makeSocket} createMedia={createFakeMedia} />);
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: "Ana" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("switch", { name: /available/i });
    const sock = MockWebSocket.last();
    act(() => sock.serverOpen());
    vi.setSystemTime((NOW + 8) * 1000);
    act(() =>
      sock.serverSend({ type: "snapshot", queue: [call], active: [], agents: [ringing], offer_timeout_secs: 20 }),
    );
    const card = screen.getByRole("dialog", { name: /incoming call/i });
    expect(within(card).getByRole("timer")).toHaveTextContent("12");
  });

  it("accept posts to the server, shows the intake summary at once, then joins the room", async () => {
    let release: (r: Response) => void = () => {};
    route = (url) => (url === "/agents/agent-1/accept" ? new Promise<Response>((r) => (release = r)) : undefined);
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe("/agents/agent-1/accept");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ session_id: "s1" });
    // In flight: the card shows progress and ignores more input.
    expect(screen.getByRole("button", { name: /accept/i })).toHaveAttribute("aria-busy", "true");

    await act(async () => release(json(acceptResult)));
    // QH-7: the summary is on screen before the audio connects.
    const caller = screen.getByRole("complementary", { name: /caller/i });
    expect(caller).toHaveTextContent("Maria Alvarez");
    expect(caller).toHaveTextContent("EVM100231");
    expect(caller).toHaveTextContent("MRI claim from Oct 28 was denied");
    expect(screen.getByRole("group", { name: /call/i })).toHaveTextContent(/connecting/i);
    expect(screen.queryByRole("dialog", { name: /incoming call/i })).not.toBeInTheDocument();

    const media = FakeMedia.last();
    expect(media.joined).toEqual({ roomUrl: "https://x.daily.co/r1", token: "meeting-token" });
    await act(async () => media.finishJoin());
    expect(screen.getByRole("group", { name: /call/i })).not.toHaveTextContent(/connecting/i);
    expect(screen.getByRole("button", { name: /mute/i })).toBeEnabled();
  });

  it("Enter accepts the offer", async () => {
    route = (url) => (url === "/agents/agent-1/accept" ? json(acceptResult) : undefined);
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.keyDown(window, { key: "Enter" });
    await screen.findByRole("group", { name: /call/i });
    expect(fetchMock.mock.calls.filter(([u]) => u === "/agents/agent-1/accept")).toHaveLength(1);
  });

  it("an accept error keeps the card and shows the message", async () => {
    route = (url) =>
      url === "/agents/agent-1/accept" ? json({ detail: "could not create a meeting token, try again" }, 502) : undefined;
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not create a meeting token");
    expect(FakeMedia.instances).toHaveLength(0);
  });

  it("decline posts to the server; the card closes when the server sets not available", async () => {
    route = (url) => (url === "/agents/agent-1/decline" ? json({ status: "declined" }) : undefined);
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /decline/i }));
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe("/agents/agent-1/decline");
    expect(JSON.parse(init.body)).toEqual({ session_id: "s1" });
    act(() => {
      sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "offline" } });
      sock.serverSend({ type: "offer_missed", agent_id: "agent-1", session_id: "s1", reason: "declined" });
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /incoming call/i })).not.toBeInTheDocument());
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/declined/i);
  });

  it("Esc declines the offer", async () => {
    route = (url) => (url === "/agents/agent-1/decline" ? json({ status: "declined" }) : undefined);
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)![0]).toBe("/agents/agent-1/decline"));
  });

  it("timeout: the server misses the offer and sets not available; the card closes with a notice", async () => {
    const sock = await signedInAvailable();
    ring(sock);
    tick(20_000);
    act(() => {
      sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "offline" } });
      sock.serverSend({ type: "offer_missed", agent_id: "agent-1", session_id: "s1", reason: "timeout" });
    });
    expect(screen.queryByRole("dialog", { name: /incoming call/i })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/missed a call/i);
    expect(screen.getByRole("switch", { name: /available/i })).toHaveAttribute("aria-checked", "false");
  });

  it("ignores offers and misses for other agents", async () => {
    const sock = await signedInAvailable();
    act(() => {
      sock.serverSend({ type: "offer", agent_id: "agent-2", call, expires_at: NOW + 20 });
      sock.serverSend({ type: "offer_missed", agent_id: "agent-2", session_id: "s1", reason: "timeout" });
    });
    expect(screen.queryByRole("dialog", { name: /incoming call/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("mute toggles the mic; hold mutes the mic and resume restores it", async () => {
    const sock = await signedInAvailable();
    const media = await acceptAndJoin(sock);
    const mute = screen.getByRole("button", { name: /mute/i });
    fireEvent.click(mute);
    expect(media.mic.at(-1)).toBe(false);
    expect(mute).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(mute);
    expect(media.mic.at(-1)).toBe(true);
    expect(mute).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: /hold/i }));
    expect(media.mic.at(-1)).toBe(false);
    expect(screen.getByRole("button", { name: /hold/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /mute/i })).toBeDisabled();
    expect(screen.getByRole("group", { name: /call/i })).toHaveTextContent(/on hold/i);
    fireEvent.click(screen.getByRole("button", { name: /hold/i }));
    expect(media.mic.at(-1)).toBe(true);

    // Muted before hold stays muted after resume.
    fireEvent.click(screen.getByRole("button", { name: /mute/i }));
    fireEvent.click(screen.getByRole("button", { name: /hold/i }));
    fireEvent.click(screen.getByRole("button", { name: /hold/i }));
    expect(media.mic.at(-1)).toBe(false);
    // Hold is local only in v1: no status command went out.
    expect(sock.sent.map((s) => JSON.parse(s).type)).not.toContain("set_status");
  });

  it("the timer runs from accept and the waveform follows the loudest remote speaker", async () => {
    const sock = await signedInAvailable();
    const media = await acceptAndJoin(sock);
    tick(65_000);
    expect(screen.getByRole("timer", { name: /call time/i })).toHaveTextContent("01:05");
    expect(screen.getByTestId("waveform")).not.toHaveClass("speaking");
    act(() => {
      media.events.onRemoteLevel(0.3, "caller");
      media.events.onRemoteLevel(0, "bot");
    });
    expect(screen.getByTestId("waveform")).toHaveClass("speaking");
    act(() => media.events.onRemoteLevel(0, "caller"));
    expect(screen.getByTestId("waveform")).not.toHaveClass("speaking");
  });

  it("End leaves the room, sets wrap up, and goes back home", async () => {
    const sock = await signedInAvailable();
    const media = await acceptAndJoin(sock);
    fireEvent.click(screen.getByRole("button", { name: /end call/i }));
    await waitFor(() => expect(media.left).toBe(1));
    expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "set_status", agent_id: "agent-1", state: "wrap_up" });
    // Until the server confirms, no "lost the call audio" prompt flashes up.
    expect(screen.queryByText(/lost the call audio/i)).not.toBeInTheDocument();
    act(() => sock.serverSend({ type: "status_set", agent: { ...ana, state: "wrap_up" } }));
    await screen.findByText("Wrap up");
    expect(screen.queryByRole("group", { name: /call/i })).not.toBeInTheDocument();
  });

  it("caller hangs up: the server sets wrap up, the portal leaves the room", async () => {
    const sock = await signedInAvailable();
    const media = await acceptAndJoin(sock);
    act(() => {
      sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "wrap_up", idle_since: null } });
      sock.serverSend({ type: "call_ended", call: { ...call, status: "ended" } });
    });
    await waitFor(() => expect(media.left).toBe(1));
    expect(screen.queryByRole("group", { name: /call/i })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/caller hung up/i);
  });

  it("a join failure keeps the call screen with an error so the agent can end it", async () => {
    route = (url) => (url === "/agents/agent-1/accept" ? json(acceptResult) : undefined);
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    await screen.findByRole("group", { name: /call/i });
    await act(async () => FakeMedia.last().failJoin("no mic"));
    expect(screen.getByRole("alert")).toHaveTextContent(/could not connect the call audio/i);
    expect(screen.getByRole("button", { name: /end call/i })).toBeEnabled();
  });

  it("after a reload mid call, offers End to move to wrap up", async () => {
    const sock = await signedInAvailable();
    act(() => sock.serverSend({ type: "agent_updated", agent: onCall }));
    expect(screen.getByText(/lost the call audio/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /end call/i }));
    await act(async () => {});
    expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "set_status", agent_id: "agent-1", state: "wrap_up" });
  });

  it("after accept, fetches the caller history for this agent and fills the panel (AP-5, AP-13)", async () => {
    let releaseCaller: (r: Response) => void = () => {};
    route = (url) => {
      if (url === "/agents/agent-1/accept") return json(acceptResult);
      if (url === "/calls/s1/caller?agent_id=agent-1") return new Promise<Response>((r) => (releaseCaller = r));
      return undefined;
    };
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    const panel = await screen.findByRole("complementary", { name: /caller/i });
    // Accept summary first: name and reason are there, history is loading.
    expect(panel).toHaveTextContent("Maria Alvarez");
    expect(panel).toHaveTextContent(/loading history/i);
    await act(async () =>
      releaseCaller(
        json({
          session_id: "s1",
          identity_result: "verified",
          not_verified: false,
          outcome: "completed",
          flags: [],
          intake: call.summary,
          wait_secs: 102,
          member: {
            member_id: "EVM100231",
            name: "Maria Alvarez",
            first_name: "Maria",
            last_name: "Alvarez",
            dob: "1984-03-12",
            plan_id: "PLN-HMO-SILVER",
            plan_name: "Evergreen HMO Silver",
            plan_type: "HMO",
          },
          calls_30d: 2,
          repeat_caller: true,
          recent_events: [
            {
              kind: "claim",
              ref: "CLM-900001",
              occurred_on: "2026-10-30",
              status: "denied",
              summary: "MRI, knee, left claim denied, CO-197",
            },
          ],
        }),
      ),
    );
    expect(panel).toHaveTextContent("EVM100231 · Evergreen HMO Silver");
    expect(panel).toHaveTextContent("Mar 12, 1984");
    expect(within(panel).getByText("Repeat")).toBeInTheDocument();
    expect(panel).toHaveTextContent("MRI, knee, left claim denied, CO-197");
  });

  it("a caller history error keeps the accept summary on screen", async () => {
    route = (url) => {
      if (url === "/agents/agent-1/accept") return json(acceptResult);
      if (url.startsWith("/calls/s1/caller")) return json({ detail: "caller history is unavailable" }, 503);
      return undefined;
    };
    const sock = await signedInAvailable();
    ring(sock);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    const panel = await screen.findByRole("complementary", { name: /caller/i });
    await waitFor(() => expect(panel).toHaveTextContent(/history is unavailable/i));
    expect(panel).toHaveTextContent("Maria Alvarez");
    expect(panel).toHaveTextContent("MRI claim from Oct 28 was denied");
  });

  it("the toggle is not shown as a way out while on a call", async () => {
    const sock = await signedInAvailable();
    await acceptAndJoin(sock);
    expect(screen.queryByRole("switch", { name: /available/i })).not.toBeInTheDocument();
    // userEvent still works with the partial fake timers.
    await userEvent.setup().keyboard("{Enter}");
    expect(fetchMock.mock.calls.filter(([u]) => u === "/agents/agent-1/accept")).toHaveLength(1);
  });
});
