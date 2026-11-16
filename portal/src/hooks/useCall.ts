import { useCallback, useEffect, useRef, useState } from "react";
import type { AcceptResult, CallerDetails } from "../api/types";
import type { CallMedia, CreateMedia } from "../call/media";
import { callerInfo, type CallerInfo } from "../state/offer";
import {
  applyTranscript,
  parseTranscriptMessage,
  type HighlightRange,
  type TranscriptLine,
} from "../state/transcript";
import type { CallPhase } from "../components/InCall";
import type { DetailsState } from "../components/CallerPanel";

export interface ActiveCall {
  sessionId: string;
  caller: CallerInfo;
  memberId: string | null;
  /** Intake answers from the accept reply (QH-7). */
  intake: Record<string, unknown>;
  /** Queue wait known at accept time; the caller endpoint has the exact figure. */
  waitSecs: number | null;
  /** Caller history from GET /calls/{id}/caller (AP-13); null until it arrives. */
  details: CallerDetails | null;
  detailsState: DetailsState;
  /** Client clock (ms) when the agent accepted; the call timer runs from here. */
  startedAtMs: number;
  phase: CallPhase;
  muted: boolean;
  held: boolean;
  remoteLevel: number;
  error: string | null;
  /** Live transcript lines in arrival order (P3-4). */
  transcript: TranscriptLine[];
  /** Trigger word ranges underlined in the transcript (Phase 4 sets them). */
  highlights: HighlightRange[];
}

export interface StartOptions {
  waitSecs?: number | null;
}

const JOIN_FAILED = "Could not connect the call audio. Check your microphone, or end the call.";

/**
 * One accepted call: shows the accept summary at once (QH-7), joins the Daily room
 * with the agent token (QH-6), and drives mute, hold, and leave (AP-4).
 * Hold is local in v1: it mutes the agent mic and does not change the queue status.
 * The bot's live transcript lines arrive as RTVI server messages (P3-4).
 */
export function useCall(createMedia: CreateMedia) {
  const [call, setCall] = useState<ActiveCall | null>(null);
  const media = useRef<CallMedia | null>(null);
  /** Warmed while the offer rings (prepare), adopted by start, or discarded. */
  const warm = useRef<CallMedia | null>(null);
  const levels = useRef(new Map<string, number>());

  const patch = useCallback((p: Partial<ActiveCall>) => {
    setCall((c) => (c ? { ...c, ...p } : c));
  }, []);

  /**
   * A media session whose events only count while it is the current call's media.
   * Created early (prepare) or at accept (start).
   */
  const makeMedia = useCallback((): CallMedia => {
    const m: CallMedia = createMedia({
      onRemoteLevel(level, id) {
        if (media.current !== m) return;
        levels.current.set(id, level);
        const loudest = Math.max(0, ...levels.current.values());
        setCall((c) => (c && c.remoteLevel !== loudest ? { ...c, remoteLevel: loudest } : c));
      },
      onParticipantLeft(id) {
        if (media.current !== m) return;
        levels.current.delete(id);
      },
      onServerMessage(data) {
        if (media.current !== m) return;
        const msg = parseTranscriptMessage(data);
        if (!msg) return;
        setCall((c) => {
          if (!c) return c;
          const transcript = applyTranscript(c.transcript, msg);
          return transcript === c.transcript ? c : { ...c, transcript };
        });
      },
      onError(message) {
        if (media.current !== m) return;
        patch({ error: message || JOIN_FAILED });
      },
    });
    return m;
  }, [createMedia, patch]);

  /** While ringing: load the call SDK and open the mic, so accept only has to connect. */
  const prepare = useCallback(() => {
    if (warm.current || media.current) return;
    const m = makeMedia();
    warm.current = m;
    m.prepare?.().catch(() => {
      // join() retries everything; a failed warm up only costs time.
    });
  }, [makeMedia]);

  /** The offer went away without an accept: release the warmed session. */
  const discard = useCallback(() => {
    const m = warm.current;
    warm.current = null;
    if (m) void m.leave().catch(() => {});
  }, []);

  const start = useCallback(
    (res: AcceptResult, opts: StartOptions = {}) => {
      levels.current = new Map();
      setCall({
        sessionId: res.session_id,
        caller: callerInfo(res),
        memberId: res.member_id,
        intake: res.summary ?? {},
        waitSecs: opts.waitSecs ?? null,
        details: null,
        detailsState: "loading",
        startedAtMs: Date.now(),
        phase: "connecting",
        muted: false,
        held: false,
        remoteLevel: 0,
        error: null,
        transcript: [],
        highlights: [],
      });
      const m = warm.current ?? makeMedia();
      warm.current = null;
      media.current = m;
      m.join(res.room_url, res.token).then(
        () => {
          if (media.current === m) patch({ phase: "live" });
        },
        () => {
          if (media.current === m) patch({ error: JOIN_FAILED });
        },
      );
    },
    [makeMedia, patch],
  );

  /** Caller history arrived (or failed) for a call; ignored if that call is gone. */
  const setDetails = useCallback((sessionId: string, details: CallerDetails | null) => {
    setCall((c) =>
      c && c.sessionId === sessionId
        ? { ...c, details, detailsState: details ? "ready" : "error" }
        : c,
    );
  }, []);

  /** Leave the room and drop the call. Safe to call more than once. */
  const leave = useCallback(async () => {
    const m = media.current;
    media.current = null;
    setCall(null);
    if (m) {
      try {
        await m.leave();
      } catch {
        // Already gone; nothing to clean up.
      }
    }
  }, []);

  const toggleMute = useCallback(() => {
    if (!call || call.held) return;
    media.current?.setMic(call.muted);
    patch({ muted: !call.muted });
  }, [call, patch]);

  const toggleHold = useCallback(() => {
    if (!call) return;
    const held = !call.held;
    media.current?.setMic(!held && !call.muted);
    patch({ held });
  }, [call, patch]);

  /** Underline trigger words: replaces the current set (empty clears). */
  const highlight = useCallback((ranges: HighlightRange[]) => patch({ highlights: ranges }), [patch]);

  // Leave the room if the component goes away mid call (sign out, reload).
  useEffect(
    () => () => {
      void media.current?.leave();
      media.current = null;
      void warm.current?.leave();
      warm.current = null;
    },
    [],
  );

  return { call, start, prepare, discard, setDetails, leave, toggleMute, toggleHold, highlight };
}
