import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyAssist,
  emptyAssist,
  localAssist,
  parseAssistMessage,
  type AssistState,
  type LocalAction,
} from "../state/assist";
import {
  askCard,
  heroCard,
  identityMsg,
  lockedCard,
  msg,
  PLAN_STEPS,
  planMsg,
  recordCard,
  toneTipCard,
  verifyCard,
} from "../test/assistFixtures";
import { ACK_MS, AssistColumn, SLOW_ASK_MS } from "./AssistColumn";

function state(...raws: Record<string, unknown>[]): AssistState {
  return raws.reduce((s, r) => applyAssist(s, parseAssistMessage(r)!, 10), emptyAssist());
}

const heroMsg = (over = {}) => msg("hero", { card: heroCard(over), reason: "new", replaces: null });

function setup(assist: AssistState) {
  const onAction = vi.fn<(a: LocalAction) => void>();
  const r = render(<AssistColumn assist={assist} onAction={onAction} firstName="Maria" />);
  const region = screen.getByRole("region", { name: /assist/i });
  return { ...r, onAction, region, hero: () => within(region).getByTestId("hero") };
}

afterEach(() => vi.useRealTimers());

describe("AssistColumn hero (DESIGN.md Assist column, docs/assist.md)", () => {
  it("listening: no suggestion yet", () => {
    const { hero } = setup(emptyAssist());
    expect(hero()).toHaveAttribute("data-state", "listening");
    expect(within(hero()).getByText("Listening with you")).toBeInTheDocument();
    expect(within(hero()).queryByRole("button", { name: /not now/i })).not.toBeInTheDocument();
  });

  it("listening: says when suggestions are held back by the rate limit", () => {
    const { hero } = setup(state(msg("assist_status", { state: "rate_limited", pending: 2 })));
    expect(within(hero()).getByText(/2 more suggestions/i)).toBeInTheDocument();
  });

  it("listening after an LLM error: says the model is unavailable and record cards still come", () => {
    const { hero } = setup(
      state(msg("assist_status", { state: "error", message: "assist model unavailable" }), msg("assist_status", { state: "listening" })),
    );
    expect(hero()).toHaveAttribute("data-state", "listening");
    expect(hero()).toHaveAttribute("data-degraded", "error");
    expect(within(hero()).getByRole("status")).toHaveTextContent(/suggestions are paused/i);
    expect(within(hero()).getByRole("status")).toHaveTextContent(/records and the call plan still update/i);
  });

  it("listening after the assist stopped: says so", () => {
    const { hero } = setup(state(msg("assist_status", { state: "stopped" })));
    expect(within(hero()).getByRole("status")).toHaveTextContent(/assist stopped/i);
  });

  it("suggestion: chip, AI suggested, trigger line, headline, why, Say this, action, source", () => {
    const { hero } = setup(state(heroMsg()));
    const h = hero();
    expect(h).toHaveAttribute("data-state", "suggestion");
    expect(h).toHaveAttribute("data-card-id", heroCard().id);
    expect(within(h).getByText("Next best step")).toBeInTheDocument();
    expect(within(h).getByText("AI suggested")).toBeInTheDocument();
    expect(within(h).getByText('Heard "denied"')).toBeInTheDocument();
    expect(within(h).getByRole("heading", { name: /Start a retro authorization review for 70412/ })).toBeInTheDocument();
    expect(within(h).getByText(/was denied CO-197 because no prior authorization/)).toBeInTheDocument();
    expect(within(h).getByText("Say this")).toBeInTheDocument();
    expect(within(h).getByText(/I can open a retro authorization review today/)).toBeInTheDocument();
    const primary = within(h).getByRole("button", { name: /Request retro auth review/ });
    expect(primary).toHaveClass("btn-primary");
    expect(within(h).getByTestId("hero-source")).toHaveAttribute("title", "Claims system, KB-CL-002 and KB-CL-009");
    expect(within(h).getByTestId("hero-source").textContent?.replace(/\u2011/g, "-")).toBe(
      "Claims system, KB-CL-002 and KB-CL-009",
    );
  });

  it("a card proposed from the whole conversation says so instead of a phrase", () => {
    const { hero } = setup(state(heroMsg({ trigger: { ...heroCard().trigger, phrase: null, start: null, end: null } })));
    expect(within(hero()).getByText("From the conversation")).toBeInTheDocument();
  });

  it("Show me why expands the trigger, the source records, and the record facts", () => {
    const { hero } = setup(state(msg("card_added", { card: recordCard() }), heroMsg()));
    const toggle = within(hero()).getByRole("button", { name: "Show me why" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(hero()).queryByTestId("hero-why-more")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    const more = within(hero()).getByTestId("hero-why-more");
    expect(within(hero()).getByRole("button", { name: "Hide why" })).toHaveAttribute("aria-expanded", "true");
    expect(within(more).getByText("So why did my MRI claim get")).toBeInTheDocument();
    expect(within(more).getByText("denied", { selector: "mark" })).toBeInTheDocument();
    expect(within(more).getByText("70412", { selector: "dd" })).toBeInTheDocument();
    expect(within(more).getByText("$1,240")).toBeInTheDocument();
    expect(within(more).getByText("Riverside Imaging Center")).toBeInTheDocument();
    expect(within(more).getByText(/claims · 70412/)).toBeInTheDocument();
  });

  it("the primary action accepts; Not now dismisses", () => {
    const { hero, onAction } = setup(state(heroMsg()));
    fireEvent.click(within(hero()).getByRole("button", { name: /Request retro auth review/ }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "accept", cardId: heroCard().id });
    fireEvent.click(within(hero()).getByRole("button", { name: /Not now/ }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "dismiss", cardId: heroCard().id });
  });

  it("accepted: phosphor check and the logged simulated action, then it settles", () => {
    vi.useFakeTimers();
    const s = localAssist(state(heroMsg()), { type: "accept", cardId: heroCard().id }, 130);
    const { hero, onAction } = setup(s);
    expect(hero()).toHaveAttribute("data-state", "accepted");
    expect(within(hero()).getByTestId("ack-check")).toBeInTheDocument();
    expect(within(hero()).getByText("Request retro auth review")).toBeInTheDocument();
    expect(within(hero()).getByText(/Logged as a simulated action/)).toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(ACK_MS));
    expect(onAction).toHaveBeenCalledWith({ type: "settle" });
  });

  it("dismissed: a short dismissed state, then it settles", () => {
    vi.useFakeTimers();
    const s = localAssist(state(heroMsg()), { type: "dismiss", cardId: heroCard().id }, 50);
    const { hero, onAction } = setup(s);
    expect(hero()).toHaveAttribute("data-state", "dismissed");
    expect(within(hero()).getByText(/Dismissed/)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(ACK_MS));
    expect(onAction).toHaveBeenCalledWith({ type: "settle" });
  });

  it("swaps in place: the hero box is the same element for every state", () => {
    const { hero, rerender, onAction } = setup(emptyAssist());
    const box = hero();
    const s = state(heroMsg());
    rerender(<AssistColumn assist={s} onAction={onAction} firstName="Maria" />);
    expect(hero()).toBe(box);
    rerender(
      <AssistColumn assist={localAssist(s, { type: "accept", cardId: heroCard().id }, 3)} onAction={onAction} firstName="Maria" />,
    );
    expect(hero()).toBe(box);
  });
});

describe("AssistColumn keyboard (Enter accepts, Esc dismisses)", () => {
  it("Enter accepts and Esc dismisses the hero", () => {
    const { onAction } = setup(state(heroMsg()));
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onAction).toHaveBeenLastCalledWith({ type: "accept", cardId: heroCard().id });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onAction).toHaveBeenLastCalledWith({ type: "dismiss", cardId: heroCard().id });
  });

  it("does nothing while listening, on key repeat, with modifiers, or inside a menu, field, or button", () => {
    const listening = setup(emptyAssist());
    fireEvent.keyDown(window, { key: "Enter" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(listening.onAction).not.toHaveBeenCalled();
    listening.unmount();

    const { onAction, region } = setup(state(heroMsg()));
    fireEvent.keyDown(window, { key: "Enter", repeat: true });
    fireEvent.keyDown(window, { key: "Enter", metaKey: true });
    fireEvent.keyDown(window, { key: "Escape", ctrlKey: true });
    const menu = document.createElement("div");
    menu.setAttribute("role", "menu");
    const item = document.createElement("button");
    menu.appendChild(item);
    document.body.appendChild(menu);
    fireEvent.keyDown(item, { key: "Escape" });
    fireEvent.keyDown(within(region).getByRole("button", { name: "Show me why" }), { key: "Enter" });
    menu.remove();
    expect(onAction).not.toHaveBeenCalled();
  });

  it("is not bound once the column is gone (call ended)", () => {
    const { onAction, unmount } = setup(state(heroMsg()));
    unmount();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onAction).not.toHaveBeenCalled();
  });
});

describe("AssistColumn shelf (supporting cards)", () => {
  it("record tile: one big answer, context, the rows as a table, AI suggested, source", () => {
    const { region } = setup(state(msg("card_added", { card: recordCard() })));
    const tile = within(region).getByTestId("tile-card:denied_claim");
    expect(tile).toHaveAttribute("data-kind", "record");
    expect(within(tile).getByText("Denied claim")).toBeInTheDocument();
    expect(within(tile).getByTestId("tile-big")).toHaveTextContent("$1,240");
    expect(within(tile).getByText("AI suggested")).toBeInTheDocument();
    expect(within(tile).getByTestId("tile-source")).toHaveTextContent("Claims system");
    expect(within(tile).getByText(/70412 MRI lumbar spine denied CO-197/)).toBeInTheDocument();
  });

  it("a multi row record shows a table of claims", () => {
    const rows = [
      { claim_id: "70412", service_date: "2026-10-28", billed_amount: 1240, status: "denied" },
      { claim_id: "70358", service_date: "2026-09-02", billed_amount: 180.5, status: "paid" },
    ];
    const card = recordCard({
      id: "card:claims_table",
      title: "Claims",
      body: { fields: ["claim_id", "service_date", "billed_amount", "status"], rows, summary: null },
    });
    const { region } = setup(state(msg("card_added", { card })));
    const table = within(within(region).getByTestId("tile-card:claims_table")).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("$180.50")).toBeInTheDocument();
    expect(within(table).getByText("Paid")).toBeInTheDocument();
  });

  it("alert tiles get the clay treatment; articles show the article title", () => {
    const alert = recordCard({
      id: "alert:duplicate_charge:PAY-1+PAY-2",
      kind: "alert",
      title: "Possible duplicate premium charge",
      template_id: "duplicate_charge",
      body: {
        fields: ["payment_id", "paid_at", "amount"],
        rows: [{ payment_id: "PAY-1", paid_at: "2026-11-02T10:00:00Z", amount: 312.4 }],
        summary: "PAY-1 drafted 312.4 on 2026-11-02, a second premium this month",
      },
    });
    const article = recordCard({
      id: "kb:KB-CL-002",
      kind: "article",
      title: "Knowledge base",
      template_id: "claims_article",
      body: {
        fields: ["article_id", "title", "summary"],
        rows: [{ article_id: "KB-CL-002", title: "Retro authorization reviews", summary: "When and how to request one." }],
        summary: null,
      },
    });
    const { region } = setup(state(msg("card_added", { card: alert }), msg("card_added", { card: article })));
    const a = within(region).getByTestId("tile-alert:duplicate_charge:PAY-1+PAY-2");
    expect(a).toHaveClass("is-alert");
    expect(within(a).getByTestId("tile-big")).toHaveTextContent("$312.40");
    const k = within(region).getByTestId("tile-kb:KB-CL-002");
    expect(within(k).getByTestId("tile-big")).toHaveTextContent("Retro authorization reviews");
    expect(within(k).getByText("When and how to request one.")).toBeInTheDocument();
  });

  it("pin, dismiss, and thumbs send feedback; pinned and rated states show", () => {
    const s0 = state(msg("card_added", { card: recordCard() }));
    const { region, onAction, rerender } = setup(s0);
    const tile = () => within(region).getByTestId("tile-card:denied_claim");
    fireEvent.click(within(tile()).getByRole("button", { name: "Pin" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "pin", cardId: "card:denied_claim" });
    fireEvent.click(within(tile()).getByRole("button", { name: "Helpful" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "rate", cardId: "card:denied_claim", rating: "up" });
    fireEvent.click(within(tile()).getByRole("button", { name: "Not helpful" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "rate", cardId: "card:denied_claim", rating: "down" });
    fireEvent.click(within(tile()).getByRole("button", { name: "Dismiss" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "dismiss", cardId: "card:denied_claim" });

    let s = localAssist(s0, { type: "pin", cardId: "card:denied_claim" }, 1);
    s = localAssist(s, { type: "rate", cardId: "card:denied_claim", rating: "up" }, 1);
    rerender(<AssistColumn assist={s} onAction={onAction} firstName="Maria" />);
    expect(within(tile()).getByRole("button", { name: "Unpin" })).toHaveAttribute("aria-pressed", "true");
    expect(within(tile()).getByRole("button", { name: "Helpful" })).toHaveAttribute("aria-pressed", "true");
    expect(within(tile()).getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(within(tile()).getByRole("button", { name: "Unpin" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "unpin", cardId: "card:denied_claim" });
  });

  it("pinned cards come first; new cards are added after the ones on screen", () => {
    const second = recordCard({ id: "kb:KB-CL-002", kind: "article", title: "Knowledge base" });
    let s = state(msg("card_added", { card: recordCard() }), msg("card_added", { card: second }));
    s = localAssist(s, { type: "pin", cardId: "kb:KB-CL-002" }, 1);
    const { region } = setup(s);
    const ids = within(region)
      .getAllByTestId(/^tile-(card|kb|alert|nbs)/)
      .map((t) => t.dataset.testid);
    expect(ids).toEqual(["tile-kb:KB-CL-002", "tile-card:denied_claim"]);
  });

  it("the call plan keeps its slot as cards arrive (nothing shifts)", () => {
    const second = recordCard({ id: "kb:KB-CL-002", kind: "article", title: "Knowledge base" });
    const slots = (s: AssistState) => {
      const { region, unmount } = setup(s);
      const kids = [...within(region).getByText("Call plan").closest(".shelf")!.children] as HTMLElement[];
      const out = kids.map((k) => k.dataset.testid);
      unmount();
      return out;
    };
    expect(slots(state(planMsg()))).toEqual(["shelf-tile", "tile-plan", "shelf-tile"]);
    expect(slots(state(planMsg(), msg("card_added", { card: recordCard() })))).toEqual([
      "tile-card:denied_claim",
      "tile-plan",
      "shelf-tile",
    ]);
    expect(
      slots(state(planMsg(), msg("card_added", { card: recordCard() }), msg("card_added", { card: second }))),
    ).toEqual(["tile-card:denied_claim", "tile-plan", "tile-kb:KB-CL-002"]);
  });

  it("empty shelf keeps the Studio placeholders", () => {
    const { region } = setup(emptyAssist());
    expect(within(region).getByText("Records")).toBeInTheDocument();
    expect(within(region).getByText("Call plan")).toBeInTheDocument();
    expect(within(region).getByText("Tone tip")).toBeInTheDocument();
  });

  it("tone tip tile: bakelite swatch, one big answer, why, words only source (AP-14)", () => {
    const { region, onAction } = setup(state(planMsg(), msg("card_added", { card: toneTipCard() })));
    const tile = within(region).getByTestId("tile-tone:caller-4");
    expect(tile).toHaveAttribute("data-kind", "tone_tip");
    expect(within(tile).getByText("Tone tip")).toBeInTheDocument();
    expect(within(tile).getByTestId("tile-big")).toHaveTextContent("Acknowledge, then act");
    expect(within(tile).getByText(/Name the problem in their words/)).toBeInTheDocument();
    expect(tile.querySelector(".sw")?.getAttribute("style")).toMatch(/bakelite/);
    expect(within(tile).getByTitle(/Pipecat Tone reader, words only/)).toBeInTheDocument();
    fireEvent.click(within(tile).getByRole("button", { name: "Dismiss" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "dismiss", cardId: "tone:caller-4" });
  });

  it("a tone tip takes the third slot: no placeholder next to it", () => {
    const { region } = setup(
      state(planMsg(), msg("card_added", { card: recordCard() }), msg("card_added", { card: toneTipCard() })),
    );
    const kids = [...within(region).getByText("Call plan").closest(".shelf")!.children] as HTMLElement[];
    expect(kids.map((k) => k.dataset.testid)).toEqual(["tile-card:denied_claim", "tile-plan", "tile-tone:caller-4"]);
  });

  it("a tone tip that arrives first still takes the tone slot, never the records slot", () => {
    const slots = (s: AssistState) => {
      const { region, unmount } = setup(s);
      const kids = [...within(region).getByText("Call plan").closest(".shelf")!.children] as HTMLElement[];
      const out = kids.map((k) => k.dataset.testid);
      const tips = within(region).getAllByText("Tone tip").length;
      unmount();
      return { out, tips };
    };
    const tipOnly = state(planMsg(), msg("card_added", { card: toneTipCard() }));
    expect(slots(tipOnly)).toEqual({ out: ["shelf-tile", "tile-plan", "tile-tone:caller-4"], tips: 1 });
    const tipThenRecord = state(
      planMsg(),
      msg("card_added", { card: toneTipCard() }),
      msg("card_added", { card: recordCard() }),
    );
    expect(slots(tipThenRecord).out).toEqual(["tile-card:denied_claim", "tile-plan", "tile-tone:caller-4"]);
  });
});

describe("AssistColumn call plan (AP-12)", () => {
  it("checklist with progress; the next step has the ring; auto ticks show as done", () => {
    const ticked = PLAN_STEPS.map((p, i) => (i === 0 ? { ...p, done: true, by: "transcript" as const } : p));
    const { region } = setup(state(planMsg(ticked)));
    const plan = within(region).getByTestId("tile-plan");
    expect(within(plan).getByText("1 of 4")).toBeInTheDocument();
    const boxes = within(plan).getAllByRole("checkbox");
    expect(boxes).toHaveLength(4);
    expect(boxes[0]).toBeChecked();
    expect(boxes[0].closest("li")).toHaveClass("is-done");
    expect(boxes[1].closest("li")).toHaveClass("is-next");
    expect(boxes[2].closest("li")).not.toHaveClass("is-next");
  });

  it("manual tick and untick", () => {
    const first = setup(state(planMsg()));
    const boxes = within(within(first.region).getByTestId("tile-plan")).getAllByRole("checkbox");
    fireEvent.click(boxes[1]);
    expect(first.onAction).toHaveBeenLastCalledWith({ type: "tick", stepId: "explain_status", done: true });
    first.unmount();
    const ticked = PLAN_STEPS.map((p, i) => (i === 0 ? { ...p, done: true, by: "action" as const } : p));
    const { region, onAction } = setup(state(planMsg(ticked)));
    fireEvent.click(within(within(region).getByTestId("tile-plan")).getAllByRole("checkbox")[0]);
    expect(onAction).toHaveBeenLastCalledWith({ type: "tick", stepId: "confirm_claim", done: false });
  });
});

describe("AssistColumn Ask bar (P4-6, AP-8)", () => {
  it("Cmd+K or Ctrl+K focuses the Ask field; Enter sends the question and clears it", () => {
    const { region, onAction } = setup(state(heroMsg()));
    const input = within(region).getByRole("textbox", { name: "Ask" });
    expect(input).toBeEnabled();
    expect(input).toHaveAttribute("placeholder", "Ask about Maria");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(input).toHaveFocus();
    input.blur();
    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "Show the last 3 claims" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenLastCalledWith({ type: "ask", askId: expect.any(String), text: "Show the last 3 claims" });
    expect(input).toHaveValue("");
  });

  it("Enter in the Ask field never accepts the hero; Esc leaves the field without dismissing it", () => {
    const { region, onAction } = setup(state(heroMsg()));
    const input = within(region).getByRole("textbox", { name: "Ask" });
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.keyDown(input, { key: "Enter" }); // empty: nothing
    fireEvent.change(input, { target: { value: "half a question" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onAction).not.toHaveBeenCalled();
    expect(input).not.toHaveFocus();
    expect(input).toHaveValue("");
  });

  it("Ctrl+K hint on Windows and Linux, ⌘ K on Mac (P5-3)", () => {
    const nav = vi.spyOn(window.navigator, "platform", "get");
    nav.mockReturnValue("Win32");
    const win = setup(emptyAssist());
    expect(within(win.region).getByTestId("ask-kbd")).toHaveTextContent("Ctrl K");
    win.unmount();
    nav.mockReturnValue("MacIntel");
    const mac = setup(emptyAssist());
    expect(within(mac.region).getByTestId("ask-kbd")).toHaveTextContent("⌘ K");
    nav.mockRestore();
  });

  it("a slow lookup says it is still looking (slow connector, P5-3)", () => {
    vi.useFakeTimers();
    const s = localAssist(state(planMsg()), { type: "ask", askId: "q1", text: "Show the last 3 claims" }, 20);
    const { region } = setup(s);
    expect(within(region).getByRole("status")).toHaveTextContent(/Looking up “Show the last 3 claims”/);
    expect(within(region).getByRole("status")).not.toHaveTextContent(/still looking/i);
    act(() => {
      vi.advanceTimersByTime(SLOW_ASK_MS + 10);
    });
    expect(within(region).getByRole("status")).toHaveTextContent(/still looking/i);
  });

  it("shows the question while it is answered, and the reason when there is no answer", () => {
    let s = state(planMsg());
    s = localAssist(s, { type: "ask", askId: "q1", text: "Show the last 3 claims" }, 20);
    const pending = setup(s);
    expect(within(pending.region).getByRole("status")).toHaveTextContent(/Looking up “Show the last 3 claims”/);
    pending.unmount();
    s = applyAssist(
      s,
      parseAssistMessage(msg("ask_result", { ask_id: "q1", status: "no_answer", card_id: null, message: "No claims found for this caller." }))!,
      21,
    );
    const done = setup(s);
    expect(within(done.region).getByRole("status")).toHaveTextContent("No claims found for this caller.");
  });

  it("an answer the agent asked for is scrolled into view on the shelf, once", () => {
    const into = vi.fn();
    Element.prototype.scrollIntoView = into;
    let s = state(planMsg(), msg("card_added", { card: recordCard() }));
    s = localAssist(s, { type: "ask", askId: "ask-1", text: "Show the last 3 claims" }, 20);
    const onAction = vi.fn();
    const r = render(<AssistColumn assist={s} onAction={onAction} firstName="Maria" />);
    expect(into).not.toHaveBeenCalled();
    s = applyAssist(s, parseAssistMessage(msg("card_added", { card: askCard() }))!, 21);
    s = applyAssist(
      s,
      parseAssistMessage(msg("ask_result", { ask_id: "ask-1", status: "answered", card_id: "ask:ask-1", message: null }))!,
      21,
    );
    r.rerender(<AssistColumn assist={s} onAction={onAction} firstName="Maria" />);
    expect(into).toHaveBeenCalledTimes(1);
    expect(into.mock.contexts[0]).toBe(screen.getByTestId("tile-ask:ask-1"));
    r.rerender(<AssistColumn assist={{ ...s }} onAction={onAction} firstName="Maria" />);
    expect(into).toHaveBeenCalledTimes(1);
  });

  it("an answer is a supporting card that names the question and its source", () => {
    let s = state(planMsg());
    s = localAssist(s, { type: "ask", askId: "ask-1", text: "Show the last 3 claims" }, 20);
    s = applyAssist(s, parseAssistMessage(msg("card_added", { card: askCard() }))!, 21);
    s = applyAssist(
      s,
      parseAssistMessage(msg("ask_result", { ask_id: "ask-1", status: "answered", card_id: "ask:ask-1", message: null }))!,
      21,
    );
    const { region } = setup(s);
    const tile = within(region).getByTestId("tile-ask:ask-1");
    expect(within(tile).getByText("Claims")).toBeInTheDocument();
    expect(within(tile).getByText("You asked “Show the last 3 claims”")).toBeInTheDocument();
    expect(within(tile).getByTestId("tile-big")).toHaveTextContent("2 claims");
    expect(within(tile).getAllByRole("row")).toHaveLength(3);
    expect(within(tile).getByTestId("tile-source")).toHaveTextContent("Claims system");
    expect(within(tile).getByTestId("tile-source")).toHaveAttribute("title", "70412, 70358");
    expect(within(region).queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("AssistColumn Earlier this call", () => {
  it("lists accepted actions and steps done with their call time", () => {
    let s = state(planMsg(), heroMsg());
    s = localAssist(s, { type: "accept", cardId: heroCard().id }, 130);
    s = localAssist(s, { type: "tick", stepId: "confirm_claim", done: true }, 48);
    const { region } = setup(s);
    const hist = within(region).getByRole("list", { name: "Earlier this call" });
    const rows = within(hist).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/Request retro auth review.*Simulated · 70412.*02:10/),
      expect.stringMatching(/Confirm which claim the caller means.*00:48/),
    ]);
  });

  it("says what will show up before anything is done", () => {
    const { region } = setup(emptyAssist());
    expect(within(region).getByText("Steps you finish show up here.")).toBeInTheDocument();
  });

  it("lists the agent's identity decision (IN-13)", () => {
    let s = state(identityMsg("pending"), verifyMsg());
    s = localAssist(s, { type: "verify", cardId: "verify:identity", verified: true }, 75);
    const { region } = setup(s);
    const hist = within(region).getByRole("list", { name: "Earlier this call" });
    expect(within(hist).getByRole("listitem").textContent).toMatch(/Caller verified.*By you.*01:15/);
  });
});

const verifyMsg = (over = {}, result = "close_match") =>
  msg("hero", { card: verifyCard(over, result), reason: "new", replaces: null });

describe("AssistColumn verify hero (IN-13)", () => {
  it("headline, what matched and what did not, Say this, and the two decisions", () => {
    const { hero } = setup(state(identityMsg("pending"), verifyMsg()));
    const h = hero();
    expect(h).toHaveAttribute("data-state", "verify");
    expect(h).toHaveAttribute("data-result", "close_match");
    expect(within(h).getByRole("heading", { name: /Confirm who you're speaking with/ })).toBeInTheDocument();
    const items = within(h).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringMatching(/Member ID.*Match/),
      expect.stringMatching(/Date of birth.*Match/),
      expect.stringMatching(/Name.*Heard “Linda Yuen”.*On file “Linda Nguyen”/),
    ]);
    expect(items[2]).toHaveAttribute("data-ok", "false");
    expect(within(h).getByText("Say this")).toBeInTheDocument();
    expect(within(h).getByText(/spell your last name/)).toBeInTheDocument();
    expect(within(h).getByRole("button", { name: "Mark verified" })).toHaveClass("btn-primary");
    expect(within(h).getByRole("button", { name: "Could not verify" })).not.toHaveClass("btn-primary");
    // Not an AI suggestion, and no Not now: only the agent's decision closes it.
    expect(within(h).queryByText("AI suggested")).not.toBeInTheDocument();
    expect(within(h).queryByRole("button", { name: /not now/i })).not.toBeInTheDocument();
  });

  it("not found uses the clay treatment", () => {
    const { hero } = setup(state(identityMsg("pending", { result: "not_found" }), verifyMsg({}, "not_found")));
    expect(hero()).toHaveAttribute("data-result", "not_found");
    expect(hero()).toHaveClass("is-not-found");
  });

  it("Mark verified and Could not verify send the decision", () => {
    const { hero, onAction } = setup(state(identityMsg("pending"), verifyMsg()));
    fireEvent.click(within(hero()).getByRole("button", { name: "Mark verified" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "verify", cardId: "verify:identity", verified: true });
    fireEvent.click(within(hero()).getByRole("button", { name: "Could not verify" }));
    expect(onAction).toHaveBeenLastCalledWith({ type: "verify", cardId: "verify:identity", verified: false });
  });

  it("Enter marks verified; Esc does nothing (no dismiss)", () => {
    const { onAction } = setup(state(identityMsg("pending"), verifyMsg()));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onAction).toHaveBeenCalledWith({ type: "verify", cardId: "verify:identity", verified: true });
  });

  it("verified ack: phosphor check and Verified by you", () => {
    vi.useFakeTimers();
    let s = state(identityMsg("pending"), verifyMsg());
    s = localAssist(s, { type: "verify", cardId: "verify:identity", verified: true }, 20);
    const { hero } = setup(s);
    expect(hero()).toHaveAttribute("data-state", "verified");
    expect(within(hero()).getByTestId("ack-check")).toBeInTheDocument();
    expect(within(hero()).getByText(/Records are unlocked/)).toBeInTheDocument();
  });

  it("could not verify ack: says records stay hidden and to offer a callback", () => {
    let s = state(identityMsg("pending"), verifyMsg());
    s = localAssist(s, { type: "verify", cardId: "verify:identity", verified: false }, 20);
    const { hero } = setup(s);
    expect(hero()).toHaveAttribute("data-state", "not_verified");
    expect(within(hero()).getByText(/Records stay hidden for this call/)).toBeInTheDocument();
  });
});

describe("AssistColumn locked tiles (IN-13)", () => {
  it("a locked record is a stone tile with a lock: no values, no source records", () => {
    const { region } = setup(state(identityMsg("pending"), verifyMsg(), msg("card_added", { card: lockedCard() })));
    const tile = within(region).getByTestId(`tile-${lockedCard().id}`);
    expect(tile).toHaveClass("is-locked");
    expect(tile).toHaveAttribute("data-locked", "true");
    expect(within(tile).getByText("Verify the caller to see records")).toBeInTheDocument();
    expect(within(tile).getByTestId("tile-lock")).toBeInTheDocument();
    expect(tile.textContent).not.toMatch(/70412|MRI|\$/);
    // No pin, thumbs, or dismiss on a tile the agent cannot read.
    expect(within(tile).queryByRole("button")).not.toBeInTheDocument();
  });

  it("an Ask answer is withheld the same way", () => {
    let s = localAssist(state(identityMsg("pending"), verifyMsg()), { type: "ask", askId: "ask-1", text: "Show the last 3 claims" }, 5);
    s = applyAssist(s, parseAssistMessage(msg("card_added", { card: lockedCard({ id: "ask:ask-1", trigger: askCard().trigger }) }))!, 6);
    s = applyAssist(s, parseAssistMessage(msg("ask_result", { ask_id: "ask-1", status: "answered", card_id: "ask:ask-1", message: null }))!, 6);
    const { region } = setup(s);
    const tile = within(region).getByTestId("tile-ask:ask-1");
    expect(within(tile).getByText("Verify the caller to see records")).toBeInTheDocument();
    expect(within(tile).getByText("You asked “Show the last 3 claims”")).toBeInTheDocument();
    expect(tile.textContent).not.toMatch(/70412/);
  });

  it("the empty records slot says records wait for verification; articles and the plan still show", () => {
    const article = recordCard({
      id: "kb:KB-CL-010",
      kind: "article",
      title: "Knowledge base",
      body: { fields: ["article_id", "title", "summary"], rows: [{ article_id: "KB-CL-010", title: "Reading an EOB", summary: "Not a bill." }], summary: null },
    });
    const { region } = setup(state(identityMsg("pending"), verifyMsg(), planMsg(), msg("card_added", { card: article })));
    expect(within(region).getByTestId("tile-kb:KB-CL-010")).not.toHaveClass("is-locked");
    expect(within(region).getByTestId("tile-plan")).toBeInTheDocument();
  });

  it("once verified, the unlocked record replaces the locked tile", () => {
    let s = state(identityMsg("pending"), verifyMsg(), msg("card_added", { card: lockedCard() }));
    s = applyAssist(s, parseAssistMessage(identityMsg("verified", { by: "Ana", at: 1 }))!, 9);
    s = applyAssist(s, parseAssistMessage(msg("card_removed", { card_id: lockedCard().id, reason: "unlocked" }))!, 9);
    s = applyAssist(s, parseAssistMessage(msg("card_added", { card: recordCard() }))!, 9);
    const { region } = setup(s);
    expect(within(region).queryByText("Verify the caller to see records")).not.toBeInTheDocument();
    expect(within(region).getByTestId("tile-card:denied_claim")).toHaveTextContent("70412");
  });
});
