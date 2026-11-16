/** Fake CallMedia for tests: records calls, lets the test drive events. */
import type { CallMedia, CallMediaEvents } from "../call/media";

export class FakeMedia implements CallMedia {
  static instances: FakeMedia[] = [];
  static reset(): void {
    FakeMedia.instances = [];
  }
  static last(): FakeMedia {
    const m = FakeMedia.instances.at(-1);
    if (!m) throw new Error("no FakeMedia created");
    return m;
  }

  readonly events: CallMediaEvents;
  joined: { roomUrl: string; token: string } | null = null;
  mic: boolean[] = [];
  left = 0;
  private resolveJoin: (() => void) | null = null;
  private rejectJoin: ((e: Error) => void) | null = null;

  constructor(events: CallMediaEvents) {
    this.events = events;
    FakeMedia.instances.push(this);
  }

  join(roomUrl: string, token: string): Promise<void> {
    this.joined = { roomUrl, token };
    return new Promise((resolve, reject) => {
      this.resolveJoin = resolve;
      this.rejectJoin = reject;
    });
  }

  setMic(on: boolean): void {
    this.mic.push(on);
  }

  async leave(): Promise<void> {
    this.left += 1;
  }

  /** Test helpers. */
  finishJoin(): void {
    this.resolveJoin?.();
  }
  failJoin(message = "join failed"): void {
    this.rejectJoin?.(new Error(message));
  }
}

export const createFakeMedia = (events: CallMediaEvents) => new FakeMedia(events);
