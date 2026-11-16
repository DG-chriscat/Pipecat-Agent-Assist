import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { QueueSocket, SocketStatus } from "../api/queueClient";
import type { ClientCommand, ServerMessage } from "../api/types";
import { applyMessage, emptyQueueState, type QueueState } from "../state/queueState";

/**
 * Opens one QueueSocket for the lifetime of the component, keeps a QueueState from
 * its snapshot and events, and calls onMessage for every frame (after the state
 * update is queued). A new connection resets `synced` until its snapshot arrives.
 */
export function useQueueSocket(
  createSocket: () => QueueSocket,
  onMessage?: (msg: ServerMessage) => void,
) {
  const [state, dispatch] = useReducer(
    (s: QueueState, a: ServerMessage | { type: "unsync" }) =>
      a.type === "unsync" ? { ...s, synced: false } : applyMessage(s, a),
    emptyQueueState,
  );
  const [status, setStatus] = useState<SocketStatus>("idle");
  const socketRef = useRef<QueueSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    const socket = createSocket();
    socketRef.current = socket;
    const offStatus = socket.onStatus((s) => {
      setStatus(s);
      if (s !== "open") dispatch({ type: "unsync" });
    });
    const offMessage = socket.onMessage((msg) => {
      dispatch(msg);
      onMessageRef.current?.(msg);
    });
    socket.connect();
    return () => {
      offStatus();
      offMessage();
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [createSocket]);

  const send = useCallback((cmd: ClientCommand) => socketRef.current?.send(cmd) ?? false, []);

  return { state, status, send };
}
