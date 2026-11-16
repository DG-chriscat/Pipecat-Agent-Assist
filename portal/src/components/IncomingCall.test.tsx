import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Call } from "../api/types";
import { IncomingCall } from "./IncomingCall";

const call: Call = {
  session_id: "s1",
  room_url: "https://x.daily.co/r1",
  room_name: "r1",
  summary: { full_name: "Maria Alvarez", reason: "MRI claim from Oct 28 was denied", summary: "Denied MRI claim" },
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
  status: "queued",
  queued_at: 1000,
  offered_to: "agent-1",
  agent_id: null,
  answered_at: null,
  ended_at: null,
};

function setup(over: Partial<Parameters<typeof IncomingCall>[0]> = {}) {
  const props = {
    offer: { call, expiresAt: 1120 },
    windowSecs: 20,
    nowMs: 1102_000,
    busy: false,
    error: null,
    onAccept: vi.fn(),
    onDecline: vi.fn(),
    ...over,
  };
  const r = render(<IncomingCall {...props} />);
  return { ...r, props };
}

describe("IncomingCall (AP-3)", () => {
  it("shows caller name, verified status, reason, and wait time", () => {
    setup();
    const card = screen.getByRole("dialog", { name: /incoming call/i });
    expect(card).toHaveTextContent("Maria Alvarez");
    expect(card).toHaveTextContent("Verified");
    expect(card).toHaveTextContent("MRI claim from Oct 28 was denied");
    // queued at 1000, now 1102: waited 1 min 42 s
    expect(card).toHaveTextContent("1 min 42 s");
  });

  it("flags a caller who did not verify", () => {
    setup({ offer: { call: { ...call, identity_result: "not_verified" }, expiresAt: 1120 } });
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("shows the seconds left and a ring that empties with them", () => {
    const { rerender, props } = setup();
    const timer = screen.getByRole("timer");
    expect(timer).toHaveTextContent("18");
    const ring = screen.getByTestId("countdown-ring");
    expect(ring.getAttribute("data-fraction")).toBe("0.9");
    rerender(<IncomingCall {...props} nowMs={1115_000} />);
    expect(screen.getByRole("timer")).toHaveTextContent("5");
    expect(screen.getByTestId("countdown-ring").getAttribute("data-fraction")).toBe("0.25");
  });

  it("calls onAccept and onDecline from the buttons", () => {
    const { props } = setup();
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    expect(props.onAccept).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /decline/i }));
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("Enter accepts and Esc declines", () => {
    const { props } = setup();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(props.onAccept).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("ignores keys and clicks while a command is in flight", () => {
    const { props } = setup({ busy: true });
    fireEvent.keyDown(window, { key: "Enter" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: /accept/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /decline/i })).toBeDisabled();
    expect(props.onAccept).not.toHaveBeenCalled();
    expect(props.onDecline).not.toHaveBeenCalled();
  });

  it("disables accept once the countdown runs out", () => {
    const { props } = setup({ nowMs: 1120_500 });
    expect(screen.getByRole("timer")).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: /accept/i })).toBeDisabled();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(props.onAccept).not.toHaveBeenCalled();
  });

  it("shows an accept error", () => {
    setup({ error: "no open offer for call 's1'" });
    expect(screen.getByRole("alert")).toHaveTextContent("no open offer");
  });

  it("stops listening for keys when it unmounts", () => {
    const { props, unmount } = setup();
    unmount();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(props.onAccept).not.toHaveBeenCalled();
  });
});
