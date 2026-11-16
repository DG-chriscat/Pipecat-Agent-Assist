import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { QueueSocket } from "./api/queueClient";
import type { Agent, Call } from "./api/types";
import { MockWebSocket } from "./test/mockSocket";

const ana: Agent = {
  agent_id: "agent-1",
  name: "Ana",
  state: "offline",
  idle_since: null,
  offer_call_id: null,
  offer_expires_at: null,
  call_id: null,
};

const queued = (id: string): Call => ({
  session_id: id,
  room_url: `https://x.daily.co/${id}`,
  room_name: id,
  summary: {},
  outcome: "completed",
  identity_result: "verified",
  member_id: null,
  flags: [],
  status: "queued",
  queued_at: 1,
  offered_to: null,
  agent_id: null,
  answered_at: null,
  ended_at: null,
});

function jsonResponse(body: unknown, status = 200) {
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

function sendSnapshot(sock: MockWebSocket, agents: Agent[] = [ana], queue: Call[] = []) {
  act(() => {
    sock.serverSend({ type: "snapshot", queue, active: [], agents, offer_timeout_secs: 20 });
  });
}

async function signInAsAna() {
  const user = userEvent.setup();
  render(<App createSocket={makeSocket} />);
  await user.type(screen.getByLabelText(/your name/i), "Ana");
  await user.click(screen.getByRole("button", { name: /sign in/i }));
  await screen.findByRole("switch", { name: /available/i });
  const sock = MockWebSocket.last();
  act(() => sock.serverOpen());
  return { user, sock };
}

describe("App", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    MockWebSocket.reset();
    sessionStorage.clear();
    fetchMock = vi.fn().mockImplementation(async () => jsonResponse(ana));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  describe("sign in (AP-1)", () => {
    it("shows the sign in screen first, with the brand top bar", () => {
      render(<App createSocket={makeSocket} />);
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(screen.getByAltText("Deepgram")).toBeInTheDocument();
      expect(screen.getByText("Vitals")).toBeInTheDocument();
      expect(screen.queryByText(/agent assist/i)).not.toBeInTheDocument();
      expect(MockWebSocket.instances).toHaveLength(0);
    });

    it("does not submit an empty name", async () => {
      const user = userEvent.setup();
      render(<App createSocket={makeSocket} />);
      expect(screen.getByRole("button", { name: /sign in/i })).toBeDisabled();
      await user.type(screen.getByLabelText(/your name/i), "   ");
      expect(screen.getByRole("button", { name: /sign in/i })).toBeDisabled();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("posts the trimmed name, then shows the idle home", async () => {
      await signInAsAna();
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe("/agents/sign-in");
      expect(JSON.parse(init.body)).toEqual({ name: "Ana" });
      expect(screen.getByText("Ana")).toBeInTheDocument();
      expect(MockWebSocket.last().url).toBe("ws://test/ws/queue");
    });

    it("shows the server error and stays on sign in", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "name must be 1 to 100 characters" }, 409));
      const user = userEvent.setup();
      render(<App createSocket={makeSocket} />);
      await user.type(screen.getByLabelText(/your name/i), "Ana");
      await user.click(screen.getByRole("button", { name: /sign in/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("name must be 1 to 100 characters");
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    });

    it("remembers the name for this tab and signs back in on reload", async () => {
      await signInAsAna();
      expect(sessionStorage.getItem("vitals.agentName")).toBe("Ana");
      // Simulate a reload.
      cleanup();
      fetchMock.mockClear();
      render(<App createSocket={makeSocket} />);
      await screen.findByRole("switch", { name: /available/i });
      expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "Ana" });
    });

    it("sign out goes back to sign in and sets the agent not available", async () => {
      const { user, sock } = await signInAsAna();
      sendSnapshot(sock, [{ ...ana, state: "available" }]);
      await user.click(screen.getByRole("button", { name: /agent menu/i }));
      await user.click(screen.getByRole("menuitem", { name: /sign out/i }));
      expect(JSON.parse(sock.sent.at(-1)!)).toEqual({
        type: "set_status",
        agent_id: "agent-1",
        state: "offline",
      });
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(sessionStorage.getItem("vitals.agentName")).toBeNull();
    });
  });

  describe("theme (AP-16)", () => {
    beforeEach(() => {
      localStorage.clear();
      delete document.documentElement.dataset.theme;
    });

    it("starts light on sign in, with the Studio rail and header", () => {
      render(<App createSocket={makeSocket} />);
      expect(document.documentElement.dataset.theme).toBe("light");
      expect(screen.getByText("PHI masked")).toBeInTheDocument();
    });

    it("switches in the rail avatar menu and is saved per agent", async () => {
      const { user } = await signInAsAna();
      const rail = screen.getByRole("navigation", { name: /main/i });
      expect(rail).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /agent menu/i }));
      const dark = screen.getByRole("menuitemradio", { name: /dark/i });
      expect(dark).toHaveAttribute("aria-checked", "false");
      expect(screen.getByRole("menuitemradio", { name: /light/i })).toHaveAttribute("aria-checked", "true");
      await user.click(dark);
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(localStorage.getItem("vitals.theme.Ana")).toBe("dark");

      // Reload: Ana gets dark back; another agent starts light.
      cleanup();
      delete document.documentElement.dataset.theme;
      render(<App createSocket={makeSocket} />);
      await screen.findByRole("switch", { name: /available/i });
      expect(document.documentElement.dataset.theme).toBe("dark");
      await user.click(screen.getByRole("button", { name: /agent menu/i }));
      await user.click(screen.getByRole("menuitem", { name: /sign out/i }));
      fetchMock.mockImplementation(async () => jsonResponse({ ...ana, agent_id: "agent-2", name: "Ben" }));
      await user.type(screen.getByLabelText(/your name/i), "Ben");
      await user.click(screen.getByRole("button", { name: /sign in/i }));
      await screen.findByRole("switch", { name: /available/i });
      expect(document.documentElement.dataset.theme).toBe("light");
    });
  });

  describe("idle home and status (AP-2)", () => {
    it("shows the calm empty state and the live queue count from the snapshot", async () => {
      const { sock } = await signInAsAna();
      sendSnapshot(sock, [ana], [queued("a"), queued("b")]);
      expect(screen.getByText("Waiting for the next call")).toBeInTheDocument();
      expect(screen.getByTestId("queue-count")).toHaveTextContent("2");
      act(() => sock.serverSend({ type: "call_queued", call: queued("c"), position: 3 }));
      expect(screen.getByTestId("queue-count")).toHaveTextContent("3");
      act(() => sock.serverSend({ type: "call_ended", call: { ...queued("a"), status: "abandoned" } }));
      expect(screen.getByTestId("queue-count")).toHaveTextContent("2");
    });

    it("toggles available and not available over the websocket", async () => {
      const { user, sock } = await signInAsAna();
      sendSnapshot(sock);
      const toggle = screen.getByRole("switch", { name: /available/i });
      expect(toggle).toHaveAttribute("aria-checked", "false");
      expect(screen.getByText("Not available")).toBeInTheDocument();

      await user.click(toggle);
      expect(JSON.parse(sock.sent.at(-1)!)).toEqual({
        type: "set_status",
        agent_id: "agent-1",
        state: "available",
      });
      // Pending until the server confirms.
      expect(toggle).toHaveAttribute("aria-busy", "true");
      act(() => sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "available" } }));
      act(() => sock.serverSend({ type: "status_set", agent: { ...ana, state: "available" } }));
      expect(toggle).toHaveAttribute("aria-checked", "true");
      expect(toggle).toHaveAttribute("aria-busy", "false");

      await user.click(toggle);
      expect(JSON.parse(sock.sent.at(-1)!)).toMatchObject({ state: "offline" });
      act(() => sock.serverSend({ type: "status_set", agent: ana }));
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("follows server side status changes (missed offer sets not available)", async () => {
      const { sock } = await signInAsAna();
      sendSnapshot(sock, [{ ...ana, state: "available" }]);
      const toggle = screen.getByRole("switch", { name: /available/i });
      expect(toggle).toHaveAttribute("aria-checked", "true");
      act(() => sock.serverSend({ type: "agent_updated", agent: ana }));
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("names service set states and locks the toggle while ringing or on a call", async () => {
      const { sock } = await signInAsAna();
      sendSnapshot(sock, [{ ...ana, state: "ringing" }]);
      const toggle = screen.getByRole("switch", { name: /available/i });
      expect(screen.getByText("Ringing")).toBeInTheDocument();
      expect(toggle).toBeDisabled();
      act(() => sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "on_call" } }));
      expect(screen.getByText("On call")).toBeInTheDocument();
      expect(toggle).toBeDisabled();
      act(() => sock.serverSend({ type: "agent_updated", agent: { ...ana, state: "wrap_up" } }));
      expect(screen.getByText("Wrap up")).toBeInTheDocument();
      expect(toggle).toBeEnabled();
    });

    it("shows a status error from the server", async () => {
      const { user, sock } = await signInAsAna();
      sendSnapshot(sock);
      await user.click(screen.getByRole("switch", { name: /available/i }));
      act(() => sock.serverSend({ type: "error", message: "cannot go from ringing to available" }));
      expect(screen.getByRole("alert")).toHaveTextContent("cannot go from ringing to available");
      expect(screen.getByRole("switch", { name: /available/i })).toHaveAttribute("aria-busy", "false");
    });

    it("disables the toggle while reconnecting and resyncs from the new snapshot", async () => {
      const { sock } = await signInAsAna();
      sendSnapshot(sock, [ana], [queued("a")]);
      act(() => sock.serverClose(1006));
      expect(screen.getByRole("switch", { name: /available/i })).toBeDisabled();
      expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();

      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
      const sock2 = MockWebSocket.last();
      act(() => sock2.serverOpen());
      sendSnapshot(sock2, [{ ...ana, state: "available" }], [queued("a"), queued("b"), queued("c")]);
      expect(screen.getByRole("switch", { name: /available/i })).toBeEnabled();
      expect(screen.getByRole("switch", { name: /available/i })).toHaveAttribute("aria-checked", "true");
      expect(screen.getByTestId("queue-count")).toHaveTextContent("3");
    });

    it("signs in again on the socket when the server lost the agent (restart)", async () => {
      const { sock } = await signInAsAna();
      sendSnapshot(sock, []);
      expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "sign_in", name: "Ana" });
      act(() => sock.serverSend({ type: "signed_in", agent: { ...ana, agent_id: "agent-2" } }));
      act(() => sock.serverSend({ type: "agent_updated", agent: { ...ana, agent_id: "agent-2" } }));
      const toggle = screen.getByRole("switch", { name: /available/i });
      await userEvent.setup().click(toggle);
      expect(JSON.parse(sock.sent.at(-1)!)).toMatchObject({ agent_id: "agent-2", state: "available" });
    });

    it("tells the agent to reload when the login expired (4401)", async () => {
      const { sock } = await signInAsAna();
      act(() => sock.serverClose(4401));
      expect(screen.getByRole("alert")).toHaveTextContent(/session expired/i);
    });
  });
});
