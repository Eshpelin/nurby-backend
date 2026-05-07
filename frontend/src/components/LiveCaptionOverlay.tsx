"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";

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
 *
 * On mount, hydrates from REST so a page reload that lands inside an
 * active speech window still shows the last caption immediately.
 *
 * Reconnects with capped exponential backoff. Hold time stretches with
 * caption length so long sentences don't cut off mid-read.
 */
export function LiveCaptionOverlay({
  cameraId,
  position = "bottom",
  holdMs = 6000,
}: Props) {
  const { token } = useAuth();
  const [caption, setCaption] = useState<Caption | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // REST hydrate. Pull the most recent transcript so a fresh mount
  // (e.g. after reload) shows the caption that was on screen before
  // the WS connection re-establishes.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(
          `/api/transcripts?camera_id=${cameraId}&limit=1`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!resp.ok) return;
        const rows = await resp.json();
        if (cancelled) return;
        const last = Array.isArray(rows) ? rows[0] : null;
        if (!last) return;
        const text = (last.text || "").trim();
        if (!text) return;
        // Only seed if the last transcript is recent enough to still be
        // worth showing. Stale captions just confuse the user.
        const ageMs = Date.now() - new Date(last.started_at).getTime();
        if (ageMs > holdMs) return;
        setCaption({ text, provider: last.provider, ts: Date.now() });
        if (hideTimer.current) clearTimeout(hideTimer.current);
        const remaining = computeHold(text, holdMs) - ageMs;
        if (remaining > 0) {
          hideTimer.current = setTimeout(() => setCaption(null), remaining);
        }
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cameraId, token, holdMs]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const scheduleReconnect = () => {
      if (cancelled) return;
      // Capped exponential backoff. 1s, 2s, 4s, ... up to 30s.
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
        };
        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type !== "transcript_created") return;
            if (msg.camera_id !== cameraId) return;
            const text = (msg.text || "").trim();
            if (!text) return;
            setCaption({ text, provider: msg.provider, ts: Date.now() });
            if (hideTimer.current) clearTimeout(hideTimer.current);
            hideTimer.current = setTimeout(
              () => setCaption(null),
              computeHold(text, holdMs)
            );
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
      role="status"
      aria-live="polite"
      aria-atomic="true"
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
        aria-hidden="true"
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

// Stretch hold time for long captions so the user can finish reading.
// Roughly 60ms per character on top of the floor, capped at 14s.
function computeHold(text: string, floor: number): number {
  const stretched = floor + Math.min(8000, text.length * 60);
  return Math.min(14000, Math.max(floor, stretched));
}
