import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import contentSource from "../architecture/content.ts?raw";
import { NODES } from "../architecture/content";
import { Architecture } from "./Architecture";
import type { ShellAgent } from "./Shell";

const shell: ShellAgent = {
  name: "Ana",
  online: false,
  theme: "light",
  onTheme: () => {},
  onSignOut: () => {},
  page: "architecture",
  onPage: () => {},
};

function setup(over: Partial<Parameters<typeof Architecture>[0]> = {}) {
  const user = userEvent.setup();
  const exportPng = vi.fn().mockResolvedValue(undefined);
  render(<Architecture shell={shell} exportPng={exportPng} {...over} />);
  return { user, exportPng };
}

const node = (name: RegExp | string) => screen.getByRole("button", { name });
const panel = () => screen.getByRole("complementary", { name: /details/i });

describe("Architecture page (AP-21)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("is a page of its own with the rail's Architecture item current", () => {
    setup();
    expect(screen.getByRole("region", { name: "Architecture" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "How Deepgram Vitals works" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Architecture" })).toHaveAttribute("aria-current", "page");
  });

  describe("Business / Technical toggle", () => {
    it("starts in Business with plain names and no protocol labels", () => {
      setup();
      expect(screen.getByRole("radio", { name: "Business" })).toBeChecked();
      expect(node(/^Intake listening/)).toBeInTheDocument();
      expect(node(/^Assist listening/)).toBeInTheDocument();
      expect(screen.queryByText("SIP / WebRTC")).not.toBeInTheDocument();
      expect(screen.queryByText("Deepgram STT, Nova-3 or Flux")).not.toBeInTheDocument();
    });

    it("Technical shows services, models, and edge labels", async () => {
      const { user } = setup();
      await user.click(screen.getByRole("radio", { name: "Technical" }));
      expect(screen.getByRole("radio", { name: "Technical" })).toBeChecked();
      expect(node(/^Deepgram STT, Nova-3 or Flux/)).toBeInTheDocument();
      expect(node(/^Deepgram Nova-3 STT/)).toBeInTheDocument();
      expect(node(/^Deepgram Flux TTS/)).toBeInTheDocument();
      expect(screen.getByText("SIP / WebRTC")).toBeInTheDocument();
      expect(screen.getByText("RTVI cards + transcript")).toBeInTheDocument();
      expect(screen.getByText("Pipecat 1.15 bot, one process per call")).toBeInTheDocument();
    });
  });

  describe("node panel", () => {
    it("click a node: what it does, why (3), measured numbers, docs; outlined; Esc closes", async () => {
      const { user } = setup();
      await user.click(node(/^Assist listening/));
      const p = panel();
      expect(within(p).getByRole("heading", { name: "Assist listening" })).toBeInTheDocument();
      expect(within(p).getByText(/transcribed on their own tracks/)).toBeInTheDocument();
      expect(within(p).getAllByRole("listitem")).toHaveLength(3);
      expect(within(p).getByText("0.44 s")).toBeInTheDocument();
      expect(within(p).getByText("51 of 51")).toBeInTheDocument();
      expect(within(p).getByRole("link", { name: /Keyterm prompting/ })).toHaveAttribute(
        "href",
        "https://developers.deepgram.com/docs/keyterm",
      );
      expect(node(/^Assist listening/)).toHaveAttribute("aria-pressed", "true");
      expect(node(/^Assist listening/)).toHaveClass("is-selected");

      await user.keyboard("{Escape}");
      expect(node(/^Assist listening/)).toHaveAttribute("aria-pressed", "false");
      expect(within(panel()).getByRole("tablist")).toBeInTheDocument();
    });

    it("the close button and a second click also close it", async () => {
      const { user } = setup();
      await user.click(node(/^Daily/));
      await user.click(within(panel()).getByRole("button", { name: /close/i }));
      expect(node(/^Daily/)).toHaveAttribute("aria-pressed", "false");
      await user.click(node(/^Daily/));
      await user.click(node(/^Daily/));
      expect(node(/^Daily/)).toHaveAttribute("aria-pressed", "false");
    });

    it("nodes are reachable with the keyboard", async () => {
      const { user } = setup();
      node(/^Caller/).focus();
      await user.keyboard("{Enter}");
      expect(within(panel()).getByRole("heading", { name: "Caller" })).toBeInTheDocument();
    });

    it("the technical view shows the technical text and key facts", async () => {
      const { user } = setup();
      await user.click(screen.getByRole("radio", { name: "Technical" }));
      await user.click(node(/^Supabase Postgres/));
      const p = panel();
      expect(within(p).getByRole("heading", { name: "Supabase Postgres" })).toBeInTheDocument();
      expect(within(p).getByText("Row level security on every table")).toBeInTheDocument();
    });
  });

  describe("sections when nothing is selected", () => {
    it("tabs for data and privacy, prospect questions, and PoC vs production", async () => {
      const { user } = setup();
      const tabs = within(panel()).getAllByRole("tab");
      expect(tabs.map((t) => t.textContent)).toEqual(["Data and privacy", "Prospect questions", "PoC vs production"]);
      expect(within(panel()).getByText("No audio recording")).toBeInTheDocument();

      await user.click(screen.getByRole("tab", { name: "Prospect questions" }));
      expect(screen.getByRole("tab", { name: "Prospect questions" })).toHaveAttribute("aria-selected", "true");
      expect(within(panel()).getByText("Can Deepgram run in our own cloud (self hosted)?")).toBeInTheDocument();

      await user.click(screen.getByRole("tab", { name: "PoC vs production" }));
      const table = within(panel()).getByRole("table");
      expect(within(table).getByText("Pipecat Cloud, or your own cloud")).toBeInTheDocument();
      expect(within(table).getByText("Your carrier, connected to Daily over SIP")).toBeInTheDocument();
    });
  });

  describe("walk a call", () => {
    it("9 steps; arrow keys and buttons move; active parts lit, the rest dimmed", async () => {
      const { user } = setup();
      await user.click(screen.getByRole("button", { name: /walk a call/i }));
      const bar = screen.getByRole("list", { name: /call steps/i });
      expect(within(bar).getAllByRole("button")).toHaveLength(9);
      expect(within(panel()).getByText("Step 1 of 9")).toBeInTheDocument();
      expect(within(panel()).getByRole("heading", { name: "Dial in" })).toBeInTheDocument();

      for (let i = 0; i < 6; i++) await user.keyboard("{ArrowRight}");
      expect(within(panel()).getByText("Step 7 of 9")).toBeInTheDocument();
      expect(within(panel()).getByRole("heading", { name: "Live assist" })).toBeInTheDocument();
      expect(within(panel()).getByText("0.88 to 1.61 s")).toBeInTheDocument();
      expect(within(panel()).getByText("0.3 s")).toBeInTheDocument();
      expect(within(bar).getByRole("button", { name: /7\. Live assist/ })).toHaveAttribute("aria-current", "step");

      expect(node(/^Assist engine/)).toHaveClass("is-lit");
      expect(node(/^Agent portal/)).toHaveClass("is-lit");
      expect(node(/^Caller/)).toHaveClass("is-dim");
      expect(node(/^Tone reader/)).toHaveClass("is-dim");
      expect(document.querySelector('[data-edge="assist-portal"]')).toHaveClass("is-lit");
      expect(document.querySelector('[data-edge="caller-daily"]')).toHaveClass("is-dim");

      await user.keyboard("{ArrowLeft}");
      expect(within(panel()).getByText("Step 6 of 9")).toBeInTheDocument();
      await user.click(within(panel()).getByRole("button", { name: /next: live assist/i }));
      expect(within(panel()).getByText("Step 7 of 9")).toBeInTheDocument();
      await user.click(within(panel()).getByRole("button", { name: /back: handoff/i }));
      expect(within(panel()).getByText("Step 6 of 9")).toBeInTheDocument();
      await user.click(within(bar).getByRole("button", { name: /9\. Done/ }));
      expect(within(panel()).getByText("Step 9 of 9")).toBeInTheDocument();
      await user.keyboard("{ArrowRight}");
      expect(within(panel()).getByText("Step 9 of 9")).toBeInTheDocument();
    });

    it("Esc leaves the walk", async () => {
      const { user } = setup();
      await user.click(screen.getByRole("button", { name: /walk a call/i }));
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("list", { name: /call steps/i })).not.toBeInTheDocument();
      expect(node(/^Caller/)).not.toHaveClass("is-dim");
    });
  });

  describe("export and present", () => {
    it("Export hands the diagram to the exporter with a file name for the view", async () => {
      const { user, exportPng } = setup();
      await user.click(screen.getByRole("radio", { name: "Technical" }));
      await user.click(screen.getByRole("button", { name: /export/i }));
      expect(exportPng).toHaveBeenCalledTimes(1);
      const [el, name] = exportPng.mock.calls[0];
      expect(el).toBeInstanceOf(HTMLElement);
      expect(el).toContainElement(node(/^Daily/));
      expect(name).toBe("vitals-architecture-technical.png");
    });

    it("Present goes full screen without the rail and header, and keeps the walk", async () => {
      const requestFullscreen = vi.fn().mockResolvedValue(undefined);
      const exitFullscreen = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(document.documentElement, "requestFullscreen", {
        value: requestFullscreen,
        configurable: true,
      });
      Object.defineProperty(document, "exitFullscreen", { value: exitFullscreen, configurable: true });
      const { user } = setup();
      await user.click(screen.getByRole("button", { name: /walk a call/i }));
      await user.click(screen.getByRole("button", { name: /^present/i }));
      expect(requestFullscreen).toHaveBeenCalled();
      expect(screen.queryByRole("navigation", { name: /main/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("banner")).not.toBeInTheDocument();
      expect(screen.getByRole("list", { name: /call steps/i })).toBeInTheDocument();
      await user.keyboard("{ArrowRight}");
      expect(within(panel()).getByText("Step 2 of 9")).toBeInTheDocument();

      // Leaving full screen (Esc in the browser) ends present mode.
      Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });
      act(() => {
        document.dispatchEvent(new Event("fullscreenchange"));
      });
      expect(screen.getByRole("navigation", { name: /main/i })).toBeInTheDocument();
    });

    it("Present works without the Fullscreen API too", async () => {
      Object.defineProperty(document.documentElement, "requestFullscreen", { value: undefined, configurable: true });
      const { user } = setup();
      await user.click(screen.getByRole("button", { name: /^present/i }));
      expect(screen.queryByRole("navigation", { name: /main/i })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /exit present/i }));
      expect(screen.getByRole("navigation", { name: /main/i })).toBeInTheDocument();
    });
  });

  describe("external links open in a new tab", () => {
    const external = () => [...document.querySelectorAll<HTMLAnchorElement>("a[href]")].filter((a) => /^https?:/.test(a.getAttribute("href")!));

    function expectNewTab(links: HTMLAnchorElement[]) {
      for (const a of links) {
        expect(a.getAttribute("target"), a.href).toBe("_blank");
        expect(a.getAttribute("rel"), a.href).toBe("noopener noreferrer");
      }
    }

    it("every http link on every panel, section, and step of the page", async () => {
      const { user } = setup();
      const seen = new Set<string>();
      const collect = () => {
        const links = external();
        expectNewTab(links);
        links.forEach((a) => seen.add(a.getAttribute("href")!));
      };
      for (const view of ["Business", "Technical"]) {
        await user.click(screen.getByRole("radio", { name: view }));
        for (const n of NODES) {
          fireEvent.click(document.querySelector(`[data-node="${n.id}"]`)!);
          collect();
          await user.keyboard("{Escape}");
        }
        for (const tab of ["Data and privacy", "Prospect questions", "PoC vs production"]) {
          await user.click(screen.getByRole("tab", { name: tab }));
          collect();
        }
      }
      await user.click(screen.getByRole("button", { name: /walk a call/i }));
      for (let i = 0; i < 9; i++) {
        collect();
        await user.keyboard("{ArrowRight}");
      }
      // Every URL in the content file was rendered (and so checked above).
      const inContent = new Set(contentSource.match(/https?:\/\/[^"'`\s)]+/g) ?? []);
      expect(inContent.size).toBeGreaterThan(20);
      for (const url of inContent) expect(seen, url).toContain(url);
    });

    it("no component writes a raw http anchor: they all go through ExternalLink", () => {
      const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<
        string,
        string
      >;
      for (const [path, src] of Object.entries(sources)) {
        if (path.endsWith(".test.tsx") || path.endsWith("ExternalLink.tsx")) continue;
        for (const m of src.matchAll(/<a\b[^>]*>/g)) {
          const tag = m[0];
          // Same site links (a path or a query) are fine; anything else must open a new tab.
          if (/href=(["'`]|\{`)[/?]/.test(tag)) continue;
          expect(tag, path).toMatch(/target="_blank"/);
          expect(tag, path).toMatch(/rel="noopener noreferrer"/);
        }
      }
    });
  });
});
