import { useCallback, useEffect, useRef, useState } from "react";
import type { AcceptResult } from "../api/types";
import type { CallMedia, CreateMedia } from "../call/media";
import { callerInfo, type CallerInfo } from "../state/offer";
import {
  applyTranscript,
  parseTranscriptMessage,
  type HighlightRange,
  type TranscriptLine,
} from "../state/transcript";
import type { CallPhase } from "../components/InCall";

export interface ActiveCall {
  sessionId: string;
  caller: CallerInfo;
  memberId: string | null;
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
  const levels = useRef(new Map<string, number>());

  const patch = useCallback((p: Partial<ActiveCall>) => {
    setCall((c) => (c ? { ...c, ...p } : c));
  }, []);

  const start = useCallback(
    (res: AcceptResult) => {
      levels.current = new Map();
      setCall({
        sessionId: res.session_id,
        caller: callerInfo(res),
        memberId: res.member_id,
        startedAtMs: Date.now(),
        phase: "connecting",
        muted: false,
        held: false,
        remoteLevel: 0,
        error: null,
        transcript: [],
        highlights: [],
      });
      const m = createMedia({
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
    [createMedia, patch],
  );

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
    },
    [],
  );

  return { call, start, leave, toggleMute, toggleHold, highlight };
}
