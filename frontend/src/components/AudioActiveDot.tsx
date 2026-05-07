"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  cameraId: string;
  // Hold-time per pulse before fading out.
  holdMs?: number;
}

/**
 * Subscribes to /ws and pulses an emerald dot whenever a vad_pulse
 * (or transcript_created) event lands for this camera. Drives the
 * "audio active" visual on a camera tile without needing an STT
 * round-trip.
 */
export function AudioActiveDot({ cameraId, holdMs = 1500 }: Props) {
  const [active, setActive] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
      const delay = Math.min(30000, 1000 * 2 ** (attempt - 1));
      reconnect = setTimeout(connect, delay);
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
            if (msg.type !== "vad_pulse" && msg.type !== "transcript_created") return;
            if (msg.camera_id !== cameraId) return;
            setActive(true);
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => setActive(false), holdMs);
          } catch {
            /* ignore */
          }
        };
        ws.onclose = () => {
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
      if (reconnect) clearTimeout(reconnect);
      if (timer.current) clearTimeout(timer.current);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, [cameraId, holdMs]);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={active ? "Audio active on camera" : "Audio idle"}
      title={active ? "Audio active" : "Audio idle"}
      className={`flex items-center gap-1 rounded-full bg-black/60 backdrop-blur-sm px-1.5 py-0.5 border ${
        active ? "border-emerald-400/60" : "border-white/10"
      }`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full transition-colors ${
          active ? "bg-emerald-400 animate-pulse" : "bg-white/30"
        }`}
      />
      <svg
        width="9"
        height="9"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={active ? "text-emerald-400" : "text-white/40"}
      >
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      </svg>
    </div>
  );
}
