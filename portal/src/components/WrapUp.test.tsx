import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WrapUpInfo } from "../api/types";
import { WrapUp } from "./WrapUp";

const info: WrapUpInfo = {
  session_id: "s1",
  status: "ended",
  ended_by: "caller",
  caller_name: "Maria Alvarez",
  reason: "MRI claim from Oct 28 was denied",
  identity_result: "verified",
  duration_secs: 252,
  summary_draft: null,
  reason_codes: [
    { code: "claim_status_given", label: "Claim status given" },
    { code: "denial_explained", label: "Denial explained" },
    { code: "other", label: "Other" },
  ],
  wrap_up: null,
};

function setup(overrides: Partial<Parameters<typeof WrapUp>[0]> = {}) {
  const onSave = vi.fn();
  const onRetry = vi.fn();
  render(
    <WrapUp info={info} loadError={null} saving={false} saveError={null} onSave={onSave} onRetry={onRetry} {...overrides} />,
  );
  return { onSave, onRetry };
}

describe("WrapUp (P3-5)", () => {
  it("shows who ended the call, the caller, and the call length", () => {
    setup();
    const view = screen.getByRole("region", { name: /wrap up/i });
    expect(view).toHaveTextContent("Maria Alvarez");
    expect(view).toHaveTextContent(/the caller hung up/i);
    expect(view).toHaveTextContent("04:12");
    expect(view).toHaveTextContent("MRI claim from Oct 28 was denied");
    expect(within(view).getByText("Verified")).toBeInTheDocument();
  });

  it("says when the agent ended it", () => {
    setup({ info: { ...info, ended_by: "agent" } });
    expect(screen.getByRole("region", { name: /wrap up/i })).toHaveTextContent(/you ended the call/i);
  });

  it("shows a placeholder until the assist drafts the summary (AP-9)", () => {
    setup();
    expect(screen.getByTestId("summary-draft")).toHaveTextContent(/summary draft/i);
    expect(screen.getByTestId("summary-draft")).toHaveTextContent(/coming soon/i);
  });

  it("shows the draft when there is one", () => {
    setup({ info: { ...info, summary_draft: "Explained the PA-01 denial." } });
    expect(screen.getByTestId("summary-draft")).toHaveTextContent("Explained the PA-01 denial.");
  });

  it("lists the pack's reason codes; Save is disabled until one is picked", () => {
    const { onSave } = setup();
    const group = screen.getByRole("radiogroup", { name: /reason code/i });
    const options = within(group).getAllByRole("radio");
    expect(options.map((o) => o.textContent)).toEqual(["Claim status given", "Denial explained", "Other"]);
    const save = screen.getByRole("button", { name: /save/i });
    expect(save).toBeDisabled();
    fireEvent.click(within(group).getByRole("radio", { name: "Denial explained" }));
    expect(within(group).getByRole("radio", { name: "Denial explained" })).toHaveAttribute("aria-checked", "true");
    expect(save).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/notes/i), { target: { value: "Told her about the retro review." } });
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledWith("denial_explained", "Told her about the retro review.");
  });

  it("Ctrl or Cmd plus Enter saves from the notes field", () => {
    const { onSave } = setup();
    fireEvent.click(screen.getByRole("radio", { name: "Other" }));
    const notes = screen.getByLabelText(/notes/i);
    fireEvent.change(notes, { target: { value: "n" } });
    fireEvent.keyDown(notes, { key: "Enter" });
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.keyDown(notes, { key: "Enter", metaKey: true });
    expect(onSave).toHaveBeenCalledWith("other", "n");
  });

  it("while saving, the form is locked", () => {
    setup({ saving: true });
    expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled();
    expect(screen.getByLabelText(/notes/i)).toBeDisabled();
  });

  it("shows a save error", () => {
    setup({ saveError: "Could not save. Try again." });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save. Try again.");
  });

  it("loading and load error states", () => {
    const { onRetry } = setup({ info: null });
    expect(screen.getByRole("region", { name: /wrap up/i })).toHaveTextContent(/loading the call/i);
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
    render(
      <WrapUp info={null} loadError="Could not load the call." saving={false} saveError={null} onSave={vi.fn()} onRetry={onRetry} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
