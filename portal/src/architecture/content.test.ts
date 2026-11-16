import { describe, expect, it } from "vitest";
import { EDGES, FAQ, MEASURED, NODES, POC_VS_PROD, PRIVACY, WALK, allLinks, allStats } from "./content";

/** Our measurement docs, read as text so every number can be traced to its source. */
const DOCS = import.meta.glob("../../../docs/**/*.md", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;
const doc = (path: string) => DOCS[`../../../${path}`];
const flat = (s: string) => s.replace(/\s+/g, " ");

/** The doc URLs verified on Nov 16 (card t_c23e60a2); no other external link is allowed. */
const VERIFIED = new Set([
  "https://developers.deepgram.com/docs/models-languages-overview",
  "https://developers.deepgram.com/docs/keyterm",
  "https://developers.deepgram.com/docs/flux-tts/quickstart",
  "https://developers.deepgram.com/docs/flux/quickstart",
  "https://developers.deepgram.com/docs/text-to-speech",
  "https://developers.deepgram.com/docs/live-streaming-audio",
  "https://developers.deepgram.com/docs/numerals",
  "https://developers.deepgram.com/docs/self-hosted-introduction",
  "https://deepgram.com/product/speech-to-text",
  "https://deepgram.com/product/text-to-speech",
  "https://docs.pipecat.ai/overview/introduction",
  "https://docs.pipecat.ai/pipecat/telephony/daily-pstn",
  "https://docs.pipecat.ai/pipecat/flows/introduction",
  "https://docs.pipecat.ai/client/introduction",
  "https://docs.pipecat.ai/api-reference/server/services/stt/deepgram",
  "https://docs.pipecat.ai/api-reference/server/services/tts/deepgram",
  "https://docs.pipecat.ai/pipecat-cloud/introduction",
  "https://docs.daily.co/docs/guides/features/dial-in-dial-out",
  "https://supabase.com/docs/guides/database/postgres/row-level-security",
  "https://supabase.com/docs/guides/database/connecting-to-postgres",
  "https://docs.fly.io",
  "https://platform.claude.com/docs/en/models/overview",
  "https://github.com/pipecat-ai/pipecat",
]);

describe("architecture content (AP-21)", () => {
  it("has the diagram's nodes, with the STT split into intake (Nova-3, Flux selectable) and assist (Nova-3)", () => {
    expect(NODES.map((n) => n.id)).toEqual([
      "caller",
      "daily",
      "intake-stt",
      "assist-stt",
      "tone",
      "flows",
      "assist",
      "tts",
      "server",
      "portal",
      "db",
    ]);
    const t = (id: string) => NODES.find((n) => n.id === id)!.technical.title;
    // P5-7: Nova-3 is the intake default; Flux is selectable (docs/spikes/stt-flux-intake.md).
    expect(t("intake-stt")).toBe("Deepgram STT, Nova-3 or Flux");
    expect(NODES.find((n) => n.id === "intake-stt")!.technical.lead).toMatch(/Nova-3 by default/);
    expect(t("assist-stt")).toMatch(/Nova-3/);
    expect(t("tts")).toMatch(/Flux TTS/);
    expect(NODES.find((n) => n.id === "tts")!.technical.sub).toMatch(/flux-alexis-en/);
  });

  it("gives every node business and technical text, three reasons, and doc links", () => {
    for (const n of NODES) {
      for (const v of [n.business, n.technical]) {
        expect(v.title, n.id).not.toBe("");
        expect(v.sub, n.id).not.toBe("");
        // "What it does": two sentences.
        expect(v.lead.match(/[.!?](\s|$)/g)?.length, `${n.id}: ${v.lead}`).toBe(2);
      }
      expect(n.why, n.id).toHaveLength(3);
      expect(n.links.length, n.id).toBeGreaterThan(0);
    }
  });

  it("traces every measured number to the doc it came from", () => {
    const stats = allStats();
    expect(stats.length).toBeGreaterThan(20);
    for (const s of stats) {
      const text = doc(s.source);
      expect(text, `${s.value}: ${s.source} exists`).toBeDefined();
      const evidence = Array.isArray(s.evidence) ? s.evidence : [s.evidence];
      for (const e of evidence) expect(flat(text), `${s.value} "${s.label}" <- ${s.source}`).toContain(flat(e));
    }
  });

  it("uses the current headline numbers", () => {
    const values = allStats().map((s) => s.value);
    for (const v of ["0.44 s", "0.88 to 1.61 s", "0.3 s", "10 of 10", "51 of 51", "80 of 80"]) {
      expect(values).toContain(v);
    }
  });

  it("quotes only measured numbers in the prospect answers", () => {
    const measured = Object.values(MEASURED).map((s) => s.value);
    const numbers = FAQ.flatMap((f) => [...f.a.matchAll(/\d[\d.]*\s?(?:s\b|%|of \d+)/g)].map((m) => m[0]));
    expect(numbers.length).toBeGreaterThan(5);
    for (const n of numbers) expect(measured.some((v) => v.includes(n)), n).toBe(true);
  });

  it("links only verified https doc pages", () => {
    const links = allLinks();
    expect(links.length).toBeGreaterThan(20);
    for (const l of links) expect(VERIFIED, l.href).toContain(l.href);
    // Every node's docs include the Flux STT quickstart on the intake listener (P5-7).
    expect(NODES.find((n) => n.id === "intake-stt")!.links.map((l) => l.href)).toContain(
      "https://developers.deepgram.com/docs/flux/quickstart",
    );
  });

  it("wires edges and walk steps to real nodes", () => {
    const ids = new Set(NODES.map((n) => n.id));
    const edgeIds = new Set(EDGES.map((e) => e.id));
    for (const e of EDGES) {
      expect(ids, e.id).toContain(e.from);
      expect(ids, e.id).toContain(e.to);
      expect(e.label, e.id).not.toBe("");
    }
    for (const s of WALK) {
      for (const n of s.nodes) expect(ids, s.id).toContain(n);
      for (const e of s.edges) expect(edgeIds, s.id).toContain(e);
    }
  });

  it("walks a call in 9 steps", () => {
    expect(WALK.map((s) => s.title)).toEqual([
      "Dial in",
      "Greeting",
      "Identity",
      "Intake",
      "Queue and hold",
      "Handoff",
      "Live assist",
      "Wrap up",
      "Done",
    ]);
  });

  it("covers privacy, prospect questions, and PoC vs production", () => {
    const privacy = PRIVACY.map((p) => p.title).join(" | ");
    for (const t of [/stored/i, /mask/i, /identity/i, /audio/i, /mock data/i, /production/i]) expect(privacy).toMatch(t);
    const production = PRIVACY.find((p) => /production/i.test(p.title))!.text;
    for (const t of [/BAA/, /HIPAA/, /host/i]) expect(production).toMatch(t);

    const qs = FAQ.map((f) => f.q).join(" | ");
    for (const t of [/fast|latency/i, /medical|accura/i, /own cloud|self.host/i, /language/i, /scale/i, /LLM/, /carrier/i, /Pipecat Cloud/]) {
      expect(qs).toMatch(t);
    }

    const rows = POC_VS_PROD.map((r) => `${r.area} ${r.poc} ${r.production}`).join(" | ");
    for (const t of [/Fly/, /Pipecat Cloud/, /mock/i, /connector/i, /Daily/, /SIP/]) expect(rows).toMatch(t);
  });
});
