import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CallerDetails } from "../api/types";
import { CallerPanel, type CallerPanelProps } from "./CallerPanel";

/** Accept summary for demo persona Maria Alvarez (docs/db.md). */
const mariaIntake = {
  full_name: "Maria Alvarez",
  dob: "1984-03-12",
  member_id: "EVM100231",
  reason: "MRI claim denied",
  claim_number: "CLM-900001",
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
      ref: "CLM-900001",
      occurred_on: "2026-10-30",
      status: "denied",
      summary: "MRI, knee, left claim denied, CO-197",
    },
  ],
};

const danielIntake = {
  full_name: "Daniel Okafor",
  dob: "1990-06-17",
  member_id: "EVM100235",
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
      name: "Maria Alvarez",
      verified: "verified",
      reason: "MRI claim denied",
      summary: mariaIntake.summary,
    },
    memberId: "EVM100231",
    intake: mariaIntake,
    waitSecs: 102,
    details: null,
    detailsState: "loading",
    ...over,
  };
  render(<CallerPanel {...props} />);
  return screen.getByRole("complementary", { name: /caller/i });
}

const fact = (panel: HTMLElement, term: string) => {
  const dt = within(panel).getByText(term, { selector: "dt" });
  return dt.nextElementSibling as HTMLElement;
};

describe("CallerPanel (AP-5, AP-13, DESIGN.md caller panel)", () => {
  it("fills from the accept summary at once, before the history arrives", () => {
    const panel = setup();
    expect(within(panel).getByRole("heading", { name: "Maria Alvarez" })).toBeInTheDocument();
    expect(within(panel).getByTestId("caller-id")).toHaveTextContent("EVM100231");
    const why = within(panel).getByTestId("why");
    expect(why).toHaveTextContent("Why they called");
    expect(why).toHaveTextContent("MRI claim was denied. She expected it to be covered.");
    expect(fact(panel, "Claim")).toHaveTextContent("CLM-900001");
    expect(fact(panel, "Waited")).toHaveTextContent("1 min 42 s");
    expect(within(panel).getByTestId("history")).toHaveTextContent(/loading/i);
  });

  it("verified repeat caller (Maria): plan in mono, born, repeat chip, three colored events", () => {
    const panel = setup({ details: mariaDetails, detailsState: "ready" });
    const id = within(panel).getByTestId("caller-id");
    expect(id).toHaveClass("mono");
    expect(id).toHaveTextContent("EVM100231 · Evergreen HMO Silver");
    expect(fact(panel, "Born")).toHaveTextContent("Mar 12, 1984");
    expect(fact(panel, "Claim")).toHaveClass("mono");
    const calls = fact(panel, "Calls, 30 d");
    expect(calls).toHaveTextContent("2");
    const chip = within(calls).getByText("Repeat");
    expect(chip).toHaveClass("chip", "chip-warn");

    const items = within(within(panel).getByTestId("history")).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Nov 9");
    expect(items[0]).toHaveTextContent("called claims, 9 min, escalated to claims review");
    expect(items[2]).toHaveTextContent("Oct 30");
    expect(items[2]).toHaveTextContent("MRI, knee, left claim denied, CO-197");
    const dots = items.map((li) => li.querySelector(".tl-dot")!.className);
    expect(dots[0]).toContain("tl-info");
    expect(dots[2]).toContain("tl-bad");
  });

  it("verified, one call in 30 days: count without the Repeat chip", () => {
    const panel = setup({
      details: { ...mariaDetails, calls_30d: 1, repeat_caller: false },
      detailsState: "ready",
    });
    const calls = fact(panel, "Calls, 30 d");
    expect(calls).toHaveTextContent("1");
    expect(within(calls).queryByText("Repeat")).not.toBeInTheDocument();
  });

  it("verified with no history: zero calls and an empty timeline note", () => {
    const panel = setup({
      details: { ...mariaDetails, calls_30d: 0, repeat_caller: false, recent_events: [] },
      detailsState: "ready",
    });
    expect(fact(panel, "Calls, 30 d")).toHaveTextContent("0");
    const history = within(panel).getByTestId("history");
    expect(within(history).queryAllByRole("listitem")).toHaveLength(0);
    expect(history).toHaveTextContent(/no past calls, claims, or referrals/i);
  });

  it("not verified (Daniel): intake data only, a warning, no member record or history", () => {
    const panel = setup({
      caller: {
        name: "Daniel Okafor",
        verified: "not_verified",
        reason: danielIntake.reason,
        summary: danielIntake.summary,
      },
      memberId: "EVM100235",
      intake: danielIntake,
      waitSecs: 40,
      details: danielDetails,
      detailsState: "ready",
    });
    expect(within(panel).getByRole("heading", { name: "Daniel Okafor" })).toBeInTheDocument();
    expect(within(panel).getByTestId("caller-id")).toHaveTextContent("EVM100235 · Not verified");
    expect(within(panel).getByTestId("caller-id")).not.toHaveTextContent("Harbor");
    expect(within(panel).getByRole("note")).toHaveTextContent(/identity is not verified/i);
    // What the caller said, labeled as such.
    expect(fact(panel, "Born, said")).toHaveTextContent("Jun 17, 1990");
    expect(within(panel).queryByText("Calls, 30 d")).not.toBeInTheDocument();
    expect(within(panel).queryByText("Repeat")).not.toBeInTheDocument();
    const history = within(panel).getByTestId("history");
    expect(within(history).queryAllByRole("listitem")).toHaveLength(0);
    expect(history).toHaveTextContent(/once identity is verified/i);
  });

  it("not found: same intake only view", () => {
    const panel = setup({
      caller: { name: "Pat Doe", verified: "not_found", reason: "Billing", summary: "" },
      memberId: null,
      intake: { full_name: "Pat Doe", reason: "Billing" },
      details: { ...danielDetails, identity_result: "not_found", intake: {} },
      detailsState: "ready",
    });
    expect(within(panel).getByTestId("caller-id")).toHaveTextContent("No member ID");
    expect(within(panel).getByRole("note")).toHaveTextContent(/not verified/i);
    expect(within(panel).getByTestId("why")).toHaveTextContent("Billing");
  });

  it("history error keeps the summary and says history is unavailable", () => {
    const panel = setup({ details: null, detailsState: "error" });
    expect(within(panel).getByRole("heading", { name: "Maria Alvarez" })).toBeInTheDocument();
    expect(within(panel).getByTestId("history")).toHaveTextContent(/history is unavailable/i);
    expect(within(panel).queryByText("Calls, 30 d")).not.toBeInTheDocument();
  });

  it("mood over call: empty state with the Calm to Upset axis until tone readings arrive", () => {
    const panel = setup({ details: mariaDetails, detailsState: "ready" });
    const mood = within(panel).getByTestId("mood");
    expect(mood).toHaveTextContent("Mood over call");
    expect(mood).toHaveTextContent("Calm");
    expect(mood).toHaveTextContent("Upset");
    expect(mood).toHaveTextContent(/mood shows up once the caller talks/i);
    expect(mood.querySelector("path.spark-line")).toBeNull();
  });

  it("mood over call: draws a sparkline when readings are given", () => {
    const panel = setup({ details: mariaDetails, detailsState: "ready", mood: [0.1, 0.3, 0.6, 0.9] });
    const mood = within(panel).getByTestId("mood");
    expect(mood.querySelector("path.spark-line")).not.toBeNull();
    expect(mood).not.toHaveTextContent(/mood shows up/i);
  });
});
