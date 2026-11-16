import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { applyAssist, emptyAssist, parseAssistMessage } from "../state/assist";
import { moodMsg } from "../test/assistFixtures";
import { InCall, type InCallProps } from "./InCall";

function setup(over: Partial<InCallProps> = {}) {
  const props: InCallProps = {
    caller: {
      name: "Maria Alvarez",
      verified: "verified",
      identity: "verified",
      reason: "MRI claim from Oct 28 was denied",
      summary: "Denied MRI claim",
    },
    memberId: "EVM100231",
    shell: { name: "Ana", online: true, theme: "light", onTheme: vi.fn(), onSignOut: vi.fn() },
    onRevealPhi: vi.fn(),
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

  it("mood chip: a dash until the first reading, then the mood label in its tone (AP-14)", () => {
    const { rerender, props } = setup();
    const chip = () => within(screen.getByRole("group", { name: /call/i })).getByTestId("mood-chip");
    expect(chip()).toHaveTextContent("Mood –");
    expect(chip()).toHaveClass("chip-neutral");
    expect(chip()).toHaveAttribute("title", "Tone reader, words only.");
    const assist = applyAssist(emptyAssist(), parseAssistMessage(moodMsg([1, 4], "Frustrated, worsening", "bad"))!, 0);
    rerender(<InCall {...props} assist={assist} />);
    expect(chip()).toHaveTextContent("Frustrated, worsening");
    expect(chip()).toHaveClass("chip-bad");
    // The caller panel draws the same trend.
    expect(screen.getByTestId("mood").querySelectorAll(".bars i")).toHaveLength(2);
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

  it("lays out caller panel, assist column with the Studio placeholders, and conversation", () => {
    setup();
    const caller = screen.getByRole("complementary", { name: /caller/i });
    expect(within(caller).getByText("Maria Alvarez", { exact: false })).toBeInTheDocument();
    // AP-17: masked in the panel.
    expect(within(caller).getByTestId("phi-member_id")).toHaveTextContent("••••31");
    expect(within(caller).queryByText("EVM100231")).not.toBeInTheDocument();
    expect(within(caller).getByText("MRI claim from Oct 28 was denied")).toBeInTheDocument();
    const assist = screen.getByRole("region", { name: /assist/i });
    // Hero listening state, three empty shelf tiles, Earlier this call, Ask bar (P4-6: live).
    expect(within(assist).getByText("Listening with you")).toBeInTheDocument();
    expect(within(assist).getByText("Next best step")).toBeInTheDocument();
    expect(within(assist).getAllByTestId("shelf-tile")).toHaveLength(3);
    expect(within(assist).getByText("Earlier this call")).toBeInTheDocument();
    const ask = within(assist).getByRole("textbox", { name: /ask/i });
    expect(ask).toBeEnabled();
    expect(ask).toHaveAttribute("placeholder", "Ask about Maria");
    const transcript = screen.getByRole("complementary", { name: /conversation/i });
    expect(within(transcript).getByText("Conversation")).toBeInTheDocument();
  });

  it("header: wordmark, Vitals, PHI masked chip, rail with Calls active", () => {
    setup();
    expect(screen.getByAltText("Deepgram")).toBeInTheDocument();
    expect(screen.getByText("Vitals")).toBeInTheDocument();
    expect(screen.getByText("PHI masked")).toBeInTheDocument();
    const rail = screen.getByRole("navigation", { name: /main/i });
    expect(within(rail).getByRole("button", { name: "Calls" })).toHaveAttribute("aria-current", "page");
    expect(within(rail).getByRole("button", { name: "Queue" })).toBeDisabled();
    expect(within(rail).getByRole("button", { name: "History" })).toBeDisabled();
    expect(within(rail).getByRole("button", { name: /agent menu/i })).toHaveTextContent("AN");
    expect(screen.queryByText(/agent assist/i)).not.toBeInTheDocument();
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
