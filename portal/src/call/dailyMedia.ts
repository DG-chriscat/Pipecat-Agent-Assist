/**
 * CallMedia on the Pipecat client SDK with the Daily transport (QH-6): audio only,
 * mic on. The SDK is loaded on first use so the idle portal and the tests do not
 * pull in daily-js. prepare() does that work (SDK, client, mic) while the offer
 * rings, so join() after accept only has to connect (P3-5, accept to audio < 2 s).
 *
 * The agent joins a room the bot already runs; the Daily transport treats every
 * remote participant as "the bot", so disconnectOnBotDisconnect is off (the bot or
 * the caller leaving must not drop the agent). The room is "live" once Daily has
 * joined; we do not wait for the bot's RTVI bot-ready.
 */
import type { PipecatClient } from "@pipecat-ai/client-js";
import type { CallMedia, CallMediaEvents } from "./media";

/** Call timeline marks (performance.mark "vitals:<name>"), read by the live tests. */
function mark(name: string): void {
  try {
    performance.mark(`vitals:${name}`);
  } catch {
    // No performance API (old test env): timing is optional.
  }
}

export class DailyCallMedia implements CallMedia {
  private client: PipecatClient | null = null;
  /** The client with its devices started; shared by prepare() and join(). */
  private ready: Promise<PipecatClient | null> | null = null;
  private connected: () => void = () => {};
  private readonly audio = new Map<string, HTMLAudioElement>();
  private readonly events: CallMediaEvents;
  private closed = false;

  constructor(events: CallMediaEvents) {
    this.events = events;
  }

  /** While ringing: load the SDK, create the client, and start the mic (P3-5). */
  prepare(): Promise<void> {
    return this.setup().then(() => undefined);
  }

  private setup(): Promise<PipecatClient | null> {
    this.ready ??= this.build();
    return this.ready;
  }

  private async build(): Promise<PipecatClient | null> {
    mark("setup");
    const [{ PipecatClient }, { DailyTransport }] = await Promise.all([
      import("@pipecat-ai/client-js"),
      import("@pipecat-ai/daily-transport"),
    ]);
    mark("sdk-loaded");
    if (this.closed) return null;
    const ev = this.events;
    const client = new PipecatClient({
      transport: new DailyTransport(),
      enableMic: true,
      enableCam: false,
      disconnectOnBotDisconnect: false,
      callbacks: {
        onConnected: () => {
          mark("connected");
          this.connected();
        },
        onServerMessage: (data: unknown) => ev.onServerMessage(data),
        onRemoteAudioLevel: (level, p) => ev.onRemoteLevel(level, p.id),
        onParticipantLeft: (p) => {
          if (!p.local) ev.onParticipantLeft(p.id);
        },
        onTrackStarted: (track, p) => {
          if (p && !p.local && track.kind === "audio") {
            mark(`track:${p.id}`);
            this.play(p.id, track);
          }
        },
        onTrackStopped: (track, p) => {
          if (p && !p.local && track.kind === "audio") this.stop(p.id);
        },
        onError: (msg) => {
          const data = (msg?.data ?? {}) as { message?: string; fatal?: boolean };
          if (data.fatal) ev.onError(data.message ?? "");
        },
      },
    });
    this.client = client;
    try {
      // Mic permission and Daily's call machine; connect() skips this once done.
      await client.initDevices();
      mark("devices");
    } catch {
      // connect() runs initDevices again and reports a real failure.
    }
    if (this.closed) {
      // Released while warming up (the offer went away): free the mic.
      await client.disconnect().catch(() => {});
      return null;
    }
    return client;
  }

  async join(roomUrl: string, token: string): Promise<void> {
    mark("join");
    const client = await this.setup();
    if (!client || this.closed) return;
    const connected = new Promise<void>((resolve) => (this.connected = resolve));
    const failed = client.connect({ url: roomUrl, token }).then(
      () => new Promise<never>(() => {}),
      (e: unknown) => Promise.reject(e instanceof Error ? e : new Error(String(e))),
    );
    await Promise.race([connected, failed]);
  }

  setMic(on: boolean): void {
    this.client?.enableMic(on);
  }

  sendClientMessage(msgType: string, data: unknown): void {
    if (!this.client?.connected) return;
    try {
      this.client.sendClientMessage(msgType, data);
    } catch {
      // Feedback is best effort: the portal already shows the agent's action.
    }
  }

  async leave(): Promise<void> {
    this.closed = true;
    for (const id of [...this.audio.keys()]) this.stop(id);
    const c = this.client;
    this.client = null;
    if (c) await c.disconnect();
  }

  /** Daily call objects do not play remote audio by themselves. */
  private play(id: string, track: MediaStreamTrack): void {
    this.stop(id);
    const el = document.createElement("audio");
    el.autoplay = true;
    el.dataset.participant = id;
    el.srcObject = new MediaStream([track]);
    document.body.appendChild(el);
    void el.play().catch(() => {
      // Autoplay policy: the accept click is a user gesture, so this is rare.
    });
    this.audio.set(id, el);
  }

  private stop(id: string): void {
    const el = this.audio.get(id);
    if (!el) return;
    el.srcObject = null;
    el.remove();
    this.audio.delete(id);
  }
}

export const createDailyMedia = (events: CallMediaEvents): CallMedia => new DailyCallMedia(events);
