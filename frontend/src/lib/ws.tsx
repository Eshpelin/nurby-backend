"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Shared /ws subscription. Mounted once at the dashboard root, fans
 * out events to subscribers via a small pub/sub. Replaces N tile-
 * scoped sockets with a single connection.
 *
 * Reconnect uses capped exponential backoff (1s, 2s, 4s, ..., 30s).
 * Subscribers register handlers per ``type``; ``"*"`` is the
 * wildcard. Camera filtering happens in the subscriber.
 */

export type WSMessage = {
  type: string;
  camera_id?: string;
  [key: string]: unknown;
};

type Handler = (msg: WSMessage) => void;

interface WSContextValue {
  connected: boolean;
  subscribe: (type: string, handler: Handler) => () => void;
}

const WSContext = createContext<WSContextValue | null>(null);

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Map<string, Set<Handler>>>(new Map());

  useEffect(() => {
    if (typeof window === "undefined") return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const scheduleReconnect = () => {
      if (cancelled) return;
      attempt = Math.min(attempt + 1, 6);
      const delay = Math.min(30000, 1000 * 2 ** (attempt - 1));
      reconnectTimer = setTimeout(connect, delay);
    };

    const connect = () => {
      if (cancelled) return;
      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => {
          attempt = 0;
          setConnected(true);
        };
        ws.onmessage = (evt) => {
          let msg: WSMessage | null = null;
          try {
            msg = JSON.parse(evt.data);
          } catch {
            return;
          }
          if (!msg || typeof msg.type !== "string") return;
          // Fan out to type-specific and wildcard subscribers.
          const direct = handlersRef.current.get(msg.type);
          const wild = handlersRef.current.get("*");
          if (direct) direct.forEach((h) => safe(h, msg!));
          if (wild) wild.forEach((h) => safe(h, msg!));
        };
        ws.onclose = () => {
          setConnected(false);
          scheduleReconnect();
        };
        ws.onerror = () => ws.close();
      } catch {
        scheduleReconnect();
      }
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const value = useMemo<WSContextValue>(
    () => ({
      connected,
      subscribe(type, handler) {
        const map = handlersRef.current;
        let bucket = map.get(type);
        if (!bucket) {
          bucket = new Set();
          map.set(type, bucket);
        }
        bucket.add(handler);
        return () => {
          bucket!.delete(handler);
          if (bucket!.size === 0) map.delete(type);
        };
      },
    }),
    [connected]
  );

  return <WSContext.Provider value={value}>{children}</WSContext.Provider>;
}

export function useWebSocket(): WSContextValue {
  const ctx = useContext(WSContext);
  if (!ctx) {
    // Provider not mounted. Return a no-op so a component can render
    // outside the dashboard without crashing.
    return {
      connected: false,
      subscribe: () => () => {},
    };
  }
  return ctx;
}

/**
 * Subscribe to one or more WS types and run ``handler`` for each
 * matching message. Optional ``cameraId`` filter pre-checks
 * ``msg.camera_id`` so subscribers don't repeat the boilerplate.
 */
export function useWSSubscribe(
  types: string | string[],
  handler: Handler,
  cameraId?: string
) {
  const ctx = useContext(WSContext);
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  useEffect(() => {
    if (!ctx) return;
    const list = Array.isArray(types) ? types : [types];
    const unsubs = list.map((t) =>
      ctx.subscribe(t, (msg) => {
        if (cameraId && msg.camera_id && msg.camera_id !== cameraId) return;
        handlerRef.current(msg);
      })
    );
    return () => {
      for (const u of unsubs) u();
    };
    // types may be a literal array; serialize for stable deps.
  }, [ctx, cameraId, Array.isArray(types) ? types.join("|") : types]); // eslint-disable-line react-hooks/exhaustive-deps
}

function safe(h: Handler, msg: WSMessage) {
  try {
    h(msg);
  } catch {
    /* swallow handler errors */
  }
}
