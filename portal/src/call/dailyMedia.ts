/**
 * CallMedia on the Pipecat client SDK with the Daily transport (QH-6): audio only,
 * mic on. The SDK is loaded on first join so the idle portal and the tests do not
 * pull in daily-js.
 *
 * The agent joins a room the bot already runs; the Daily transport treats every
 * remote participant as "the bot", so disconnectOnBotDisconnect is off (the bot or
 * the caller leaving must not drop the agent). The room is "live" once Daily has
 * joined; we do not wait for the bot's RTVI bot-ready.
 */
import type { PipecatClient } from "@pipecat-ai/client-js";
import type { CallMedia, CallMediaEvents } from "./media";

export class DailyCallMedia implements CallMedia {
  private client: PipecatClient | null = null;
  private readonly audio = new Map<string, HTMLAudioElement>();
  private readonly events: CallMediaEvents;
  private closed = false;

  constructor(events: CallMediaEvents) {
    this.events = events;
  }

  async join(roomUrl: string, token: string): Promise<void> {
    const [{ PipecatClient }, { DailyTransport }] = await Promise.all([
      import("@pipecat-ai/client-js"),
      import("@pipecat-ai/daily-transport"),
    ]);
    if (this.closed) return;
    const ev = this.events;
    let joined: () => void = () => {};
    const connected = new Promise<void>((resolve) => (joined = resolve));
    const client = new PipecatClient({
      transport: new DailyTransport(),
      enableMic: true,
      enableCam: false,
      disconnectOnBotDisconnect: false,
      callbacks: {
        onConnected: () => joined(),
        onRemoteAudioLevel: (level, p) => ev.onRemoteLevel(level, p.id),
        onParticipantLeft: (p) => {
          if (!p.local) ev.onParticipantLeft(p.id);
        },
        onTrackStarted: (track, p) => {
          if (p && !p.local && track.kind === "audio") this.play(p.id, track);
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
    const failed = client.connect({ url: roomUrl, token }).then(
      () => new Promise<never>(() => {}),
      (e: unknown) => Promise.reject(e instanceof Error ? e : new Error(String(e))),
    );
    await Promise.race([connected, failed]);
  }

  setMic(on: boolean): void {
    this.client?.enableMic(on);
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
