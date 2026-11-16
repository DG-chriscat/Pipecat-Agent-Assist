/** Call audio for the agent: join the caller's Daily room (QH-6). */

export interface CallMediaEvents {
  /** Audio level 0..1 of one remote participant (caller or bot), about every 100 ms. */
  onRemoteLevel(level: number, participantId: string): void;
  /** A remote participant left the room. */
  onParticipantLeft(participantId: string): void;
  /** An RTVI server message from the bot (e.g. a live transcript line, P3-4). */
  onServerMessage(data: unknown): void;
  /** Fatal media error after joining. */
  onError(message: string): void;
}

export interface CallMedia {
  /**
   * Optional warm up while the offer rings (P3-5): load the SDK and open the mic so
   * join() only has to connect. Safe to skip; join() does it all if needed.
   */
  prepare?(): Promise<void>;
  /** Join the room, audio only, mic on. Resolves once joined. */
  join(roomUrl: string, token: string): Promise<void>;
  setMic(on: boolean): void;
  leave(): Promise<void>;
}

export type CreateMedia = (events: CallMediaEvents) => CallMedia;
