import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

export function useEventStream(
  enabled: boolean,
  onEvent?: (event: { kind: string; [k: string]: any }) => void
) {
  const [connected, setConnected] = useState(false);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") {
      setConnected(false);
      return;
    }

    const url = api.eventStreamUrl();
    const es = new EventSource(url, { withCredentials: true });

    const handleMsg = (ev: MessageEvent) => {
      try {
        const parsed = JSON.parse(ev.data);
        cbRef.current?.(parsed);
      } catch {
        // ignore malformed event
      }
    };

    const kinds = [
      "message",
      "job.queued",
      "job.started",
      "job.finished",
      "job.failed",
      "job.cancelled",
      "job.paused",
      "job.resumed",
      "project.created",
      "project.updated",
      "dataset.upserted",
      "dataset.ready",
      "model.promoted",
      "model.activated",
      "s2c.transmit",
      "s2c.complete",
      "s2c.loopback",
    ];

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    kinds.forEach((k) => es.addEventListener(k, handleMsg as EventListener));

    return () => {
      kinds.forEach((k) => es.removeEventListener(k, handleMsg as EventListener));
      es.close();
      setConnected(false);
    };
  }, [enabled]);

  return { connected };
}
