"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  cameraId: string;
}

interface VLMState {
  status: "idle" | "processing" | "slow" | "stalled" | string;
  avg_latency?: number;
  last_latency?: number;
}

/**
 * Per-tile VLM status pill. Subscribes to /ws and listens for
 * vlm_status events. Renders nothing while idle so empty tiles stay
 * clean. Shows a spinning dot while processing and an amber warning
 * once the VLM crosses the slow threshold.
 *
 * The ping animation comes from Tailwind's animate-ping plus a
 * static dot underneath, matching the AudioActiveDot pattern so the
 * two indicators feel cohesive.
 */
export function VLMStatusBadge({ cameraId }: Props) {
  const [state, setState] = useState<VLMState | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    let cancelled = false;
    let reconnect: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const scheduleReconnect = () => {
      if (cancelled) return;
      attempt = Math.min(attempt + 1, 6);
      reconnect = setTimeout(connect, Math.min(30000, 1000 * 2 ** (attempt - 1)));
    };

    const connect = () => {
      if (cancelled) return;
      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => {
          attempt = 0;
        };
        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type !== "vlm_status") return;
            if (msg.camera_id !== cameraId) return;
            const v = msg.vlm || {};
            setState(v);
            // If status is processing/slow/stalled, hold visible until
            // the next event flips us back to idle. If we somehow miss
            // the idle event (worker shutdown), auto-clear after 30s
            // so the badge does not stick forever.
            if (idleTimer.current) clearTimeout(idleTimer.current);
            if (v.status && v.status !== "idle") {
              idleTimer.current = setTimeout(
                () => setState((s) => (s ? { ...s, status: "idle" } : s)),
                30000
              );
            }
          } catch {
            /* ignore */
          }
        };
        ws.onclose = () => scheduleReconnect();
        ws.onerror = () => ws.close();
      } catch {
        scheduleReconnect();
      }
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnect) clearTimeout(reconnect);
      if (idleTimer.current) clearTimeout(idleTimer.current);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, [cameraId]);

  if (!state || state.status === "idle") return null;

  const isWarn = state.status === "slow" || state.status === "stalled";
  const colorDot = isWarn ? "bg-amber-400" : "bg-violet-400";
  const colorBorder = isWarn ? "border-amber-400/50" : "border-violet-400/50";
  const colorText = isWarn ? "text-amber-300" : "text-violet-300";
  const label =
    state.status === "stalled"
      ? "VLM stalled"
      : state.status === "slow"
        ? `VLM slow (${state.avg_latency?.toFixed(1)}s)`
        : "Thinking";

  return (
    <div
      role="status"
      aria-label={label}
      title={
        state.last_latency
          ? `${label} · last ${state.last_latency.toFixed(1)}s`
          : label
      }
      className={`flex items-center gap-1 rounded-full bg-black/60 backdrop-blur-sm px-1.5 py-0.5 border ${colorBorder}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        <span
          className={`absolute inline-flex h-full w-full animate-ping rounded-full ${colorDot} opacity-60`}
        />
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${colorDot}`} />
      </span>
      <span className={`text-[10px] uppercase tracking-wider ${colorText}`}>
        {label}
      </span>
    </div>
  );
}
