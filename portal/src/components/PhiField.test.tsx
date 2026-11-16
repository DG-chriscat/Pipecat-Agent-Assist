import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PhiField } from "./PhiField";

describe("PhiField (AP-17)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("a disabled Reveal never reveals or logs (IN-13)", () => {
    const onReveal = vi.fn();
    render(<PhiField field="dob" label="Date of birth" value="Jan 30, 1958" masked="••/••/1958" onReveal={onReveal} disabled />);
    const btn = screen.getByRole("button", { name: "Reveal date of birth" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onReveal).not.toHaveBeenCalled();
    expect(screen.getByTestId("phi-dob")).toHaveTextContent("••/••/1958");
  });

  it("is masked by default", () => {
    render(<PhiField field="member_id" label="Member ID" value="EVM100231" masked="••••31" onReveal={vi.fn()} />);
    expect(screen.getByTestId("phi-member_id")).toHaveTextContent("••••31");
    expect(screen.queryByText("EVM100231")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reveal member ID" })).toBeInTheDocument();
  });

  it("Reveal shows the value and logs one reveal event", () => {
    const onReveal = vi.fn();
    render(<PhiField field="dob" label="Date of birth" value="Mar 12, 1984" masked="••/••/1984" onReveal={onReveal} />);
    fireEvent.click(screen.getByRole("button", { name: "Reveal date of birth" }));
    expect(screen.getByTestId("phi-dob")).toHaveTextContent("Mar 12, 1984");
    expect(onReveal).toHaveBeenCalledTimes(1);
    expect(onReveal).toHaveBeenCalledWith("dob");
    expect(screen.getByRole("button", { name: "Hide date of birth" })).toBeInTheDocument();
  });

  it("re-masks after 30 seconds", () => {
    render(<PhiField field="dob" label="Date of birth" value="Mar 12, 1984" masked="••/••/1984" onReveal={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /reveal/i }));
    act(() => vi.advanceTimersByTime(29_000));
    expect(screen.getByTestId("phi-dob")).toHaveTextContent("Mar 12, 1984");
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByTestId("phi-dob")).toHaveTextContent("••/••/1984");
    expect(screen.queryByText("Mar 12, 1984")).not.toBeInTheDocument();
  });

  it("Hide re-masks at once; a second reveal logs again and restarts the 30 s", () => {
    const onReveal = vi.fn();
    render(<PhiField field="member_id" label="Member ID" value="EVM100231" masked="••••31" onReveal={onReveal} />);
    fireEvent.click(screen.getByRole("button", { name: /reveal/i }));
    act(() => vi.advanceTimersByTime(20_000));
    fireEvent.click(screen.getByRole("button", { name: /hide/i }));
    expect(screen.getByTestId("phi-member_id")).toHaveTextContent("••••31");
    fireEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(onReveal).toHaveBeenCalledTimes(2);
    act(() => vi.advanceTimersByTime(20_000));
    expect(screen.getByTestId("phi-member_id")).toHaveTextContent("EVM100231");
    act(() => vi.advanceTimersByTime(10_000));
    expect(screen.getByTestId("phi-member_id")).toHaveTextContent("••••31");
  });
});
