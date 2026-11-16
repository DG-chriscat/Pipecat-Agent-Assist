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
  suggested_reason_code: null,
  draft_pending: false,
  pack_id: "claims_status",
  reason_codes: [
    { code: "claim_status_given", label: "Claim status given" },
    { code: "denial_explained", label: "Denial explained" },
    { code: "appeal_started", label: "Appeal or retro review started" },
    { code: "other", label: "Other" },
  ],
  wrap_up: null,
};

const DRAFT =
  "Maria Alvarez called about her denied MRI claim CLM-900001. The agent explained the CO-197 denial. " +
  "The agent requested a retro authorization review for CLM-900001.";
const drafted: WrapUpInfo = { ...info, summary_draft: DRAFT, suggested_reason_code: "appeal_started" };

type Props = Parameters<typeof WrapUp>[0];

function props(overrides: Partial<Props> = {}): Props {
  return {
    info,
    loadError: null,
    saving: false,
    saveError: null,
    onSave: vi.fn(),
    onRetry: vi.fn(),
    ...overrides,
  };
}

function setup(overrides: Partial<Props> = {}) {
  const p = props(overrides);
  const view = render(<WrapUp {...p} />);
  return { ...p, rerender: (o: Partial<Props>) => view.rerender(<WrapUp {...p} {...o} />) };
}

const summaryBox = () => screen.getByLabelText(/^summary$/i) as HTMLTextAreaElement;
const radio = (name: string) => screen.getByRole("radio", { name: new RegExp(`^${name}`) });

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

  it("lists the pack's reason codes; Save is disabled until one is picked", () => {
    const { onSave } = setup();
    const group = screen.getByRole("radiogroup", { name: /reason code/i });
    const options = within(group).getAllByRole("radio");
    expect(options.map((o) => o.textContent)).toEqual([
      "Claim status given",
      "Denial explained",
      "Appeal or retro review started",
      "Other",
    ]);
    const save = screen.getByRole("button", { name: /save/i });
    expect(save).toBeDisabled();
    fireEvent.click(radio("Denial explained"));
    expect(radio("Denial explained")).toHaveAttribute("aria-checked", "true");
    expect(save).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/notes/i), { target: { value: "Told her about the retro review." } });
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledWith("denial_explained", "Told her about the retro review.", "");
  });

  it("Ctrl or Cmd plus Enter saves from the notes field", () => {
    const { onSave } = setup();
    fireEvent.click(radio("Other"));
    const notes = screen.getByLabelText(/notes/i);
    fireEvent.change(notes, { target: { value: "n" } });
    fireEvent.keyDown(notes, { key: "Enter" });
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.keyDown(notes, { key: "Enter", metaKey: true });
    expect(onSave).toHaveBeenCalledWith("other", "n", "");
  });

  it("while saving, the form is locked", () => {
    setup({ saving: true, info: drafted });
    expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled();
    expect(screen.getByLabelText(/notes/i)).toBeDisabled();
    expect(summaryBox()).toBeDisabled();
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

describe("WrapUp summary draft and suggested reason code (P5-1, AP-9)", () => {
  it("fills the summary editor with the draft and preselects the suggested code", () => {
    const { onSave } = setup({ info: drafted });
    expect(summaryBox().value).toBe(DRAFT);
    expect(screen.getByTestId("summary-draft")).toHaveTextContent(/summary draft/i);
    expect(radio("Appeal or retro review started")).toHaveAttribute("aria-checked", "true");
    expect(radio("Appeal or retro review started")).toHaveTextContent(/suggested/i);
    // Save is ready at once: the suggestion counts as a pick.
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSave).toHaveBeenCalledWith("appeal_started", "", DRAFT);
  });

  it("the agent edits the summary and picks another code; Save sends the edits", () => {
    const { onSave } = setup({ info: drafted });
    fireEvent.change(summaryBox(), { target: { value: "Edited summary." } });
    fireEvent.click(radio("Denial explained"));
    expect(radio("Appeal or retro review started")).toHaveAttribute("aria-checked", "false");
    // The suggestion marker stays on the suggested code.
    expect(radio("Appeal or retro review started")).toHaveTextContent(/suggested/i);
    expect(radio("Denial explained")).not.toHaveTextContent(/suggested/i);
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSave).toHaveBeenCalledWith("denial_explained", "", "Edited summary.");
  });

  it("while the draft is coming, the box says so and the agent can still type", () => {
    setup({ info: { ...info, draft_pending: true } });
    const box = screen.getByTestId("summary-draft");
    expect(box).toHaveClass("is-pending");
    expect(box).toHaveTextContent(/drafting/i);
    expect(summaryBox()).toBeEnabled();
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("a draft that arrives later fills the editor and preselects the code", () => {
    const { rerender } = setup({ info: { ...info, draft_pending: true } });
    rerender({ info: drafted });
    expect(summaryBox().value).toBe(DRAFT);
    expect(radio("Appeal or retro review started")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("summary-draft")).not.toHaveClass("is-pending");
  });

  it("a late draft never overwrites what the agent already typed or picked", () => {
    const { rerender, onSave } = setup({ info: { ...info, draft_pending: true } });
    fireEvent.change(summaryBox(), { target: { value: "My own words." } });
    fireEvent.click(radio("Other"));
    rerender({ info: drafted });
    expect(summaryBox().value).toBe("My own words.");
    expect(radio("Other")).toHaveAttribute("aria-checked", "true");
    expect(radio("Appeal or retro review started")).toHaveTextContent(/suggested/i);
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSave).toHaveBeenCalledWith("other", "", "My own words.");
  });

  it("with no draft and nothing pending, the agent writes the summary", () => {
    setup();
    const box = screen.getByTestId("summary-draft");
    expect(box).toHaveClass("is-empty");
    expect(box).not.toHaveTextContent(/coming soon/i);
    expect(summaryBox()).toHaveAttribute("placeholder", expect.stringMatching(/summary/i));
  });

  it("a suggested code not in the list is ignored", () => {
    setup({ info: { ...drafted, suggested_reason_code: "made_up" } });
    expect(screen.getAllByRole("radio").every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("Ctrl or Cmd plus Enter saves from the summary editor", () => {
    const { onSave } = setup({ info: drafted });
    fireEvent.keyDown(summaryBox(), { key: "Enter", ctrlKey: true });
    expect(onSave).toHaveBeenCalledWith("appeal_started", "", DRAFT);
  });
});
