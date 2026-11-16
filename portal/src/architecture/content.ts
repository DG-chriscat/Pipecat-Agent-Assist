/**
 * Architecture page content (PRD AP-21): the one data file for the diagram, the node
 * panels, the call walk, and the sections. Copy is plain English for business readers;
 * the technical view adds the services, protocols, and models.
 *
 * Numbers: every measured number is a `stat(value, label, source, evidence)`. `source` is
 * the repo doc it came from and `evidence` is the exact text in that doc;
 * content.test.ts fails if the doc does not contain it. No number here is estimated.
 *
 * Speech to text follows P5-7 (IN-14): both sides are picked in Settings. Intake runs
 * Nova-3 by default (Flux selectable; Flux missed a hard last name in testing, see
 * docs/spikes/stt-flux-intake.md) and the assist listens with Nova-3 per person. The
 * intake numbers below were measured on Nova-3; their labels say so.
 *
 * Links: only doc pages verified on Nov 16 (card t_c23e60a2); scripts/check_links.py
 * re-checks them. They render through ExternalLink, so they open in a new tab.
 */

export type View = "business" | "technical";
export type NodeKind = "ai" | "deepgram" | "infra" | "data";
export type Swatch = "copper" | "aluminum" | "bakelite" | "stone" | "glass" | "fuchsia";
export type IconName =
  | "phone"
  | "users"
  | "ear"
  | "audio"
  | "heart"
  | "workflow"
  | "sparkles"
  | "volume"
  | "server"
  | "monitor"
  | "database";

export interface Stat {
  value: string;
  label: string;
  /** Repo path of the doc the number comes from. */
  source: string;
  /** Text in that doc that shows the number (checked by content.test.ts). */
  evidence: string | string[];
}

export interface DocLink {
  label: string;
  href: string;
}

export interface NodeText {
  title: string;
  sub: string;
  /** What it does, two sentences. */
  lead: string;
}

export interface ArchNode {
  id: string;
  kind: NodeKind;
  swatch: Swatch;
  icon: IconName;
  /** Small label over the panel title. */
  role: string;
  /** Position on the 1100 x 620 map, as in the mockups. */
  box: { x: number; y: number; w: number; h: number };
  /** Inside the Pipecat bot group. */
  inBot?: boolean;
  business: NodeText;
  technical: NodeText;
  why: [string, string, string];
  stats: Stat[];
  /** Technical view only: key facts, as in the Supabase mockup panel. */
  facts?: { k: string; v: string }[];
  links: DocLink[];
}

export interface ArchEdge {
  id: string;
  from: string;
  to: string;
  /** Technical view only. */
  label: string;
  d: string;
  labelAt: { x: number; y: number };
}

export interface WalkStep {
  id: string;
  title: string;
  text: string;
  nodes: string[];
  edges: string[];
  stats: Stat[];
}

export interface Section {
  title: string;
  text: string;
}

export interface Faq {
  q: string;
  a: string;
  links?: DocLink[];
}

export interface PocRow {
  area: string;
  poc: string;
  production: string;
}

const stat = (value: string, label: string, source: string, evidence: string | string[]): Stat => ({
  value,
  label,
  source,
  evidence,
});

// ---- measured numbers (value, label, source doc, evidence in that doc) ----

export const MEASURED = {
  transcript: stat("0.44 s", "spoken word to text, median of 27 turns", "docs/spikes/S2.md", "median 0.438 s"),
  transcriptRange: stat("0.32 to 0.61 s", "end of speech to final text, every turn", "docs/spikes/S2.md", "0.32 to 0.61 s after the end of speech"),
  labels: stat("51 of 51", "lines labeled to the right person", "docs/spikes/S2.md", "(51 of 51 lines)"),
  cpu: stat("~35%", "of one vCPU for a 2 person call", "docs/spikes/S2.md", "~35% of one vCPU"),
  memberIds: stat("10 of 10", "member IDs heard right with read back (measured on Nova-3)", "docs/spikes/identity.md", "The member ID was heard right in 10 of 10 runs"),
  identityChecks: stat("7 of 7", "identity checks right, of the runs that reached the check", "docs/spikes/identity.md", "7 of 7 runs that reached the check"),
  intakeTime: stat("about 100 s", "intake on a clean call, greeting to queue", "docs/spikes/identity.md", "On clean runs intake is now about 100 s"),
  flux: stat("80 of 80", "member IDs, dates, amounts, claim numbers read right", "docs/spikes/tts-flux-vs-aura.md", "entity right in 80 of 80 checks"),
  fluxTtfa: stat("0.12 s", "text to first audio, Flux Alexis (median)", "docs/spikes/tts-flux-vs-aura.md", "| 0.143 | 0.118 |"),
  greetingAfterJoin: stat("0.27 s", "bot joined to greeting audio, Flux voice", "docs/spikes/tts-flux-vs-aura.md", "0.27 s after the bot joined"),
  warmGreeting: stat("about 3 s", "call start to greeting, warm bot (browser call)", "docs/warm-bot.md", ["warm warm 0.89s 3.41s 3.73s", "warm (refill) warm 0.66s 2.57s 2.91s"]),
  coldGreeting: stat("12.8 s", "the same without a warm bot (cold start)", "docs/warm-bot.md", "cold cold 0.82s 12.36s 12.81s"),
  roomAndToken: stat("~0.7 s", "room and token created at Daily", "docs/warm-bot.md", "The ~0.7 s before `bot_launched` is room and token creation at Daily"),
  toneLoad: stat("1.3 to 2.5 s", "tone model load, done before the call on a warm bot", "docs/warm-bot.md", "| 1.3 to 2.5 |"),
  phonellmReply: stat("0.31 s", "phoneLLM mini first byte on an intake reply (median)", "docs/spikes/llm-deepgram.md", "| LLM first byte, median | **0.310 s** (n=175)"),
  cards: stat("0.88 to 1.61 s", "trigger word to card on screen, 3 packs", "docs/spikes/phase4.md", "0.88 to 1.61 s after the trigger word"),
  ask: stat("0.3 s", "Ask bar answer, Enter to card (0.27 to 0.33 s)", "docs/spikes/phase4.md", ["0.33 s after Enter", "| **0.27** |"]),
  botSend: stat("366 to 397 ms", "inside the bot, final line to card sent", "docs/spikes/phase4.md", "366 to 397 ms"),
  dbRoundTrip: stat("~350 ms", "Supabase lookup from the bot host, cache miss", "docs/spikes/phase4.md", "~350 ms on a cache miss"),
  offer: stat("under 10 ms", "agent available to call offered", "docs/spikes/phase2.md", "offer under 10 ms after the agent goes available"),
  token: stat("under 0.5 s", "accept to the agent's room token", "docs/spikes/phase2.md", "token under 0.5 s after accept"),
  acceptToAudio: stat("1.77 s", "accept to hearing the caller (median)", "docs/spikes/phase3.md", "median 1.77 s in run 5"),
  wrapView: stat("0.38 s", "End to the wrap up view", "docs/spikes/phase3.md", "wrap up view 0.38 s after End"),
  savedToAvailable: stat("0.14 s", "Save to available again", "docs/spikes/phase3.md", "saved to available 0.14 s"),
  lookupTimeout: stat("2 s", "timeout per lookup, cached per call", "docs/connectors.md", "2 s per call"),
  botSilent: stat("0", "words the bot says to the caller in this step", "docs/PRD.md", "It never speaks to the caller in this mode."),
  roomClose: stat("6 s", "after goodbye, the room is deleted", "docs/queue.md", "`ROOM_CLOSE_DELAY_SECS` (6 s)"),
  noAudio: stat("0", "audio recordings stored", "docs/transcript.md", "No audio is stored."),
  tokenLife: stat("1 hour", "agent room token lifetime", "docs/spikes/S4.md", "a 1 hour expiry"),
} satisfies Record<string, Stat>;
const N = MEASURED;

// ---- doc links (verified Nov 16) ----
const L = {
  dgModels: { label: "Models and languages", href: "https://developers.deepgram.com/docs/models-languages-overview" },
  dgKeyterm: { label: "Keyterm prompting", href: "https://developers.deepgram.com/docs/keyterm" },
  dgFluxTts: { label: "Flux TTS quickstart", href: "https://developers.deepgram.com/docs/flux-tts/quickstart" },
  dgFluxStt: { label: "Flux STT quickstart", href: "https://developers.deepgram.com/docs/flux/quickstart" },
  dgTts: { label: "Text to speech", href: "https://developers.deepgram.com/docs/text-to-speech" },
  dgStreaming: { label: "Streaming audio", href: "https://developers.deepgram.com/docs/live-streaming-audio" },
  dgNumerals: { label: "Numerals", href: "https://developers.deepgram.com/docs/numerals" },
  dgSelfHosted: { label: "Self hosted Deepgram", href: "https://developers.deepgram.com/docs/self-hosted-introduction" },
  dgSttProduct: { label: "Deepgram speech to text", href: "https://deepgram.com/product/speech-to-text" },
  dgTtsProduct: { label: "Deepgram text to speech", href: "https://deepgram.com/product/text-to-speech" },
  pcIntro: { label: "Pipecat overview", href: "https://docs.pipecat.ai/overview/introduction" },
  pcDailyPstn: { label: "Pipecat phone calls with Daily", href: "https://docs.pipecat.ai/pipecat/telephony/daily-pstn" },
  pcFlows: { label: "Pipecat Flows", href: "https://docs.pipecat.ai/pipecat/flows/introduction" },
  pcClient: { label: "Pipecat client", href: "https://docs.pipecat.ai/client/introduction" },
  pcStt: { label: "Pipecat Deepgram STT", href: "https://docs.pipecat.ai/api-reference/server/services/stt/deepgram" },
  pcTts: { label: "Pipecat Deepgram TTS", href: "https://docs.pipecat.ai/api-reference/server/services/tts/deepgram" },
  pcCloud: { label: "Pipecat Cloud", href: "https://docs.pipecat.ai/pipecat-cloud/introduction" },
  pcGithub: { label: "Pipecat on GitHub", href: "https://github.com/pipecat-ai/pipecat" },
  daily: { label: "Daily dial-in and dial-out", href: "https://docs.daily.co/docs/guides/features/dial-in-dial-out" },
  rls: { label: "Row level security", href: "https://supabase.com/docs/guides/database/postgres/row-level-security" },
  pg: { label: "Connecting to Postgres", href: "https://supabase.com/docs/guides/database/connecting-to-postgres" },
  fly: { label: "Fly.io docs", href: "https://docs.fly.io" },
  claude: { label: "Claude models", href: "https://platform.claude.com/docs/en/models/overview" },
  dgLlm: { label: "Deepgram LLMs (Early Access)", href: "https://developers.deepgram.com/docs/llm" },
} satisfies Record<string, DocLink>;

// ---- map layout (1100 x 620, mockup coordinates) ----
const BOT_X = 444;
const BOT_W = 300;
const BOT_H = 70;
const botRow = (i: number) => ({ x: BOT_X, y: 52 + i * 82, w: BOT_W, h: BOT_H });

/** The dashed group around the bot's parts. */
export const BOT_GROUP = {
  box: { x: 430, y: 30, w: 328, h: 526 },
  business: "The voice bot (Pipecat)",
  technical: "Pipecat 1.15 bot, one process per call",
};
export const MAP = { w: 1100, h: 620 };

export const NODES: ArchNode[] = [
  {
    id: "caller",
    kind: "infra",
    swatch: "copper",
    icon: "phone",
    role: "Caller",
    box: { x: 0, y: 250, w: 190, h: 112 },
    business: {
      title: "Caller",
      sub: "Phone or web browser",
      lead: "A health plan member calls the plan's number, or starts a call from a web page. Nothing to install and no app to download.",
    },
    technical: {
      title: "Caller",
      sub: "PSTN or WebRTC browser",
      lead: "Phone callers reach a Daily dial-in number over the phone network, browser callers join a Daily room over WebRTC. Both land in the same kind of private room.",
    },
    why: [
      "Callers keep the phone number they already know.",
      "A browser call makes live demos easy without a phone line.",
      "The same room works for both, so the rest of the system never changes.",
    ],
    stats: [N.warmGreeting, N.greetingAfterJoin],
    links: [L.pcDailyPstn, L.daily],
  },
  {
    id: "daily",
    kind: "infra",
    swatch: "aluminum",
    icon: "users",
    role: "Calls and rooms",
    box: { x: 222, y: 250, w: 190, h: 112 },
    business: {
      title: "Daily",
      sub: "Phone line and call room",
      lead: "Daily provides the phone number and a private call room for each call. The caller, the voice bot, and later the agent all meet in that room.",
    },
    technical: {
      title: "Daily",
      sub: "SIP dial-in, WebRTC rooms",
      lead: "A Daily dial-in number sends a webhook to the Vitals server, which creates a private room and starts the bot. Each participant's audio arrives on its own track, which is how every line is labeled.",
    },
    why: [
      "Phone and browser calls in one room, with Pipecat support built in.",
      "Separate audio per person, so no guessing who spoke.",
      "Private rooms with short lived tokens for each agent.",
    ],
    stats: [N.roomAndToken, N.acceptToAudio, N.tokenLife],
    links: [L.daily, L.pcDailyPstn],
  },
  {
    id: "intake-stt",
    kind: "deepgram",
    swatch: "glass",
    icon: "ear",
    role: "Speech to text, intake",
    box: botRow(0),
    inBot: true,
    business: {
      title: "Intake listening",
      sub: "Hears the caller during intake",
      lead: "Turns what the caller says to the bot into text while they speak. It also knows when the caller has finished talking, so the bot answers at the right moment.",
    },
    technical: {
      title: "Deepgram STT, Nova-3 or Flux",
      sub: "nova-3-general by default, flux-general-en in Settings",
      lead: "Nova-3 by default, with VAD and smart turn deciding when the caller has finished; Flux, picked in Settings, detects the end of each turn itself and answers a little sooner, but heard a hard last name less well in testing. During identity questions numerals and keyterms keep member IDs and plan names exact.",
    },
    why: [
      "Picked per side in Settings: Nova-3 by default, Flux one click away.",
      "Keyterms for plan names and the member ID prefix.",
      "Same vendor for listening and speaking, one bill and one support team.",
    ],
    stats: [N.memberIds, N.identityChecks, N.intakeTime],
    links: [L.dgFluxStt, L.dgKeyterm, L.dgNumerals, L.pcStt],
  },
  {
    id: "assist-stt",
    kind: "deepgram",
    swatch: "glass",
    icon: "audio",
    role: "Speech to text, live call",
    box: botRow(1),
    inBot: true,
    business: {
      title: "Assist listening",
      sub: "Hears each person on their own",
      lead: "Turns each person's voice into text as they speak once the agent joins. The caller and the agent are transcribed on their own tracks, so every line is labeled without guessing who spoke.",
    },
    technical: {
      title: "Deepgram Nova-3 STT",
      sub: "Streaming, keyterms, per track",
      lead: "One Nova-3 stream per person, fed from that person's Daily track, so labels come from the track and not from diarization. Every pack's keyterms are boosted, and each agent can pick Nova-3 Medical or Flux.",
    },
    why: [
      "Accurate on health plan terms, boosted with keyterms like plan names and member ID prefixes.",
      "Fast enough for live help on screen.",
      "Labels are exact by construction, even when people talk over each other.",
    ],
    stats: [N.transcript, N.labels, N.transcriptRange],
    links: [L.dgModels, L.dgKeyterm, L.dgStreaming, L.pcStt, L.dgSttProduct],
  },
  {
    id: "tone",
    kind: "ai",
    swatch: "bakelite",
    icon: "heart",
    role: "Caller mood",
    box: botRow(2),
    inBot: true,
    business: {
      title: "Tone reader",
      sub: "Reads how the caller feels",
      lead: "Reads each caller line and tracks their mood across the call. When the caller gets more upset, the agent gets a short tip on how to respond.",
    },
    technical: {
      title: "Pipecat Tone reader",
      sub: "Realtime Context Standard, local",
      lead: "Pipecat's tone reader runs a small model on the CPU inside the bot, on the words of each final caller line. The reading is saved with the transcript line and drives the mood trend and tone tips.",
    },
    why: [
      "Runs inside the bot: no extra service and no audio leaves for it.",
      "Words only, which is easy to explain and audit.",
      "Part of Pipecat, so it ships with the same release.",
    ],
    stats: [N.toneLoad],
    links: [L.pcIntro, L.pcGithub],
  },
  {
    id: "flows",
    kind: "ai",
    swatch: "stone",
    icon: "workflow",
    role: "Intake",
    box: botRow(3),
    inBot: true,
    business: {
      title: "Intake assistant",
      sub: "Asks the questions, checks identity",
      lead: "Greets the caller, checks who they are, and asks why they are calling. It reads the details back so the caller can fix anything before the agent sees it.",
    },
    technical: {
      title: "Pipecat Flows + phoneLLM mini",
      sub: "Function calls to the database",
      lead: "Pipecat Flows runs intake as fixed steps, and Deepgram phoneLLM mini fills each step through function calls. Identity is checked against the member database by a function, not by the model.",
    },
    why: [
      "Fixed steps keep intake on script and easy to change per use case.",
      "The model talks naturally but never decides identity on its own.",
      "The LLM is picked per job in Settings: phoneLLM mini, Think-Fast (both Deepgram, Early Access), or Claude Haiku.",
    ],
    stats: [N.identityChecks, N.intakeTime, N.phonellmReply],
    links: [L.pcFlows, L.dgLlm, L.claude],
  },
  {
    id: "assist",
    kind: "ai",
    swatch: "fuchsia",
    icon: "sparkles",
    role: "Live help",
    box: botRow(4),
    inBot: true,
    business: {
      title: "Assist engine",
      sub: "Suggests the next best step",
      lead: "Listens to the live call and looks up what the agent needs, like the caller's claim or payment history. It puts one next best step on the agent's screen, with the words to say.",
    },
    technical: {
      title: "Assist engine + Claude",
      sub: "Ranks cards, read only lookups",
      lead: "Trigger rules from the use case pack fire on the transcript and call read only connectors; Claude adds article cards and answers the Ask bar. Cards go to the agent's portal only, as RTVI messages.",
    },
    why: [
      "Rules for the fast, common cases; the LLM for the rest.",
      "Read only, member scoped lookups, every one logged.",
      "New use cases are packs, not code changes.",
    ],
    stats: [N.cards, N.ask, N.botSend],
    links: [L.claude, L.pcClient, L.pcGithub],
  },
  {
    id: "tts",
    kind: "deepgram",
    swatch: "glass",
    icon: "volume",
    role: "Text to speech",
    box: botRow(5),
    inBot: true,
    business: {
      title: "Deepgram voice",
      sub: "Speaks to the caller",
      lead: "Speaks the bot's lines to the caller in a natural voice. It reads member IDs, dates, and amounts clearly, which matters most during intake.",
    },
    technical: {
      title: "Deepgram Flux TTS",
      sub: "/v2/speak, flux-alexis-en",
      lead: "Deepgram Flux TTS over a websocket, voice flux-alexis-en by default; Aura-2 is one click away in Settings. The greeting and read backs are fixed lines, so they cannot be cut off.",
    },
    why: [
      "Read every key entity right in our test, a hair better than Aura-2.",
      "First audio in about a tenth of a second.",
      "Voice and greeting are settings, not code.",
    ],
    stats: [N.flux, N.fluxTtfa, N.greetingAfterJoin],
    links: [L.dgFluxTts, L.dgTts, L.pcTts, L.dgTtsProduct],
  },
  {
    id: "server",
    kind: "infra",
    swatch: "aluminum",
    icon: "server",
    role: "Backend",
    box: { x: 806, y: 54, w: 250, h: 112 },
    business: {
      title: "Vitals server",
      sub: "Queue, agents, sign in",
      lead: "Answers the call, starts a bot, and keeps the line of waiting callers. It offers each call to the next available agent.",
    },
    technical: {
      title: "Vitals server (FastAPI)",
      sub: "Queue, websocket, Daily tokens",
      lead: "A FastAPI app takes the Daily webhook, creates the room, and hands the call to a pre warmed bot. It runs the queue over a websocket and mints each agent's room token.",
    },
    why: [
      "A warm bot is already loaded, so the caller hears the greeting fast.",
      "One small service: queue, offers, wrap up, settings.",
      "Runs anywhere Python runs.",
    ],
    stats: [N.warmGreeting, N.coldGreeting, N.offer],
    links: [L.fly, L.pcCloud, L.daily],
  },
  {
    id: "portal",
    kind: "infra",
    swatch: "copper",
    icon: "monitor",
    role: "Agent screen",
    box: { x: 806, y: 250, w: 250, h: 112 },
    business: {
      title: "Agent portal",
      sub: "What the agent sees",
      lead: "The agent's screen: the incoming call, who the caller is, the live conversation, and suggestions. The agent talks to the caller from the browser.",
    },
    technical: {
      title: "Agent portal (React)",
      sub: "Pipecat client, RTVI messages",
      lead: "A React app that joins the Daily room with the Pipecat client when the agent accepts. Transcript lines and assist cards arrive as RTVI messages from the bot.",
    },
    why: [
      "Runs in any modern browser, no install.",
      "Gets the intake summary before saying hello.",
      "PHI is masked by default and every reveal is logged.",
    ],
    stats: [N.acceptToAudio, N.cards, N.ask],
    links: [L.pcClient],
  },
  {
    id: "db",
    kind: "data",
    swatch: "stone",
    icon: "database",
    role: "Data",
    box: { x: 806, y: 446, w: 250, h: 112 },
    business: {
      title: "Member records",
      sub: "Claims, payments, history",
      lead: "Mock member, claim, payment, and plan records for the demo. In production this is the health plan's own systems, reached the same way.",
    },
    technical: {
      title: "Supabase Postgres",
      sub: "Row level security, mock data",
      lead: "Mock member, plan, claim, payment, referral, authorization, interaction, and article tables. Plain Postgres over the session pooler, so any Postgres works in production.",
    },
    why: [
      "Plain Postgres: nothing vendor specific in the code.",
      "Row level security on every table.",
      "The same connector shape swaps in the payer's systems.",
    ],
    stats: [N.lookupTimeout, N.dbRoundTrip],
    facts: [
      { k: "Access", v: "Read only connectors, search and get by ID" },
      { k: "Timeout", v: "2 s per lookup, cached per call" },
      { k: "Security", v: "Row level security on every table" },
      { k: "Audit", v: "Every lookup logged with call ID" },
      { k: "Production", v: "Swap for the payer's systems behind the same connector shape" },
    ],
    links: [L.rls, L.pg],
  },
];

export const EDGES: ArchEdge[] = [
  { id: "caller-daily", from: "caller", to: "daily", label: "SIP / WebRTC", d: "M190 306 L222 306", labelAt: { x: 160, y: 226 } },
  {
    id: "daily-bot",
    from: "daily",
    to: "intake-stt",
    label: "audio per track",
    d: "M412 306 C420 306 422 306 430 306",
    labelAt: { x: 312, y: 372 },
  },
  {
    id: "bot-server",
    from: "flows",
    to: "server",
    label: "handoff HTTP",
    d: "M758 87 C782 87 782 110 806 110",
    labelAt: { x: 862, y: 26 },
  },
  { id: "server-portal", from: "server", to: "portal", label: "websocket offers", d: "M931 166 L931 250", labelAt: { x: 940, y: 198 } },
  {
    id: "assist-portal",
    from: "assist",
    to: "portal",
    label: "RTVI cards + transcript",
    d: "M758 415 C782 415 782 306 806 306",
    labelAt: { x: 764, y: 378 },
  },
  {
    id: "assist-db",
    from: "assist",
    to: "db",
    label: "read only SQL",
    d: "M758 497 C782 497 782 502 806 502",
    labelAt: { x: 744, y: 566 },
  },
  {
    id: "server-db",
    from: "server",
    to: "db",
    label: "SQL",
    d: "M1056 110 C1100 110 1100 502 1056 502",
    labelAt: { x: 1084, y: 300 },
  },
  {
    id: "portal-daily",
    from: "portal",
    to: "daily",
    label: "agent joins the room (WebRTC)",
    d: "M302 362 C302 604 931 604 931 362",
    labelAt: { x: 520, y: 588 },
  },
];

export const WALK: WalkStep[] = [
  {
    id: "dial-in",
    title: "Dial in",
    text: "The caller dials the plan's number, or starts a call in the browser. Daily answers and tells the Vitals server, which opens a private call room and hands it to a voice bot that is already loaded.",
    nodes: ["caller", "daily", "server"],
    edges: ["caller-daily"],
    stats: [N.roomAndToken, N.warmGreeting],
  },
  {
    id: "greeting",
    title: "Greeting",
    text: "The bot joins the room and greets the caller in the Deepgram voice, and says it is an AI assistant. The greeting is a fixed line from Settings, so it plays without waiting on the AI model.",
    nodes: ["daily", "tts", "flows"],
    edges: ["daily-bot"],
    stats: [N.greetingAfterJoin, N.warmGreeting, N.fluxTtfa],
  },
  {
    id: "identity",
    title: "Identity",
    text: "The bot asks for name, date of birth, and member ID, reads the name and ID back, and checks them against the member records. A close name match with the right ID and date of birth goes to the agent to confirm.",
    nodes: ["daily", "intake-stt", "flows", "tts", "db"],
    edges: ["daily-bot"],
    stats: [N.memberIds, N.identityChecks, N.flux],
  },
  {
    id: "intake",
    title: "Intake",
    text: "The bot asks why they are calling, with the questions from the use case pack, and reads a short summary back. The tone reader notes how the caller feels from the start.",
    nodes: ["daily", "intake-stt", "tone", "flows", "tts"],
    edges: ["daily-bot"],
    stats: [N.intakeTime, N.phonellmReply],
  },
  {
    id: "queue",
    title: "Queue and hold",
    text: "Intake hands the call to the Vitals server's queue with the summary. The next available agent gets the offer on screen; the caller hears their place in line while they wait.",
    nodes: ["flows", "tts", "server", "portal"],
    edges: ["bot-server", "server-portal"],
    stats: [N.offer],
  },
  {
    id: "handoff",
    title: "Handoff",
    text: "The agent accepts and joins the same call room from the browser, with the intake summary already on screen. The bot says it is connecting them and goes quiet.",
    nodes: ["server", "portal", "daily"],
    edges: ["server-portal", "portal-daily"],
    stats: [N.acceptToAudio, N.token],
  },
  {
    id: "live-assist",
    title: "Live assist",
    text: 'The agent and caller talk. Deepgram transcribes both. When the caller says "denied" or "prior authorization", the assist engine looks up her claim and puts one next best step on the agent\'s screen, with the words to say.',
    nodes: ["daily", "assist-stt", "assist", "db", "portal"],
    edges: ["daily-bot", "assist-portal", "assist-db"],
    stats: [N.cards, N.ask, N.botSilent],
  },
  {
    id: "wrap-up",
    title: "Wrap up",
    text: "When the call ends the bot drafts a short summary with Claude Haiku and suggests a reason code. The agent edits, picks the code, and saves; the record keeps the transcript and what the assist showed.",
    nodes: ["assist", "server", "portal", "db"],
    edges: ["assist-portal", "server-db"],
    stats: [N.wrapView, N.savedToAvailable],
  },
  {
    id: "done",
    title: "Done",
    text: "The agent is available for the next call. The bot has left, the room is deleted, and no audio was recorded.",
    nodes: ["server", "portal", "db"],
    edges: ["server-db"],
    stats: [N.roomClose, N.noAudio],
  },
];

export const PRIVACY: Section[] = [
  {
    title: "What is stored where",
    text: "Call records, transcript lines, intake answers, assist events, and wrap up notes are stored in Postgres (Supabase for the demo). Settings and the queue live on the Vitals server. Daily, Deepgram, and the LLM process audio or text for the live call only.",
  },
  {
    title: "PHI masking",
    text: "Member ID and date of birth are masked on the agent's screen by default. Reveal shows them for 30 seconds and every reveal is logged on the call record. Logs never hold member IDs, query text, or result rows.",
  },
  {
    title: "Identity verification and the record gate",
    text: "Identity is checked by a database function, not by the AI. A caller who is not fully verified reaches an agent flagged, with claims and payments locked until the agent confirms who they are; that decision is logged.",
  },
  {
    title: "No audio recording",
    text: "Audio streams through the call and is never stored. Only the text transcript and the cards are saved per call.",
  },
  {
    title: "Mock data only",
    text: "Every member, claim, and payment in this demo is made up. No real patient data is used anywhere.",
  },
  {
    title: "What changes for production",
    text: "Signed BAAs with every vendor that sees PHI (Daily, Deepgram, the LLM provider, hosting), HIPAA controls (access, audit, retention, encryption), and hosting in the plan's approved cloud. Real data comes from the plan's systems through the same read only connectors.",
  },
];

export const FAQ: Faq[] = [
  {
    q: "How fast is it?",
    a: "Spoken words show as text on the agent's screen in 0.44 s (median). A suggestion card shows 0.88 to 1.61 s after the words that trigger it, and the Ask bar answers in about 0.3 s. With a warm bot the caller hears the greeting about 3 s after the call starts.",
  },
  {
    q: "How accurate is it on medical and plan terms?",
    a: "Deepgram keyterms boost plan names, provider names, denial codes, and the member ID prefix, and numerals keep IDs as digits. With read back, member IDs were heard right in 10 of 10 test calls, and the voice read every ID, date, and amount right in 80 of 80 checks.",
    links: [L.dgKeyterm, L.dgNumerals],
  },
  {
    q: "Can Deepgram run in our own cloud (self hosted)?",
    a: "Yes. Deepgram offers self hosted deployment, so audio never leaves your environment. The bot only needs the Deepgram URL changed.",
    links: [L.dgSelfHosted],
  },
  {
    q: "Which languages does it support?",
    a: "This demo is English only. Deepgram supports many languages; adding one means the models for it plus translated intake questions and packs.",
    links: [L.dgModels],
  },
  {
    q: "How does it scale?",
    a: "Each call runs its own bot process, so calls scale out by adding machines. A two person call used about 35% of one vCPU in our test; production would run bots on Pipecat Cloud or your own cluster.",
    links: [L.pcCloud],
  },
  {
    q: "Can we swap the LLM or the phone carrier?",
    a: "Yes. The LLM is picked per job in Settings: Deepgram phoneLLM mini runs intake by default, Claude Haiku the assist and wrap up, and Deepgram Think-Fast is a third choice. The Deepgram LLMs are Early Access, not for production traffic yet. Phone calls can come from your own carrier over SIP into Daily, or through another transport Pipecat supports.",
    links: [L.dgLlm, L.claude, L.daily],
  },
  {
    q: "What is Pipecat Cloud?",
    a: "Managed hosting for Pipecat bots from Daily: it runs one bot per call, scales with traffic, and handles the phone and room setup. The demo runs bots on our own Fly machine; production could move to Pipecat Cloud without code changes to the bot.",
    links: [L.pcCloud, L.pcIntro],
  },
];

export const POC_VS_PROD: PocRow[] = [
  { area: "Bot hosting", poc: "One Fly.io machine with a warm bot", production: "Pipecat Cloud, or your own cloud" },
  { area: "Member data", poc: "Mock records in Supabase Postgres", production: "Your systems, through the same read only connector shape" },
  { area: "Phone line", poc: "A Daily phone number", production: "Your carrier, connected to Daily over SIP" },
  { area: "Sign in", poc: "One shared demo password", production: "Your single sign on and agent roles" },
  { area: "Compliance", poc: "Mock data only, no BAAs", production: "BAAs with every vendor, HIPAA controls" },
];

/** Every stat on the page (nodes and walk steps). */
export function allStats(): Stat[] {
  return [...NODES.flatMap((n) => n.stats), ...WALK.flatMap((s) => s.stats)];
}

/** Every external link in the content. */
export function allLinks(): DocLink[] {
  return [...NODES.flatMap((n) => n.links), ...FAQ.flatMap((f) => f.links ?? [])];
}
