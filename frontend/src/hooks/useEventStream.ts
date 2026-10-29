import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/**
 * Server-Sent Events over `fetch`, not `EventSource`.
 *
 * `EventSource` cannot set request headers, which is why the stream URL used to
 * carry `?_tok=<token>`. A credential in a URL is copied into proxy access logs,
 * browser history and the Referer header of anything the page embeds, so that
 * was the weakest link in an otherwise header-only auth scheme.
 *
 * Reading the stream with `fetch` lets us send the token in a header like every
 * other request, and still works cross-origin (where `EventSource` depended on
 * third-party cookies being allowed). The parser below handles the subset of the
 * SSE wire format the server emits: `event:`/`data:` pairs and `:` comments.
 */
function parseSseChunk(
  buffer: string,
  onEvent: (data: string, kind: string) => void
): string {
  // events are separated by a blank line; keep any trailing partial event
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    let kind = "message";
    const data: string[] = [];
    for (const line of raw.split("\n")) {
      if (!line || line.startsWith(":")) continue; // comment / keep-alive
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") kind = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) onEvent(data.join("\n"), kind);
  }
  return buffer;
}

export function useEventStream(
  enabled: boolean,
  onEvent?: (event: { kind: string; [k: string]: any }) => void
) {
  const [connected, setConnected] = useState(false);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }

    const controller = new AbortController();
    let retryDelay = 3000;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const run = async () => {
      while (!stopped) {
        try {
          const res = await fetch(api.eventStreamUrl(), {
            credentials: "include",
            headers: { Accept: "text/event-stream", ...api.authHeaders() },
            signal: controller.signal,
          });
          if (!res.ok || !res.body) {
            throw new Error(`stream failed: HTTP ${res.status}`);
          }
          setConnected(true);
          retryDelay = 3000; // a successful connect resets the backoff
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!stopped) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // treat a lone \r\n\r\n separator too, and guard against unbounded
            // growth if the server ever stops sending blank lines
            buffer = parseSseChunk(buffer.replace(/\r\n/g, "\n"), (raw, kind) => {
              try {
                cbRef.current?.({ ...JSON.parse(raw), kind });
              } catch {
                // ignore malformed payload
              }
            });
            if (buffer.length > 1 << 20) buffer = "";
          }
        } catch (err) {
          if (stopped || controller.signal.aborted) return;
        }
        setConnected(false);
        if (stopped) return;
        // reconnect with backoff, capped so a dead server is not hammered
        await new Promise<void>((resolve) => {
          timer = setTimeout(resolve, retryDelay);
        });
        retryDelay = Math.min(retryDelay * 1.5, 30000);
      }
    };

    run();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      controller.abort();
      setConnected(false);
    };
  }, [enabled]);

  return { connected };
}
