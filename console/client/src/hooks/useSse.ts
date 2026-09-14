import { useEffect, useRef, useState } from "react";

const EVENT_TYPES = [
  "session.started",
  "session.state_changed",
  "session.completed",
  "session.error",
  "session.interrupted",
  "session.waiting_input",
  "message.user",
  "message.agent",
  "message.delta",
  "tool.started",
  "tool.stdout",
  "tool.finished",
  "permission.requested",
  "permission.resolved",
  "artifact.created",
  "subagent.started",
  "subagent.progress",
  "subagent.updated",
  "subagent.finished",
  "subagent.delta",
  "subagent.tool.started",
  "subagent.tool.finished",
  "subagent.tool.progress",
  "mcp.progress",
];

export function useSse(url: string | null, onEvent: (evt: MessageEvent) => void) {
  const [connected, setConnected] = useState(false);
  const ref = useRef<EventSource | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url, { withCredentials: true });
    ref.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    const handler = (e: MessageEvent) => onEventRef.current(e);
    EVENT_TYPES.forEach((t) => es.addEventListener(t, handler as EventListenerOrEventListenerObject));
    return () => {
      es.close();
      setConnected(false);
    };
  }, [url]);

  return {
    connected,
    reconnect: () => {
      ref.current?.close();
      setConnected(false);
      // useEffect will re-run if url changes
    },
  };
}
