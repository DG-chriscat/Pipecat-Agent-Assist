import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InCall, type InCallProps } from "./InCall";

function setup(over: Partial<InCallProps> = {}) {
  const props: InCallProps = {
    caller: {
      name: "Maria Alvarez",
      verified: "verified",
      reason: "MRI claim from Oct 28 was denied",
      summary: "Denied MRI claim",
    },
    memberId: "EVM100231",
    phase: "live",
    muted: false,
    held: false,
    remoteLevel: 0,
    elapsedSecs: 252,
    error: null,
    transcript: [],
    highlights: [],
    onMute: vi.fn(),
    onHold: vi.fn(),
    onEnd: vi.fn(),
    ...over,
  };
  const r = render(<InCall {...props} />);
  return { ...r, props };
}

describe("InCall shell (AP-4)", () => {
  it("shows the call island: caller, verified chip, waveform, mood placeholder, timer", () => {
    setup();
    const island = screen.getByRole("group", { name: /call/i });
    expect(within(island).getByText("Maria Alvarez")).toBeInTheDocument();
    expect(within(island).getByText("Verified")).toBeInTheDocument();
    expect(within(island).getByTestId("waveform")).toBeInTheDocument();
    expect(within(island).getByTestId("mood-chip")).toBeInTheDocument();
    expect(within(island).getByRole("timer")).toHaveTextContent("04:12");
  });

  it("drives the waveform from the remote audio level and animates only while someone speaks", () => {
    const { rerender, props } = setup({ remoteLevel: 0 });
    const wave = screen.getByTestId("waveform");
    expect(wave).not.toHaveClass("speaking");
    rerender(<InCall {...props} remoteLevel={0.4} />);
    expect(screen.getByTestId("waveform")).toHaveClass("speaking");
    const bars = screen.getByTestId("waveform").querySelectorAll("i");
    expect(bars.length).toBe(7);
    const h = Array.from(bars).map((b) => parseFloat((b as HTMLElement).style.height));
    expect(Math.max(...h)).toBeGreaterThan(8);
  });

  it("lays out caller panel, assist column with the Listening hero, and transcript column", () => {
    setup();
    const caller = screen.getByRole("complementary", { name: /caller/i });
    expect(within(caller).getByText("Maria Alvarez")).toBeInTheDocument();
    expect(within(caller).getByText("EVM100231")).toBeInTheDocument();
    expect(within(caller).getByText("MRI claim from Oct 28 was denied")).toBeInTheDocument();
    const assist = screen.getByRole("region", { name: /assist/i });
    expect(within(assist).getByText("Listening")).toBeInTheDocument();
    const ask = within(assist).getByRole("textbox", { name: /ask/i });
    expect(ask).toBeDisabled();
    const transcript = screen.getByRole("complementary", { name: /transcript/i });
    expect(within(transcript).getByText(/live transcript/i)).toBeInTheDocument();
  });

  it("controls call back and reflect mute state", () => {
    const { props, rerender } = setup();
    const mute = screen.getByRole("button", { name: /mute/i });
    expect(mute).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(mute);
    expect(props.onMute).toHaveBeenCalledTimes(1);
    rerender(<InCall {...props} muted />);
    expect(screen.getByRole("button", { name: /mute/i })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /hold/i }));
    expect(props.onHold).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /end/i }));
    expect(props.onEnd).toHaveBeenCalledTimes(1);
  });

  it("on hold: island says so, hold is pressed, mute is locked", () => {
    setup({ held: true, muted: true });
    expect(screen.getByRole("button", { name: /hold/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /mute/i })).toBeDisabled();
    expect(screen.getByRole("group", { name: /call/i })).toHaveTextContent(/on hold/i);
  });

  it("while connecting: shows the intake summary already, mute and hold wait, End works", () => {
    const { props } = setup({ phase: "connecting", elapsedSecs: 0 });
    expect(screen.getByRole("complementary", { name: /caller/i })).toHaveTextContent("Denied MRI claim");
    expect(screen.getByRole("group", { name: /call/i })).toHaveTextContent(/connecting/i);
    expect(screen.getByRole("button", { name: /mute/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /hold/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /end/i }));
    expect(props.onEnd).toHaveBeenCalled();
  });

  it("shows a media error", () => {
    setup({ error: "Could not join the call audio." });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not join the call audio.");
  });
});
