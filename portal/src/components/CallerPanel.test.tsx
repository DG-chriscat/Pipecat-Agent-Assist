import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CallerDetails } from "../api/types";
import type { MoodState } from "../state/assist";
import { reading } from "../test/assistFixtures";
import { CallerPanel, type CallerPanelProps } from "./CallerPanel";

/** Accept summary for demo persona Maria Lopez (docs/db.md). */
const mariaIntake = {
  full_name: "Maria Lopez",
  dob: "1984-03-12",
  member_id: "418302",
  reason: "MRI claim denied",
  claim_number: "70412",
  summary: "MRI claim was denied. She expected it to be covered.",
};

const mariaDetails: CallerDetails = {
  session_id: "s1",
  identity_result: "verified",
  not_verified: false,
  outcome: "completed",
  flags: [],
  intake: mariaIntake,
  wait_secs: 102,
  member: {
    member_id: "418302",
    name: "Maria Lopez",
    first_name: "Maria",
    last_name: "Lopez",
    dob: "1984-03-12",
    plan_id: "PLN-HMO-SILVER",
    plan_name: "Evergreen HMO Silver",
    plan_type: "HMO",
  },
  calls_30d: 2,
  repeat_caller: true,
  recent_events: [
    {
      kind: "call",
      ref: "INT-000001",
      occurred_on: "2026-11-09",
      status: "escalated to claims review",
      summary: "called claims, 9 min, escalated to claims review",
    },
    {
      kind: "call",
      ref: "INT-000002",
      occurred_on: "2026-10-31",
      status: "explained claim status",
      summary: "called claims, 5 min, explained claim status",
    },
    {
      kind: "claim",
      ref: "70412",
      occurred_on: "2026-10-30",
      status: "denied",
      summary: "MRI, knee, left claim denied, CO-197",
    },
  ],
};

const danielIntake = {
  full_name: "Ben Cole",
  dob: "1990-06-17",
  member_id: "852617",
  reason: "Knee surgery prior auth status",
  summary: "Asks about knee surgery authorization.",
};

const danielDetails: CallerDetails = {
  session_id: "s1",
  identity_result: "not_verified",
  not_verified: true,
  outcome: "completed",
  flags: ["identity_not_verified"],
  intake: danielIntake,
  wait_secs: 40,
  member: null,
  calls_30d: null,
  repeat_caller: null,
  recent_events: [],
};

function setup(over: Partial<CallerPanelProps> = {}) {
  const props: CallerPanelProps = {
    caller: {
      name: "Maria Lopez",
      verified: "verified",
      identity: "verified",
      reason: "MRI claim denied",
      summary: mariaIntake.summary,
    },
    memberId: "418302",
    intake: { ...mariaIntake, identity_checks: ["full_name", "dob", "member_id"] },
    waitSecs: 102,
    details: null,
    detailsState: "loading",
    onRevealPhi: vi.fn(),
    ...over,
  };
  render(<CallerPanel {...props} />);
  return { panel: screen.getByRole("complementary", { name: /caller/i }), props };
}

const row = (panel: HTMLElement, term: string) => {
  const dt = within(panel).getByText(term, { selector: "dt" });
  return dt.nextElementSibling as HTMLElement;
};

const daniel = {
  caller: {
    name: "Ben Cole",
    verified: "not_verified" as const,
    identity: "not_verified" as const,
    reason: danielIntake.reason,
    summary: danielIntake.summary,
  },
  memberId: "852617",
  intake: { ...danielIntake, identity_checks: ["full_name", "member_id"] },
  waitSecs: 40,
  details: danielDetails,
  detailsState: "ready" as const,
};

describe("CallerPanel (AP-5, AP-13, AP-17, AP-18, DESIGN.md caller panel)", () => {
  it("fills from the accept summary at once, before the history arrives", () => {
    const { panel } = setup();
    expect(within(panel).getByRole("heading", { name: "Maria Lopez" })).toBeInTheDocument();
    expect(within(panel).getByTestId("phi-member_id")).toHaveTextContent("••••31");
    const quote = within(panel).getByTestId("why");
    expect(quote).toHaveTextContent("In their words");
    expect(quote).toHaveTextContent("MRI claim denied");
    expect(row(panel, "Claim")).toHaveTextContent("70412");
    expect(row(panel, "Waited")).toHaveTextContent("1:42");
    expect(within(panel).getByTestId("history")).toHaveTextContent(/loading/i);
  });

  it("verified (Maria): copper avatar inside the phosphor ring, plan, masked ID and DOB", () => {
    const { panel } = setup({ details: mariaDetails, detailsState: "ready" });
    const avatar = within(panel).getByTestId("caller-avatar");
    expect(avatar).toHaveTextContent("MA");
    expect(avatar).toHaveClass("is-verified");
    const id = within(panel).getByTestId("caller-id");
    expect(id).toHaveTextContent("Evergreen HMO Silver");
    expect(id).toHaveTextContent("••••31");
    expect(id).not.toHaveTextContent("418302");
    expect(row(panel, "Born")).toHaveTextContent("••/••/1984");
    expect(row(panel, "Born")).not.toHaveTextContent("Mar 12");
    expect(row(panel, "Claim")).toHaveClass("mono");
  });

  it("Reveal shows the member ID and DOB and asks the parent to log each reveal (AP-17)", () => {
    const { panel, props } = setup({ details: mariaDetails, detailsState: "ready" });
    fireEvent.click(within(panel).getByRole("button", { name: "Reveal member ID" }));
    expect(within(panel).getByTestId("phi-member_id")).toHaveTextContent("418302");
    fireEvent.click(within(panel).getByRole("button", { name: "Reveal date of birth" }));
    expect(row(panel, "Born")).toHaveTextContent("Mar 12, 1984");
    expect(props.onRevealPhi).toHaveBeenNthCalledWith(1, "member_id");
    expect(props.onRevealPhi).toHaveBeenNthCalledWith(2, "dob");
  });

  it("identity badge: 3 of 3 confirmed, with the checks on hover (AP-18)", () => {
    const { panel } = setup({ details: mariaDetails, detailsState: "ready" });
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Identity 3 of 3");
    expect(badge).toHaveClass("chip-ok");
    expect(badge).toHaveAttribute("title", "Name ✓ · Date of birth ✓ · Member ID ✓");
  });

  it("identity badge: partial n of 3 in bakelite, no ring (AP-18)", () => {
    const { panel } = setup(daniel);
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Identity 2 of 3");
    expect(badge).toHaveClass("chip-warn");
    expect(badge).toHaveAttribute("title", "Name ✓ · Date of birth ✗ · Member ID ✓");
    expect(within(panel).getByTestId("caller-avatar")).not.toHaveClass("is-verified");
  });

  it("identity badge: not checked when intake never checked identity (AP-18)", () => {
    const { panel } = setup({
      caller: { name: "Pat Doe", verified: "not_verified", identity: null, reason: "Billing", summary: "" },
      memberId: null,
      intake: { full_name: "Pat Doe", reason: "Billing" },
      details: { ...danielDetails, identity_result: null, intake: {} },
      detailsState: "ready",
    });
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Identity not checked");
    expect(badge).toHaveClass("chip-neutral");
  });

  it("repeat caller badge when calls in 30 days is 2 or more", () => {
    const { panel } = setup({ details: mariaDetails, detailsState: "ready" });
    expect(within(panel).getByText("Repeat caller")).toHaveClass("chip", "chip-warn");
    expect(row(panel, "Calls, 30 d")).toHaveTextContent("2");
  });

  it("verified, one call in 30 days: count without the Repeat caller badge", () => {
    const { panel } = setup({
      details: { ...mariaDetails, calls_30d: 1, repeat_caller: false },
      detailsState: "ready",
    });
    expect(row(panel, "Calls, 30 d")).toHaveTextContent("1");
    expect(within(panel).queryByText("Repeat caller")).not.toBeInTheDocument();
  });

  it("settings rows show the last call and the recent events", () => {
    const { panel } = setup({ details: mariaDetails, detailsState: "ready" });
    expect(row(panel, "Last call")).toHaveTextContent("Nov 9");
    const items = within(within(panel).getByTestId("history")).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Nov 9");
    expect(items[0]).toHaveTextContent("called claims, 9 min, escalated to claims review");
    expect(items[2]).toHaveTextContent("MRI, knee, left claim denied, CO-197");
    const dots = items.map((li) => li.querySelector(".tl-dot")!.className);
    expect(dots[0]).toContain("tl-info");
    expect(dots[2]).toContain("tl-bad");
  });

  it("verified with no history: an empty timeline note", () => {
    const { panel } = setup({
      details: { ...mariaDetails, calls_30d: 0, repeat_caller: false, recent_events: [] },
      detailsState: "ready",
    });
    expect(row(panel, "Calls, 30 d")).toHaveTextContent("0");
    const history = within(panel).getByTestId("history");
    expect(within(history).queryAllByRole("listitem")).toHaveLength(0);
    expect(history).toHaveTextContent(/no past calls, claims, or referrals/i);
  });

  it("not verified (Daniel): intake data only, a warning, no member record or history", () => {
    const { panel } = setup(daniel);
    expect(within(panel).getByRole("heading", { name: "Ben Cole" })).toBeInTheDocument();
    expect(within(panel).getByTestId("caller-id")).toHaveTextContent("Not verified");
    expect(within(panel).getByTestId("caller-id")).not.toHaveTextContent("Harbor");
    expect(within(panel).getByTestId("phi-member_id")).toHaveTextContent("••••35");
    expect(within(panel).getByRole("note")).toHaveTextContent(/identity is not verified/i);
    // What the caller said, labeled as such, still masked.
    expect(row(panel, "Born, said")).toHaveTextContent("••/••/1990");
    expect(within(panel).queryByText("Calls, 30 d")).not.toBeInTheDocument();
    expect(within(panel).queryByText("Repeat caller")).not.toBeInTheDocument();
    const history = within(panel).getByTestId("history");
    expect(within(history).queryAllByRole("listitem")).toHaveLength(0);
    expect(history).toHaveTextContent(/once identity is verified/i);
  });

  it("not found: same intake only view, no member ID", () => {
    const { panel } = setup({
      caller: { name: "Pat Doe", verified: "not_found", identity: "not_found", reason: "Billing", summary: "" },
      memberId: null,
      intake: { full_name: "Pat Doe", reason: "Billing", identity_checks: [] },
      details: { ...danielDetails, identity_result: "not_found", intake: {} },
      detailsState: "ready",
    });
    expect(within(panel).getByTestId("caller-id")).toHaveTextContent("No member ID");
    expect(within(panel).getByTestId("identity-badge")).toHaveTextContent("Identity 0 of 3");
    expect(within(panel).getByRole("note")).toHaveTextContent(/not verified/i);
    expect(within(panel).getByTestId("why")).toHaveTextContent("Billing");
  });

  it("history error keeps the summary and says history is unavailable", () => {
    const { panel } = setup({ details: null, detailsState: "error" });
    expect(within(panel).getByRole("heading", { name: "Maria Lopez" })).toBeInTheDocument();
    expect(within(panel).getByTestId("history")).toHaveTextContent(/history is unavailable/i);
    expect(within(panel).queryByText("Calls, 30 d")).not.toBeInTheDocument();
  });

  it("mood area: placeholder pinned at the bottom until tone readings arrive (P4-5)", () => {
    const { panel } = setup({ details: mariaDetails, detailsState: "ready" });
    const mood = within(panel).getByTestId("mood");
    expect(mood).toHaveTextContent("Mood");
    expect(mood).toHaveAttribute("title", "Tone reader, words only.");
    expect(mood).toHaveTextContent(/mood shows up once the caller talks/i);
    expect(mood.querySelectorAll(".bars i")).toHaveLength(0);
  });

  it("mood area: one bar per caller turn, bakelite when worried, label chip with its tone", () => {
    const mood: MoodState = {
      label: "Worried, easing",
      tone: "warn",
      step: 2,
      readings: [1, 2, 3, 4, 2].map((s, i) => reading(s, `caller-${i + 1}`)),
    };
    const { panel } = setup({ details: mariaDetails, detailsState: "ready", mood });
    const area = within(panel).getByTestId("mood");
    const bars = area.querySelectorAll(".bars i");
    expect(bars).toHaveLength(5);
    expect(bars[0]).not.toHaveClass("is-worried");
    expect(bars[2]).toHaveClass("is-worried");
    expect(bars[3]).toHaveClass("is-worried");
    expect((bars[3] as HTMLElement).style.height).toBe("100%");
    expect((bars[0] as HTMLElement).style.height).toBe("40%");
    expect(bars[3]).toHaveAttribute("title", "Frustrated (neutral 0.90)");
    const chip = within(area).getByText("Worried, easing");
    expect(chip).toHaveClass("chip-warn");
    expect(area).not.toHaveTextContent(/mood shows up/i);
    expect(area).toHaveAttribute("aria-label", "Mood: Worried, easing. 5 caller turns.");
  });
});

/** Linda Nguyen heard as "Linda Yuen": member ID and DOB exact (IN-6 close match). */
const linda = {
  caller: {
    name: "Linda Yuen",
    verified: "close_match" as const,
    identity: "close_match" as const,
    reason: "Dermatology claim denied",
    summary: "",
  },
  memberId: "741526",
  intake: { full_name: "Linda Yuen", dob: "1958-04-22", reason: "Dermatology claim denied", identity_checks: ["dob", "member_id"] },
  waitSecs: 30,
  details: {
    ...danielDetails,
    identity_result: "close_match" as const,
    identity_decision: null,
    intake: {},
  },
  detailsState: "ready" as const,
};

describe("CallerPanel human verification (IN-13)", () => {
  it("before the agent decides: Reveal is disabled and the badge asks to confirm", () => {
    const { panel, props } = setup({ ...linda, identityGate: { state: "pending", result: "close_match", checks: ["dob", "member_id"], by: null, at: null } });
    const reveal = within(panel).getByRole("button", { name: "Reveal member ID" });
    expect(reveal).toBeDisabled();
    expect(reveal).toHaveAttribute("title", "Verify the caller to reveal");
    fireEvent.click(reveal);
    expect(props.onRevealPhi).not.toHaveBeenCalled();
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Identity 2 of 3");
    expect(badge).toHaveClass("chip-warn");
  });

  it("Mark verified: the badge says Verified by agent in phosphor, the name on hover", () => {
    const decided = {
      ...linda,
      details: {
        ...mariaDetails,
        identity_result: "close_match" as const,
        identity_decision: { decision: "verified" as const, agent_id: "a1", agent_name: "Ana", at: 1794800900 },
        member: { ...mariaDetails.member!, name: "Linda Nguyen", first_name: "Linda", last_name: "Nguyen", member_id: "741526" },
      },
      identityGate: { state: "verified" as const, result: "close_match", checks: ["dob", "member_id"], by: "Ana", at: 1794800900 },
    };
    const { panel } = setup(decided);
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Verified by agent");
    expect(badge).toHaveClass("chip-ok");
    expect(badge).toHaveAttribute("title", "Verified by Ana on the call");
    expect(within(panel).getByTestId("caller-avatar")).toHaveClass("is-verified");
    expect(within(panel).getByRole("heading", { name: "Linda Nguyen" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Reveal member ID" })).toBeEnabled();
  });

  it("Could not verify: Reveal stays off and the badge says so", () => {
    const { panel } = setup({
      ...linda,
      identityGate: { state: "not_verified", result: "close_match", checks: ["dob", "member_id"], by: "Ana", at: 1 },
    });
    expect(within(panel).getByRole("button", { name: "Reveal member ID" })).toBeDisabled();
    const badge = within(panel).getByTestId("identity-badge");
    expect(badge).toHaveTextContent("Not verified by agent");
    expect(badge).toHaveClass("chip-bad");
  });
});
