import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { HighlightRange, TranscriptLine } from "../state/transcript";
import { TranscriptColumn } from "./TranscriptColumn";

const line = (id: string, role: "caller" | "agent", text: string, final = true): TranscriptLine => ({
  id,
  role,
  text,
  final,
  ts: 0,
});

/** jsdom has no layout: give the scroll box a fixed size and a content height. */
function layout(el: HTMLElement, contentHeight: number, clientHeight = 300) {
  Object.defineProperty(el, "clientHeight", { configurable: true, value: clientHeight });
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: contentHeight });
}

function scrollBox() {
  return screen.getByTestId("transcript-scroll");
}

describe("TranscriptColumn (P3-4)", () => {
  it("shows the Conversation label, the live chip, and an empty note before anyone talks", () => {
    render(<TranscriptColumn lines={[]} highlights={[]} />);
    const col = screen.getByRole("complementary", { name: /conversation/i });
    expect(within(col).getByText("Conversation")).toBeInTheDocument();
    expect(within(col).getByText("Live")).toBeInTheDocument();
    expect(within(col).getByText(/shows up here/i)).toBeInTheDocument();
  });

  it("each line has an avatar: caller initials (copper), agent You (LED tint, muted text)", () => {
    render(
      <TranscriptColumn
        callerName="Maria Alvarez"
        lines={[line("caller-1", "caller", "I already called."), line("agent-1", "agent", "I'm sorry.")]}
        highlights={[]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveClass("tr", "tr-caller");
    const ma = within(rows[0]).getByText("MA");
    expect(ma).toHaveClass("who", "av", "av-caller");
    expect(ma).toHaveAttribute("title", "Caller");
    expect(rows[1]).toHaveClass("tr", "tr-agent");
    expect(within(rows[1]).getByText("You")).toHaveClass("who", "av", "av-agent");
    expect(screen.queryByText(/shows up here/i)).not.toBeInTheDocument();
  });

  it("an interim line renders with the caret, then updates in place and commits", () => {
    const { rerender } = render(
      <TranscriptColumn lines={[line("agent-1", "agent", "Okay, there are", false)]} highlights={[]} />,
    );
    const text = screen.getByText("Okay, there are");
    expect(text).toHaveClass("typing");
    expect(text.closest("li")).toHaveAttribute("aria-busy", "true");
    rerender(
      <TranscriptColumn lines={[line("agent-1", "agent", "Okay, there are two ways", false)]} highlights={[]} />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("Okay, there are two ways")).toHaveClass("typing");
    rerender(
      <TranscriptColumn
        lines={[line("agent-1", "agent", "Okay, there are two ways to handle this.", true)]}
        highlights={[]}
      />,
    );
    const done = screen.getByText("Okay, there are two ways to handle this.");
    expect(done).not.toHaveClass("typing");
    expect(done.closest("li")).toHaveAttribute("aria-busy", "false");
  });

  it("underlines highlighted trigger words", () => {
    const hl: HighlightRange[] = [{ lineId: "caller-1", start: 11, end: 19 }];
    render(<TranscriptColumn lines={[line("caller-1", "caller", "I have the referral.")]} highlights={hl} />);
    const mark = screen.getByText("referral");
    expect(mark.tagName).toBe("MARK");
    expect(mark.closest("p")).toHaveTextContent("I have the referral.");
  });

  it("autoscrolls to the newest line while the agent is at the bottom", () => {
    const { rerender } = render(<TranscriptColumn lines={[line("caller-1", "caller", "One.")]} highlights={[]} />);
    const box = scrollBox();
    layout(box, 900);
    rerender(
      <TranscriptColumn
        lines={[line("caller-1", "caller", "One."), line("agent-1", "agent", "Two.")]}
        highlights={[]}
      />,
    );
    expect(box.scrollTop).toBe(900);
    expect(screen.queryByRole("button", { name: /latest/i })).not.toBeInTheDocument();
  });

  it("pauses autoscroll when the agent scrolls up, and resumes from Jump to latest", () => {
    const lines = [line("caller-1", "caller", "One."), line("agent-1", "agent", "Two.")];
    const { rerender } = render(<TranscriptColumn lines={lines} highlights={[]} />);
    const box = scrollBox();
    layout(box, 900);
    box.scrollTop = 200; // agent scrolled up to read
    fireEvent.scroll(box);
    const more = [...lines, line("caller-2", "caller", "Three.")];
    rerender(<TranscriptColumn lines={more} highlights={[]} />);
    expect(box.scrollTop).toBe(200); // nothing moved under the agent's eyes
    const jump = screen.getByRole("button", { name: /jump to latest/i });

    layout(box, 1000);
    act(() => jump.click());
    expect(box.scrollTop).toBe(1000);
    expect(screen.queryByRole("button", { name: /latest/i })).not.toBeInTheDocument();
    rerender(<TranscriptColumn lines={[...more, line("agent-2", "agent", "Four.")]} highlights={[]} />);
    layout(box, 1100);
    rerender(
      <TranscriptColumn
        lines={[...more, line("agent-2", "agent", "Four."), line("caller-3", "caller", "Five.")]}
        highlights={[]}
      />,
    );
    expect(box.scrollTop).toBe(1100);
  });

  it("scrolling back to the bottom by hand resumes autoscroll", () => {
    const lines = [line("caller-1", "caller", "One.")];
    const { rerender } = render(<TranscriptColumn lines={lines} highlights={[]} />);
    const box = scrollBox();
    layout(box, 900);
    box.scrollTop = 100;
    fireEvent.scroll(box);
    expect(screen.getByRole("button", { name: /jump to latest/i })).toBeInTheDocument();
    box.scrollTop = 600; // 900 - 300: at the bottom
    fireEvent.scroll(box);
    expect(screen.queryByRole("button", { name: /latest/i })).not.toBeInTheDocument();
    layout(box, 1000);
    rerender(<TranscriptColumn lines={[...lines, line("agent-1", "agent", "Two.")]} highlights={[]} />);
    expect(box.scrollTop).toBe(1000);
  });
});
