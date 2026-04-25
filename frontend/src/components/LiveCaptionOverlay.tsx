"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  cameraId: string;
  position?: "top" | "bottom";
  // Hold-time per caption before auto-hide.
  holdMs?: number;
}

interface Caption {
  text: string;
  provider?: string;
  ts: number;
}

/**
 * Subscribes to /ws and renders the most recent transcript_created
 * payload for this camera as a translucent caption strip on top of
 * the camera tile. Auto-hides after holdMs of silence.
 */
export function LiveCaptionOverlay({
  cameraId,
  position = "bottom",
  holdMs = 6000,
}: Props) {
  const [caption, setCaption] = useState<Caption | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type !== "transcript_created") return;
            if (msg.camera_id !== cameraId) return;
            const text = (msg.text || "").trim();
            if (!text) return;
            setCaption({ text, provider: msg.provider, ts: Date.now() });
            if (hideTimer.current) clearTimeout(hideTimer.current);
            hideTimer.current = setTimeout(() => setCaption(null), holdMs);
          } catch {
            /* ignore */
          }
        };
        ws.onclose = () => {
          if (cancelled) return;
          reconnectTimer = setTimeout(connect, 5000);
        };
        ws.onerror = () => ws.close();
      } catch {
        reconnectTimer = setTimeout(connect, 5000);
      }
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (hideTimer.current) clearTimeout(hideTimer.current);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, [cameraId, holdMs]);

  if (!caption) return null;

  const posClass =
    position === "top"
      ? "top-2 left-2 right-2"
      : "bottom-10 left-2 right-2";

  return (
    <div
      className={`absolute ${posClass} z-20 pointer-events-none flex items-start gap-2 rounded-md bg-black/70 px-2.5 py-1.5 backdrop-blur-sm border border-white/10 animate-[fadeIn_0.25s_ease-out]`}
    >
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-0.5 flex-shrink-0 text-emerald-400"
      >
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      </svg>
      <span className="text-[11px] leading-snug text-white/90 line-clamp-2 italic">
        {caption.text}
      </span>
    </div>
  );
}
