import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DemoPersona, DemosApi, DemosResponse } from "../api/demosClient";
import { Demos } from "./Demos";
import type { ShellAgent } from "./Shell";

const shell: ShellAgent = {
  name: "Ana",
  online: false,
  theme: "light",
  onTheme: () => {},
  onSignOut: () => {},
  page: "demos",
  onPage: () => {},
};

const persona = (over: Partial<DemoPersona>): DemoPersona => ({
  key: "maria_lopez",
  name: "Maria Lopez",
  dob: "1984-03-12",
  dob_said: "March twelfth, nineteen eighty four",
  member_id: "418302",
  member_id_groups: "418 302",
  plan_id: "PLN-HMO-SILVER",
  plan: "Evergreen HMO Silver",
  in_seed: true,
  hero: false,
  pack: "claims_status",
  case: "MRI claim 70412 denied, missing prior authorization",
  start_line: "My MRI claim was denied and I don't know why.",
  audience_sees: { headline: "Start a retro authorization review for 70412.", cards: ["Denied claim 70412"] },
  features: ["identity_verified", "repeat_caller"],
  kb_articles: [],
  script: [
    { to: "bot", say: "My name is Maria Lopez." },
    { to: "agent", say: "Hi, my MRI claim was denied.", note: "The hero appears." },
  ],
  ...over,
});

const DATA: DemosResponse = {
  phone_line: { number: "+1 (612) 746-7133", password_hint: "ask Chris" },
  features: [
    { id: "identity_verified", label: "Identity verified" },
    { id: "close_match", label: "Close match verification" },
    { id: "repeat_caller", label: "Repeat caller" },
    { id: "duplicate_charge", label: "Duplicate charge alert" },
    { id: "emergency", label: "Emergency guardrail" },
  ],
  personas: [
    persona({ hero: true }),
    persona({
      key: "james_carter",
      name: "James Carter",
      dob: "1979-07-04",
      member_id: "527194",
      member_id_groups: "527 194",
      plan: "Summit PPO Gold",
      pack: "account_payments",
      case: "Premium drafted twice this month, refund the duplicate",
      features: ["identity_verified", "duplicate_charge"],
    }),
    persona({
      key: "linda_nguyen",
      name: "Linda Nguyen",
      member_id: "741526",
      member_id_groups: "741 526",
      features: ["close_match"],
    }),
    persona({
      key: "emily_stone",
      name: "Emily Stone",
      member_id: "307495",
      member_id_groups: "307 495",
      features: ["emergency"],
      warning: "Do not use in a customer demo without warning. It shows the emergency guardrail.",
    }),
  ],
};

function setup(over: { api?: DemosApi; copy?: (t: string) => Promise<void> } = {}) {
  const user = userEvent.setup();
  const api: DemosApi = over.api ?? { list: vi.fn().mockResolvedValue(DATA) };
  const copy = over.copy ?? vi.fn().mockResolvedValue(undefined);
  render(<Demos shell={shell} api={api} copy={copy} />);
  return { user, api, copy };
}

const cards = () => screen.getAllByTestId(/^demo-card-/);
const card = (key: string) => screen.getByTestId(`demo-card-${key}`);

describe("Demos page (P5-10)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("is a page of its own with the rail's Demos item current", async () => {
    setup();
    expect(await screen.findByRole("heading", { level: 1, name: "Demos" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Demos" })).toHaveAttribute("aria-current", "page");
  });

  it("shows one card per persona with name, DOB, grouped member ID, plan, case, and pack", async () => {
    setup();
    await screen.findByTestId("demo-card-maria_lopez");
    expect(cards()).toHaveLength(4);
    const c = card("maria_lopez");
    expect(within(c).getByRole("heading", { name: "Maria Lopez" })).toBeInTheDocument();
    expect(within(c).getByText("1984-03-12")).toBeInTheDocument();
    const id = within(c).getByTestId("demo-member-id");
    expect(id).toHaveTextContent("418 302");
    expect(id).toHaveClass("mono");
    expect(within(c).getByText("Evergreen HMO Silver")).toBeInTheDocument();
    expect(within(c).getByText("MRI claim 70412 denied, missing prior authorization")).toBeInTheDocument();
    expect(within(c).getByText("claims_status")).toBeInTheDocument();
    expect(within(c).getByText(/My MRI claim was denied/)).toBeInTheDocument();
    expect(within(c).getByText("Start a retro authorization review for 70412.")).toBeInTheDocument();
    expect(within(c).getByText("Hero demo")).toBeInTheDocument();
  });

  it("tags each card with its feature chips", async () => {
    setup();
    const c = await screen.findByTestId("demo-card-james_carter");
    const chips = within(c).getAllByTestId("demo-feature").map((x) => x.textContent);
    expect(chips).toEqual(["Identity verified", "Duplicate charge alert"]);
  });

  it("shows the phone line and the password hint", async () => {
    setup();
    const box = await screen.findByRole("region", { name: "Phone line" });
    expect(box).toHaveTextContent("+1 (612) 746-7133");
    expect(box).toHaveTextContent("ask Chris");
    expect(within(box).getByTestId("demos-phone-number")).toHaveClass("mono");
  });

  it("marks the emergency demo with its warning", async () => {
    setup();
    const c = await screen.findByTestId("demo-card-emily_stone");
    expect(within(c).getByRole("note")).toHaveTextContent("Do not use in a customer demo without warning");
  });

  describe("filters", () => {
    it("filters by feature chip, and again to clear", async () => {
      const { user } = setup();
      await screen.findByTestId("demo-card-maria_lopez");
      const filters = screen.getByRole("group", { name: "Filter by feature" });
      await user.click(within(filters).getByRole("button", { name: "Close match verification" }));
      expect(cards().map((c) => c.dataset.testid)).toEqual(["demo-card-linda_nguyen"]);
      expect(within(filters).getByRole("button", { name: "Close match verification" })).toHaveAttribute("aria-pressed", "true");
      await user.click(within(filters).getByRole("button", { name: "Close match verification" }));
      expect(cards()).toHaveLength(4);
    });

    it("filters by pack", async () => {
      const { user } = setup();
      await screen.findByTestId("demo-card-maria_lopez");
      const packs = screen.getByRole("radiogroup", { name: "Filter by pack" });
      await user.click(within(packs).getByRole("radio", { name: "account_payments" }));
      expect(cards().map((c) => c.dataset.testid)).toEqual(["demo-card-james_carter"]);
      await user.click(within(packs).getByRole("radio", { name: "All packs" }));
      expect(cards()).toHaveLength(4);
    });

    it("says so when nothing matches", async () => {
      const { user } = setup();
      await screen.findByTestId("demo-card-maria_lopez");
      await user.click(within(screen.getByRole("radiogroup", { name: "Filter by pack" })).getByRole("radio", { name: "account_payments" }));
      await user.click(within(screen.getByRole("group", { name: "Filter by feature" })).getByRole("button", { name: "Emergency guardrail" }));
      expect(screen.queryAllByTestId(/^demo-card-/)).toHaveLength(0);
      expect(screen.getByText("No demo matches these filters.")).toBeInTheDocument();
    });
  });

  describe("actions", () => {
    it("Copy details copies name, DOB, and member ID", async () => {
      const { user, copy } = setup();
      const c = await screen.findByTestId("demo-card-maria_lopez");
      await user.click(within(c).getByRole("button", { name: "Copy details" }));
      expect(copy).toHaveBeenCalledWith("Maria Lopez\nDOB 1984-03-12\nMember ID 418 302");
      expect(await within(c).findByRole("status")).toHaveTextContent("Copied");
    });

    it("Start browser call opens /caller with the persona in a new tab", async () => {
      setup();
      const c = await screen.findByTestId("demo-card-maria_lopez");
      const link = within(c).getByRole("link", { name: /Start browser call/ });
      expect(link).toHaveAttribute("href", "/caller?persona=maria_lopez");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    });

    it("Show script expands the lines: intake answers, then lines for the agent", async () => {
      const { user } = setup();
      const c = await screen.findByTestId("demo-card-maria_lopez");
      expect(within(c).queryByRole("list", { name: "Script" })).not.toBeInTheDocument();
      const btn = within(c).getByRole("button", { name: "Show script" });
      expect(btn).toHaveAttribute("aria-expanded", "false");
      await user.click(btn);
      expect(within(c).getByRole("button", { name: "Hide script" })).toHaveAttribute("aria-expanded", "true");
      const steps = within(within(c).getByRole("list", { name: "Script" })).getAllByRole("listitem");
      expect(steps.map((s) => s.textContent)).toEqual([
        "To the botMy name is Maria Lopez.",
        "To the agentHi, my MRI claim was denied.The hero appears.",
      ]);
    });
  });

  it("shows an error when the personas cannot load", async () => {
    setup({ api: { list: vi.fn().mockRejectedValue(new Error("Request failed (500)")) } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Request failed (500)");
  });

  it("loads once on mount", async () => {
    const { api } = setup();
    await screen.findByTestId("demo-card-maria_lopez");
    await act(async () => {});
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(1));
  });
});
